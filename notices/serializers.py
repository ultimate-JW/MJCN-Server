from rest_framework import serializers

from .crawlers.base import _DEPT_PREFIX_RE
from .models import Notice, NoticeAIResult


class NoticeAIResultSerializer(serializers.ModelSerializer):
    """공지 상세 응답에 포함되는 AI 처리 결과."""

    class Meta:
        model = NoticeAIResult
        fields = ['notice_type', 'summary', 'cards', 'status']


class _NoticeBaseSerializerMixin:
    """List/Detail 공용 — department_display + title_without_dept 가공 (spec 6.7).

    - department_display: department 비어있으면 source_label로 fallback (항상 값 보장)
    - title_without_dept: title에서 [부서명] 접두사 제거된 깔끔한 버전
                         (department 비어있거나 접두사 없으면 원본 title 그대로)
    """

    def get_department_display(self, obj):
        return obj.department or obj.get_source_display()

    def get_title_without_dept(self, obj):
        if obj.department and obj.title:
            # 첫 [...] 만 제거 후 앞 공백 trim. base.py의 추출 정규식과 동일 패턴.
            return _DEPT_PREFIX_RE.sub('', obj.title, count=1).lstrip()
        return obj.title


class NoticeListSerializer(_NoticeBaseSerializerMixin, serializers.ModelSerializer):
    """목록 응답용 — 본문 제외, AI 요약 1줄만 포함."""

    source_label = serializers.CharField(source='get_source_display', read_only=True)
    summary = serializers.SerializerMethodField()
    department_display = serializers.SerializerMethodField()
    title_without_dept = serializers.SerializerMethodField()
    # spec 5.10 — 관심사 매칭 점수. view에서 instance에 attribute로 부여.
    match_score = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Notice
        fields = [
            'id', 'source', 'source_label',
            'department', 'department_display',
            'title', 'title_without_dept', 'summary',
            'published_at', 'end_date', 'url', 'tags',
            'match_score',
        ]

    def get_summary(self, obj):
        ai_result = getattr(obj, 'ai_result', None)
        return ai_result.summary if ai_result else ''


class NoticeDetailSerializer(_NoticeBaseSerializerMixin, serializers.ModelSerializer):
    """상세 응답 — 본문 + AI 카드 포함."""

    source_label = serializers.CharField(source='get_source_display', read_only=True)
    department_display = serializers.SerializerMethodField()
    title_without_dept = serializers.SerializerMethodField()
    ai_result = NoticeAIResultSerializer(read_only=True)

    class Meta:
        model = Notice
        fields = [
            'id', 'source', 'source_label',
            'department', 'department_display',
            'title', 'title_without_dept',
            'content', 'extracted_content',
            'image_urls', 'url', 'published_at', 'end_date',
            'tags', 'ai_result',
        ]
