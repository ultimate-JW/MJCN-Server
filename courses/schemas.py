"""drf-spectacular용 응답 스키마 (Swagger UI 명시 전용, spec §6.6)."""
from rest_framework import serializers

from .serializers import CurriculumPlanSerializer


class CurriculumRecommendResponseSerializer(serializers.Serializer):
    """전체 커리큘럼 추천 응답 — plans 리스트 + 선택 note 머신코드."""
    plans = CurriculumPlanSerializer(many=True)
    note = serializers.CharField(required=False, help_text='fallback 발생 시 "insufficient_data" (선택)')
