from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Theme, ThemeItem


class ThemeItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ThemeItem
        fields = ['id', 'title', 'content', 'external_url', 'item_type', 'order']


class ThemeQuickQuestionSerializer(serializers.Serializer):
    """질문칩 1개 — 탭 시 prompt를 챗 엔드포인트로 전송 (#164/#171 패턴)."""
    label = serializers.CharField()
    prompt = serializers.CharField()


class ThemeListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Theme
        fields = ['id', 'title', 'category', 'description', 'order']


class ThemeDetailSerializer(serializers.ModelSerializer):
    items = ThemeItemSerializer(many=True, read_only=True)
    quick_questions = serializers.SerializerMethodField()

    class Meta:
        model = Theme
        fields = ['id', 'title', 'category', 'description', 'order',
                  'created_at', 'items', 'quick_questions']

    @extend_schema_field(ThemeQuickQuestionSerializer(many=True))
    def get_quick_questions(self, obj):
        # 기본 빈 배열 — career(대학생활 가이드) 테마는 ThemeDetailView가 학년별 칩으로 덮어씀.
        return []
