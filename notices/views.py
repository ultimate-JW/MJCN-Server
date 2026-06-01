from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response

from common.matching import extract_user_keywords, score_match, sort_by_match

from .models import Notice
from .serializers import NoticeDetailSerializer, NoticeListSerializer


def _published_key(notice):
    """매칭 동점 시 2차 정렬 키 — 최신순(작을수록 위). published_at 없으면 최하위."""
    if notice.published_at is None:
        return float('inf')
    return -notice.published_at.timestamp()


@extend_schema(
    parameters=[
        OpenApiParameter(
            'q', OpenApiTypes.STR, OpenApiParameter.QUERY,
            description='제목/본문 부분 일치 검색 (spec 6.7)',
        ),
        OpenApiParameter(
            'source', OpenApiTypes.STR, OpenApiParameter.QUERY,
            description=(
                "출처 필터 (콤마 구분, 복수 가능). 예: 'academic,scholarship'. "
                "값: academic / general / event / scholarship / overseas / "
                "student_activity / career / contest / opentalk"
            ),
        ),
        OpenApiParameter(
            'view', OpenApiTypes.STR, OpenApiParameter.QUERY,
            description=(
                "'personalized' (기본, 관심사 매칭 정렬) 또는 'all' (최신순). "
                "spec 5.4.2 / 5.10."
            ),
            enum=['personalized', 'all'],
        ),
    ]
)
class NoticeListView(ListAPIView):
    """GET /api/v1/notices/ — 공지 목록.

    쿼리 파라미터:
      - q: 제목 / 본문 부분 일치 검색
      - source: 출처 필터 (콤마 구분, 복수 가능 예: ?source=academic,scholarship)
      - view: 'personalized' (기본) — 관심사 매칭 정렬 / 'all' — 최신순 (spec 5.4.2 / 5.10)

    페이지네이션: 공용 StandardPagination 자동 적용.
    응답에 `match_score` 필드 포함 (personalized 시 의미 있음, all 시 0).
    """

    serializer_class = NoticeListSerializer

    def get_queryset(self):
        qs = Notice.objects.select_related('ai_result').all()

        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q))

        source_param = self.request.query_params.get('source')
        if source_param:
            sources = [s.strip() for s in source_param.split(',') if s.strip()]
            if sources:
                qs = qs.filter(source__in=sources)

        return qs.order_by('-published_at')

    def list(self, request, *args, **kwargs):
        """view 파라미터에 따라 두 화면 분리 (spec 5.4.2).

        - 'personalized' (기본): match_score 내림차순 → 동점 시 최신순 정렬 (맞춤형)
        - 'all': published_at 최신순 (전체보기)

        match_score는 두 view 모두 실제 계산해 응답에 노출한다 (#162).
        """
        view_mode = request.query_params.get('view', 'personalized')
        queryset = self.filter_queryset(self.get_queryset())  # published_at 최신순
        user_keywords = extract_user_keywords(request.user)

        if view_mode == 'personalized':
            # match_score 내림차순 → 동점 시 최신순. 점수 0도 제외하지 않고 최하위 포함.
            sorted_items = sort_by_match(
                list(queryset), user_keywords,
                tags_attr='tags', secondary_key=_published_key,
            )
        else:
            # view=all — 최신순 그대로 유지, match_score는 정보로 계산해 노출
            sorted_items = list(queryset)
            for item in sorted_items:
                item.match_score = score_match(user_keywords, item.tags or [])

        page = self.paginate_queryset(sorted_items)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(sorted_items, many=True)
        return Response(serializer.data)


class NoticeDetailView(RetrieveAPIView):
    """GET /api/v1/notices/<id>/ — 공지 상세 (AI 카드 포함)."""

    serializer_class = NoticeDetailSerializer
    queryset = Notice.objects.select_related('ai_result').all()
