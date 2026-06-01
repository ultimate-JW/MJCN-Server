import json

from django.db import connection
from django.db.models import F, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from common.matching import extract_user_keywords, score_match, sort_by_match

from .models import Information
from .serializers import (
    ContestCardSerializer,
    InformationDetailSerializer,
    InformationListSerializer,
)
from .services import build_contest_guide


# end_date secondary sort key — 빠른 순, NULL은 마지막 (timestamp 최대값으로)
_FAR_FUTURE = 9_999_999_999


def _end_date_key(item):
    if item.end_date is None:
        return _FAR_FUTURE
    # date → days since epoch (작을수록 위)
    return item.end_date.toordinal()


@extend_schema(
    parameters=[
        OpenApiParameter(
            'q', OpenApiTypes.STR, OpenApiParameter.QUERY,
            description='제목/설명/주최자 부분 일치 검색 (spec 6.8)',
        ),
        OpenApiParameter(
            'category', OpenApiTypes.STR, OpenApiParameter.QUERY,
            description=(
                '카테고리 필터 (콤마 구분, OR 매칭). '
                "예: '공모전,대외활동'."
            ),
        ),
        OpenApiParameter(
            'include_expired', OpenApiTypes.BOOL, OpenApiParameter.QUERY,
            description="'true'면 마감 지난 항목도 포함 (기본: 미포함)",
        ),
        OpenApiParameter(
            'view', OpenApiTypes.STR, OpenApiParameter.QUERY,
            description=(
                "'personalized' (기본, 관심사 매칭 정렬) 또는 'all' (마감일 순). "
                "spec 5.5.2 / 5.10."
            ),
            enum=['personalized', 'all'],
        ),
    ]
)
class InformationListView(ListAPIView):
    """GET /api/v1/information/ — 정보 목록.

    쿼리 파라미터:
      - q: 제목 / 설명 / 주최자 부분 일치 검색
      - category: 카테고리 필터 (콤마 구분, OR 매칭. 예: ?category=공모전,대외활동)
      - include_expired: 'true' 이면 마감 지난 항목도 포함 (기본: 미포함)
      - view: 'personalized' (기본) — 관심사 매칭 정렬 / 'all' — 마감일 순 (spec 5.5.2 / 5.10)

    기본 노출 조건 (include_expired 미사용 시):
      - is_active=True
      - end_date IS NULL 또는 end_date >= 오늘
    정렬 (view='all'): end_date 빠른 순, NULL은 마지막
    정렬 (view='personalized'): match_score ↓ → end_date 빠른 순
    응답에 `match_score` 필드 포함 (personalized 시 의미 있음, all 시 0).
    """

    serializer_class = InformationListSerializer

    def get_queryset(self):
        qs = Information.objects.all()

        include_expired = self.request.query_params.get('include_expired', '').lower() == 'true'
        if not include_expired:
            today = timezone.localdate()
            qs = qs.filter(is_active=True).filter(
                Q(end_date__isnull=True) | Q(end_date__gte=today)
            )

        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(description__icontains=q)
                | Q(organizer__icontains=q)
            )

        category_param = self.request.query_params.get('category')
        if category_param:
            categories = [c.strip() for c in category_param.split(',') if c.strip()]
            if categories:
                cat_q = Q()
                if connection.vendor == 'postgresql':
                    # PG는 JSONB native __contains 지원 — 인덱스 활용 가능
                    for c in categories:
                        cat_q |= Q(categories__contains=[c])
                else:
                    # SQLite 폴백 (로컬·CI 전용, spec 8.5):
                    # JSONField __contains 미지원 → 직렬화 문자열 icontains.
                    # Django는 한글을 ASCII escape("공모전")로 저장하므로
                    # needle도 ensure_ascii=True 로 직렬화 (같은 표현 매칭).
                    for c in categories:
                        needle = json.dumps(c, ensure_ascii=True)
                        cat_q |= Q(categories__icontains=needle)
                qs = qs.filter(cat_q)

        # end_date 빠른 순, NULL은 마지막 (view=all 정렬)
        return qs.order_by(F('end_date').asc(nulls_last=True), '-created_at')

    def list(self, request, *args, **kwargs):
        """view 파라미터에 따라 분기 (spec 5.5.2)."""
        view_mode = request.query_params.get('view', 'personalized')
        queryset = self.filter_queryset(self.get_queryset())

        user_keywords = extract_user_keywords(request.user)
        if view_mode == 'personalized':
            # 매칭된 정보만(match_score>=1) 필터 → 점수 내림차순 → 동점 시 D-day 임박 순 (#174)
            scored = sort_by_match(
                list(queryset), user_keywords, tags_attr='categories',
                secondary_key=_end_date_key,
            )
            sorted_items = [it for it in scored if it.match_score >= 1]
        else:
            # view=all — 마감일 순 그대로 유지, match_score는 정보로 계산해 노출 (#162)
            sorted_items = list(queryset)
            for item in sorted_items:
                item.match_score = score_match(user_keywords, item.categories or [])

        page = self.paginate_queryset(sorted_items)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(sorted_items, many=True)
        return Response(serializer.data)


class InformationDetailView(RetrieveAPIView):
    """GET /api/v1/information/<id>/ — 정보 상세."""

    serializer_class = InformationDetailSerializer
    queryset = Information.objects.all()


@extend_schema(
    responses=OpenApiTypes.OBJECT,
    description=(
        '공모전 테마 상세 한 벌 (조언 + 카드 + 우선순위). spec 5.5 / 5.10.4. '
        '관심사 매칭(동의어 확장) + fallback backfill로 항상 최소 3~최대 5개 노출. '
        'state: matched(매칭 ≥3) / partial_match(1~2) / no_match(0).'
    ),
)
class ContestGuideView(APIView):
    """GET /api/v1/information/contest-guide/ — 공모전 추천 테마 상세 (이슈 #184).

    응답: { state, advice:{line1,line2}, cards:[{...,dday}], priority:[{rank,card_id,reasons}], note }
    추천 로직은 services.build_contest_guide. 카드만 직렬화하고 나머지는 머신 친화 dict 그대로.
    """

    def get(self, request, *args, **kwargs):
        guide = build_contest_guide(request.user)
        return Response({
            'state': guide['state'],
            'advice': guide['advice'],
            'cards': ContestCardSerializer(guide['cards'], many=True).data,
            'priority': guide['priority'],
            'note': guide['note'],
        })
