from rest_framework import serializers

from .models import ChatAttachment, ChatMessage, ChatRoom


class ChatMessageCreateSerializer(serializers.Serializer):
    """메시지 전송 요청 body (spec 6.5 POST /rooms/<id>/messages/)."""
    content = serializers.CharField(min_length=1, max_length=4000)


class ChatAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatAttachment
        fields = ['id', 'file', 'file_type', 'original_name', 'created_at']
        read_only_fields = fields


class ChatMessageSerializer(serializers.ModelSerializer):
    attachments = ChatAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = ChatMessage
        fields = ['id', 'role', 'content', 'created_at', 'attachments']
        read_only_fields = fields


class ChatRoomListSerializer(serializers.ModelSerializer):
    """채팅방 목록·생성·삭제 응답 (spec 6.5).

    메시지는 포함하지 않아 N+1 회피 + 페이로드 경량화.
    """
    class Meta:
        model = ChatRoom
        fields = [
            'id', 'title', 'category', 'last_message_preview',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ChatRoomDetailSerializer(ChatRoomListSerializer):
    """채팅방 상세 응답 — 메시지 nested 포함 (spec 5.2).

    PR 1 범위에서는 메시지가 항상 빈 배열. PR 2에서 페이지네이션 도입 예정.
    """
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta(ChatRoomListSerializer.Meta):
        fields = ChatRoomListSerializer.Meta.fields + ['messages']
        read_only_fields = fields
