"""대시보드 API (spec 6.10)."""
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .schemas import DashboardResponseSerializer
from .services import build_dashboard


class DashboardView(APIView):
    """GET /api/v1/dashboard/ — 메인화면 집계 데이터 (spec 5.8 / 6.10)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: DashboardResponseSerializer})
    def get(self, request):
        return Response(build_dashboard(request.user))
