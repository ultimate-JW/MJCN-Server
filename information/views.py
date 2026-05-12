import json

from django.db.models import F, Q
from django.utils import timezone
from rest_framework.generics import ListAPIView, RetrieveAPIView

from .models import Information
from .serializers import InformationDetailSerializer, InformationListSerializer


class InformationListView(ListAPIView):
    """GET /api/v1/information/ — 정보 목록.

    쿼리 파라미터:
      - q: 제목 / 설명 / 주최자 부분 일치 검색
      - category: 카테고리 필터 (콤마 구분, OR 매칭. 예: ?category=공모전,대외활동)
      - include_expired: 'true' 이면 마감 지난 항목도 포함 (기본: 미포함)

    기본 노출 조건 (include_expired 미사용 시):
      - is_active=True
      - end_date IS NULL 또는 end_date >= 오늘
    정렬: end_date 빠른 순 (D-day 임박 우선), end_date 없는 항목 후순위
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

        # end_date 빠른 순, NULL은 마지막
        return qs.order_by(F('end_date').asc(nulls_last=True), '-created_at')


class InformationDetailView(RetrieveAPIView):
    """GET /api/v1/information/<id>/ — 정보 상세."""

    serializer_class = InformationDetailSerializer
    queryset = Information.objects.all()
