from rest_framework import serializers

from .models import FCMDevice, Notification


class NotificationSerializer(serializers.ModelSerializer):
    """알림 목록 + 읽음 처리 응답."""

    class Meta:
        model = Notification
        fields = [
            'id', 'title', 'message', 'notification_type',
            'related_id', 'is_read', 'is_pushed', 'created_at',
        ]
        read_only_fields = ['title', 'message', 'notification_type',
                            'related_id', 'is_pushed', 'created_at']


class FCMDeviceRegisterSerializer(serializers.Serializer):
    """FCM 디바이스 등록 입력 검증.

    ModelSerializer 안 씀 — `registration_token`의 unique 제약으로 인한
    UniqueValidator가 멱등 POST를 막아버리기 때문 (뷰에서 직접 get_or_create 처리).
    """
    registration_token = serializers.CharField()


class FCMDeviceSerializer(serializers.ModelSerializer):
    """FCM 디바이스 응답 (read-only)."""

    class Meta:
        model = FCMDevice
        fields = ['id', 'registration_token', 'is_active', 'created_at', 'updated_at']
        read_only_fields = fields
