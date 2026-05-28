from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from notices.ai.client import AIClientError, AIResponseParseError

from .models import ChatAttachment, ChatMessage, ChatRoom
from .serializers import (
    ChatMessageCreateSerializer,
    ChatMessageSerializer,
    ChatRoomDetailSerializer,
    ChatRoomListSerializer,
    classify_attachment,
)
from .services import classify_and_title, generate_assistant_reply


_id_path_param = OpenApiParameter(
    'id', OpenApiTypes.INT, OpenApiParameter.PATH,
    description='채팅방 ID',
)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                'category', OpenApiTypes.STR, OpenApiParameter.QUERY,
                description='카테고리 폴더 필터 (spec 4.2 CHAT_CATEGORIES 중 하나)',
                required=False,
            ),
        ],
    ),
    retrieve=extend_schema(parameters=[_id_path_param]),
    destroy=extend_schema(parameters=[_id_path_param]),
)
class ChatRoomViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """AI 채팅방 CRUD + 메시지 전송 (spec 6.5).

    update 동작은 spec 외라 노출하지 않음 (ModelViewSet 대신 mixin 조합).
    본인 소유 채팅방만 조회·삭제 가능 — 다른 user 채팅방 접근은 404로 응답
    (enumeration 방어).
    """

    def get_queryset(self):
        qs = ChatRoom.objects.filter(user=self.request.user)
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        if self.action == 'retrieve':
            # 메시지·첨부 N+1 회피 (#139). list/destroy/create/send_message는 미적용.
            qs = qs.prefetch_related(
                Prefetch(
                    'messages',
                    queryset=ChatMessage.objects
                    .order_by('created_at')
                    .prefetch_related('attachments'),
                )
            )
        return qs

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ChatRoomDetailSerializer
        if self.action == 'send_message':
            return ChatMessageCreateSerializer
        return ChatRoomListSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        parameters=[_id_path_param],
        request=ChatMessageCreateSerializer,
        responses={
            201: ChatMessageSerializer,
            400: None,
            404: None,
            503: None,
        },
        description=(
            '메시지 전송 + AI 응답 (spec 5.2.3 / 5.2.4). JSON 또는 multipart/form-data 모두 지원. '
            'multipart의 경우 `attachments` 필드로 여러 파일 동시 업로드 가능 '
            '(jpg/png/mp4/pdf/docx, 10MB/파일). user 메시지·첨부파일은 짧은 트랜잭션 1로 '
            '저장하고, AI 호출(분류·응답)은 트랜잭션 밖에서 실행, assistant 메시지는 '
            '트랜잭션 2로 저장한다 (#141). 첫 메시지인 경우 ChatRoom.title·category를 AI '
            '분류로 채우며, 분류 실패 시 title=빈/category=기타 fallback 후 응답 생성까지 '
            '계속 진행. AI 응답 실패 시 503 응답 — 단 user 메시지·첨부는 살아남는다.'
        ),
    )
    @action(
        detail=True,
        methods=['post'],
        url_path='messages',
        parser_classes=[JSONParser, MultiPartParser],
    )
    def send_message(self, request, pk=None):
        serializer = ChatMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data['content']
        attachments = serializer.validated_data.get('attachments', [])

        try:
            assistant_msg = self._handle_send(pk, content, attachments)
        except (AIClientError, AIResponseParseError):
            return Response(
                {'detail': 'AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해주세요.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            ChatMessageSerializer(assistant_msg).data,
            status=status.HTTP_201_CREATED,
        )

    def _handle_send(self, room_pk, content: str, attachments: list) -> ChatMessage:
        """AI 호출을 트랜잭션 밖으로 분리해 락 시간 단축 + 사용자 메시지 손실 방지 (#141).

        흐름:
          1. 트랜잭션 1 (_save_user_message): room 락 + user_msg + 첨부 저장 — 수 ms
          2. (트랜잭션 밖) 첫 메시지면 classify_and_title — 실패 시 title=''/category='기타' fallback
          3. (트랜잭션 밖) 컨텍스트 조회 + generate_assistant_reply — 실패 시 raise → view 503
          4. 트랜잭션 2 (_save_assistant_message): assistant_msg + room 메타 갱신 — 수 ms

        AI 응답 실패 시 user_msg·첨부는 살아남아 사용자가 재전송할 때 컨텍스트 일관성 유지.
        """
        room_id, _, is_first_message = self._save_user_message(
            room_pk, content, attachments,
        )

        title, category = None, None
        if is_first_message:
            try:
                title, category = classify_and_title(content)
            except (AIClientError, AIResponseParseError):
                # 분류 실패 — fallback 후 응답 생성까지 계속 진행 (user_msg 손실 방지)
                title, category = '', '기타'

        context_size = settings.OPENAI_CHAT_CONTEXT_MESSAGES
        recent = list(
            ChatMessage.objects.filter(room_id=room_id)
            .order_by('-created_at')[:context_size]
        )
        recent.reverse()  # 호출 시점에 시간 순으로

        # 응답 실패 시 AIClientError/AIResponseParseError raise → view가 503 처리
        assistant_text = generate_assistant_reply(self.request.user, recent)

        return self._save_assistant_message(room_id, assistant_text, title, category)

    def _save_user_message(self, room_pk, content: str, attachments: list):
        """트랜잭션 1: room 락 + user_msg + 첨부 저장. 락 시간 = INSERT 시간만."""
        with transaction.atomic():
            room = (
                ChatRoom.objects
                .select_for_update()
                .filter(user=self.request.user, pk=room_pk)
                .first()
            )
            if room is None:
                # enumeration 방어: 본인 채팅방이 아니거나 존재 X 모두 동일 404
                raise Http404()

            is_first_message = not room.messages.exists()

            user_msg = ChatMessage.objects.create(
                room=room,
                role=ChatMessage.ROLE_USER,
                content=content,
            )
            for uploaded in attachments:
                ChatAttachment.objects.create(
                    message=user_msg,
                    file=uploaded,
                    file_type=classify_attachment(uploaded.name),
                    original_name=uploaded.name,
                )
        return room.id, user_msg, is_first_message

    def _save_assistant_message(self, room_id, assistant_text: str, title, category):
        """트랜잭션 2: assistant_msg 저장 + room 메타 갱신. 락 시간 = INSERT/UPDATE 시간만.

        title이 None이면 첫 메시지가 아니라 title/category 미갱신.
        title이 '' 이면 분류 실패 fallback이라 room.title은 유지 (category만 갱신).
        """
        with transaction.atomic():
            room = (
                ChatRoom.objects
                .select_for_update()
                .filter(pk=room_id)
                .first()
            )
            assistant_msg = ChatMessage.objects.create(
                room=room,
                role=ChatMessage.ROLE_ASSISTANT,
                content=assistant_text,
            )
            room.last_message_preview = assistant_text[:200]
            update_fields = ['last_message_preview', 'updated_at']
            if title is not None:
                if title:
                    room.title = title
                    update_fields.append('title')
                room.category = category
                update_fields.append('category')
            room.save(update_fields=update_fields)
        return assistant_msg
