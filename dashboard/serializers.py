"""대시보드 응답 직렬화 (spec 6.10)."""
from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from information.serializers import InformationListSerializer


class DashboardInformationSerializer(InformationListSerializer):
    """정보 목록 항목 + D-day (마감까지 남은 일수, spec 5.8).

    InformationListSerializer를 그대로 상속하고 `d_day` 한 필드만 추가한다.
    대시보드는 미마감 항목만 노출하므로 d_day는 0 이상 (또는 마감일 없으면 null).
    """

    d_day = serializers.SerializerMethodField()

    class Meta(InformationListSerializer.Meta):
        fields = InformationListSerializer.Meta.fields + ['d_day']

    def get_d_day(self, obj) -> int | None:
        if obj.end_date is None:
            return None
        return (obj.end_date - timezone.localdate()).days
