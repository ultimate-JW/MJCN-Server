from rest_framework import serializers

from .models import Notice, NoticeAIResult


class NoticeAIResultSerializer(serializers.ModelSerializer):
    """공지 상세 응답에 포함되는 AI 처리 결과."""

    class Meta:
        model = NoticeAIResult
        fields = ['notice_type', 'summary', 'cards', 'status']


class _NoticeBaseSerializerMixin:
    """List/Detail 공용 — department_display fallback 로직 (spec 6.7).

    department가 비어있으면 source_label로 자동 대체. 항상 값이 있어
    프론트는 분기 없이 메타 라인 노출 가능.
    """

    def get_department_display(self, obj):
        return obj.department or obj.get_source_display()


class NoticeListSerializer(_NoticeBaseSerializerMixin, serializers.ModelSerializer):
    """목록 응답용 — 본문 제외, AI 요약 1줄만 포함."""

    source_label = serializers.CharField(source='get_source_display', read_only=True)
    summary = serializers.SerializerMethodField()
    department_display = serializers.SerializerMethodField()

    class Meta:
        model = Notice
        fields = [
            'id', 'source', 'source_label',
            'department', 'department_display',
            'title', 'summary',
            'published_at', 'end_date', 'url', 'tags',
        ]

    def get_summary(self, obj):
        ai_result = getattr(obj, 'ai_result', None)
        return ai_result.summary if ai_result else ''


class NoticeDetailSerializer(_NoticeBaseSerializerMixin, serializers.ModelSerializer):
    """상세 응답 — 본문 + AI 카드 포함."""

    source_label = serializers.CharField(source='get_source_display', read_only=True)
    department_display = serializers.SerializerMethodField()
    ai_result = NoticeAIResultSerializer(read_only=True)

    class Meta:
        model = Notice
        fields = [
            'id', 'source', 'source_label',
            'department', 'department_display',
            'title', 'content', 'extracted_content',
            'image_urls', 'url', 'published_at', 'end_date',
            'tags', 'ai_result',
        ]
