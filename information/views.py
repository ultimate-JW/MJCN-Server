import json

from django.db.models import F, Q
from django.utils import timezone
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response

from common.matching import extract_user_keywords, sort_by_match

from .models import Information
from .serializers import InformationDetailSerializer, InformationListSerializer


# end_date secondary sort key — 빠른 순, NULL은 마지막 (timestamp 최대값으로)
_FAR_FUTURE = 9_999_999_999


def _end_date_key(item):
    if item.end_date is None:
        return _FAR_FUTURE
    # date → days since epoch (작을수록 위)
    return item.end_date.toordinal()


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
                # SQLite는 JSONField __contains 미지원 → 직렬화된 JSON 문자열에서
                # 따옴표로 감싼 값을 찾는 방식으로 우회.
                # Django는 한글을 ASCII escape("공모전")로 저장하므로
                # needle도 동일하게 ensure_ascii=True 로 직렬화.
                cat_q = Q()
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

        if view_mode == 'personalized':
            user_keywords = extract_user_keywords(request.user)
            items = list(queryset)
            # 동점 시 end_date 빠른 순 (D-day 임박 우선), NULL은 마지막
            sorted_items = sort_by_match(
                items, user_keywords, tags_attr='categories',
                secondary_key=_end_date_key,
            )
        else:
            sorted_items = list(queryset)
            for item in sorted_items:
                item.match_score = 0

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
