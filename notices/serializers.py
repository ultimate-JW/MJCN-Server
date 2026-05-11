from rest_framework import serializers

from .models import Notice, NoticeAIResult


class NoticeAIResultSerializer(serializers.ModelSerializer):
    """공지 상세 응답에 포함되는 AI 처리 결과."""

    class Meta:
        model = NoticeAIResult
        fields = ['notice_type', 'summary', 'cards', 'status']


class NoticeListSerializer(serializers.ModelSerializer):
    """목록 응답용 — 본문 제외, AI 요약 1줄만 포함."""

    source_label = serializers.CharField(source='get_source_display', read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Notice
        fields = [
            'id', 'source', 'source_label', 'title',
            'summary', 'published_at', 'end_date', 'url', 'tags',
        ]

    def get_summary(self, obj):
        ai_result = getattr(obj, 'ai_result', None)
        return ai_result.summary if ai_result else ''


class NoticeDetailSerializer(serializers.ModelSerializer):
    """상세 응답 — 본문 + AI 카드 포함."""

    source_label = serializers.CharField(source='get_source_display', read_only=True)
    ai_result = NoticeAIResultSerializer(read_only=True)

    class Meta:
        model = Notice
        fields = [
            'id', 'source', 'source_label', 'title',
            'content', 'extracted_content',
            'image_urls', 'url', 'published_at', 'end_date',
            'tags', 'ai_result',
        ]
