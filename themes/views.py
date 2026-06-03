from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response

from .college_life_guide import build_college_life_guide
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

    예외: career 카테고리(theme id=2)는 정적 items 대신 **학년별 대학생활 가이드**로
    title/description/items를 동적 치환 (시연용, 2026-06-03). themes 앱은 본래 user를
    안 보지만 이 카테고리만 request.user.grade로 분기 — 모델·마이그레이션·스키마 변경 없음.
    """
    serializer_class = ThemeDetailSerializer
    queryset = Theme.objects.prefetch_related('items').filter(is_active=True)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        data = self.get_serializer(instance).data
        # career 테마만 학년별 대학생활 가이드로 응답 치환 (다른 테마는 정적 그대로)
        if instance.category == Theme.CATEGORY_CAREER:
            guide = build_college_life_guide(getattr(request.user, 'grade', None))
            data['title'] = guide['title']
            data['description'] = guide['description']
            data['items'] = guide['items']
        return Response(data)
