from django.conf import settings
from django.db import transaction
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from notices.ai.client import AIClientError, AIResponseParseError

from .models import ChatMessage, ChatRoom
from .serializers import (
    ChatMessageCreateSerializer,
    ChatMessageSerializer,
    ChatRoomDetailSerializer,
    ChatRoomListSerializer,
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
            '메시지 전송 + AI 응답 (spec 5.2.3). 트랜잭션 안에서 user 메시지를 저장하고, '
            '첫 메시지인 경우 ChatRoom.title·category를 AI 분류로 채운 뒤 컨텍스트 '
            'OPENAI_CHAT_CONTEXT_MESSAGES개를 모아 assistant 응답을 생성한다. '
            'AI 호출 실패 시 트랜잭션 롤백 후 503 응답 (user 메시지도 저장 X).'
        ),
    )
    @action(detail=True, methods=['post'], url_path='messages')
    def send_message(self, request, pk=None):
        serializer = ChatMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data['content']

        try:
            assistant_msg = self._handle_send(pk, content)
        except (AIClientError, AIResponseParseError):
            return Response(
                {'detail': 'AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해주세요.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            ChatMessageSerializer(assistant_msg).data,
            status=status.HTTP_201_CREATED,
        )

    def _handle_send(self, room_pk, content: str) -> ChatMessage:
        """user 메시지 저장 → (첫 메시지면) title/category 생성 → AI 응답 저장.

        한 트랜잭션 안에서 모두 처리. AI 호출은 트랜잭션 안에서 일어나며 실패 시
        롤백되어 부분 저장 상태를 남기지 않는다.
        """
        context_size = settings.OPENAI_CHAT_CONTEXT_MESSAGES
        with transaction.atomic():
            room = (
                ChatRoom.objects
                .select_for_update()
                .filter(user=self.request.user, pk=room_pk)
                .first()
            )
            if room is None:
                # enumeration 방어: 본인 채팅방이 아니거나 존재 X 모두 동일 404
                from django.http import Http404
                raise Http404()

            is_first_message = not room.messages.exists()

            ChatMessage.objects.create(
                room=room,
                role=ChatMessage.ROLE_USER,
                content=content,
            )

            if is_first_message:
                title, category = classify_and_title(content)
                if title:
                    room.title = title
                room.category = category

            # 컨텍스트 윈도우: 시간 순 최근 N개
            recent = list(
                room.messages.order_by('-created_at')[:context_size]
            )
            recent.reverse()  # 호출 시점에 시간 순으로

            assistant_text = generate_assistant_reply(recent)

            assistant_msg = ChatMessage.objects.create(
                room=room,
                role=ChatMessage.ROLE_ASSISTANT,
                content=assistant_text,
            )

            room.last_message_preview = assistant_text[:200]
            room.save(update_fields=[
                'title', 'category', 'last_message_preview', 'updated_at',
            ])
            return assistant_msg
