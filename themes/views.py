from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView, RetrieveAPIView

from .models import Theme
from .serializers import ThemeDetailSerializer, ThemeListSerializer


@extend_schema(
    parameters=[
        OpenApiParameter(
            'category', OpenApiTypes.STR, OpenApiParameter.QUERY,
            description=(
                '카테고리 필터 (생략 시 전체). '
                'course_registration / career / exchange / grant / academic.'
            ),
            enum=[c for c, _ in Theme.CATEGORY_CHOICES],
        ),
    ]
)
class ThemeListView(ListAPIView):
    """GET /api/v1/themes/ — 활성 테마 목록 (spec §6.12).

    쿼리 파라미터:
      - category: 단일 카테고리 필터 (생략 시 전체)

    정렬: order ↑, 생성일 ↓ (Theme.Meta.ordering)
    페이지네이션: 공용 StandardPagination 자동 적용.
    """
    serializer_class = ThemeListSerializer

    def get_queryset(self):
        qs = Theme.objects.filter(is_active=True)
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        return qs


class ThemeDetailView(RetrieveAPIView):
    """GET /api/v1/themes/<id>/ — 테마 상세 + 항목 (spec §6.12).

    응답에 `items` 배열 nest됨 (ThemeItem.order 오름차순).
    비활성(is_active=False) 테마는 404.
    """
    serializer_class = ThemeDetailSerializer
    queryset = Theme.objects.prefetch_related('items').filter(is_active=True)
