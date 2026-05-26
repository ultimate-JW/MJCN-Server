from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets

from .models import ChatRoom
from .serializers import ChatRoomDetailSerializer, ChatRoomListSerializer


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
    """AI 채팅방 CRUD (spec 6.5).

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
        return ChatRoomListSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
