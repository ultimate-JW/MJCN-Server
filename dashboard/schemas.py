"""drf-spectacular용 응답 스키마 (Swagger UI 명시 전용, spec §6.10).

대시보드는 여러 앱 데이터를 합친 dict 응답이라 별도 응답 serializer로 형상 명시.
실제 API 동작은 `services.build_dashboard`가 dict 반환 — 본 serializer는 schema 전용.
"""
from rest_framework import serializers

from accounts.serializers import CurrentCourseSerializer
from notices.serializers import NoticeListSerializer

from .serializers import DashboardInformationSerializer


class DashboardGreetingSerializer(serializers.Serializer):
    """대시보드 인사 영역."""
    user_name = serializers.CharField()
    weekday = serializers.CharField(help_text='월/화/수/목/금/토/일')
    today_class_count = serializers.IntegerField()


class DashboardResponseSerializer(serializers.Serializer):
    """`GET /api/v1/dashboard/` 응답 (spec 6.10)."""
    greeting = DashboardGreetingSerializer()
    graduation_progress_percent = serializers.IntegerField(min_value=0, max_value=100)
    today_schedule = CurrentCourseSerializer(many=True)
    notices = NoticeListSerializer(many=True, help_text='관심사 매칭 공지 최대 3개')
    information = DashboardInformationSerializer(
        many=True, help_text='관심사 매칭 정보 최대 3개 (d_day 포함)'
    )
    unread_notification_count = serializers.IntegerField()
