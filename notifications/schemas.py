"""drf-spectacular용 응답 스키마 (Swagger UI 명시 전용, spec §6.9)."""
from rest_framework import serializers


class UnreadCountResponseSerializer(serializers.Serializer):
    """`GET /api/v1/notifications/unread-count/` 응답."""
    unread_count = serializers.IntegerField()


class ReadAllResponseSerializer(serializers.Serializer):
    """`POST /api/v1/notifications/read-all/` 응답 — 미읽음→읽음으로 갱신된 수."""
    updated = serializers.IntegerField()


class FCMDeviceErrorResponseSerializer(serializers.Serializer):
    """오류·안내 메시지 (DELETE /devices/ 404 / 400 등).

    공통 DetailResponseSerializer와 이름 충돌 방지를 위해 분리 — drf-spectacular는
    같은 클래스명을 다른 모듈에서 서로 다른 schema component로 인식해 경고함.
    """
    detail = serializers.CharField()
