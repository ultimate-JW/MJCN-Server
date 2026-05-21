"""대시보드 API (spec 6.10)."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import build_dashboard


class DashboardView(APIView):
    """GET /api/v1/dashboard/ — 메인화면 집계 데이터 (spec 5.8 / 6.10)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_dashboard(request.user))
