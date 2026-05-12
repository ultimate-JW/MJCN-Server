from django.db.models import Q
from rest_framework.generics import ListAPIView, RetrieveAPIView

from .models import Notice
from .serializers import NoticeDetailSerializer, NoticeListSerializer


class NoticeListView(ListAPIView):
    """GET /api/v1/notices/ — 공지 목록 (전체보기 기준).

    쿼리 파라미터:
      - q: 제목 / 본문 부분 일치 검색
      - source: 출처 필터 (콤마 구분, 복수 가능 예: ?source=academic,scholarship)
    페이지네이션: 공용 StandardPagination 자동 적용.
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


class NoticeDetailView(RetrieveAPIView):
    """GET /api/v1/notices/<id>/ — 공지 상세 (AI 카드 포함)."""

    serializer_class = NoticeDetailSerializer
    queryset = Notice.objects.select_related('ai_result').all()
