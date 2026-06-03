from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response

from common.matching import extract_user_keywords, score_match

from .models import Notice
from .serializers import NoticeDetailSerializer, NoticeListSerializer


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

        - 'personalized' (기본): match_score>=1 매칭된 공지만 필터, published_at 최신순
        - 'all': 전체, published_at 최신순

        두 view 모두 최신순 정렬. match_score는 실제 계산해 응답에 노출하되,
        personalized의 필터 기준으로만 쓰고 정렬 키로는 사용하지 않는다 (#196).
        """
        view_mode = request.query_params.get('view', 'personalized')
        queryset = self.filter_queryset(self.get_queryset())  # published_at 최신순
        user_keywords = extract_user_keywords(request.user)

        items = list(queryset)
        for item in items:
            item.match_score = score_match(user_keywords, item.tags or [])

        if view_mode == 'personalized':
            # 매칭된 공지만(match_score>=1) 노출, 정렬은 queryset의 최신순 유지 (#196).
            # 관심사 미설정 사용자는 빈 결과 (온보딩에서 관심사 설정 강제).
            items = [it for it in items if it.match_score >= 1]
        sorted_items = items

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
