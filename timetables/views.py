"""5.3.6 시간표 추천 API view (#97).

- POST /timetables/recommend/  — 추천 (body: year/semester + prefs override)
- GET  /timetables/preference/ — 사용자 prefs 조회 (get_or_create)
- PUT  /timetables/preference/ — 갱신
"""
from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from .codes import Note
from .models import UserPreference
from .serializers import (
    TimetableRecommendRequestSerializer,
    TimetableResponseSerializer,
    UserPreferenceSerializer,
)
from .services.pipeline import recommend_timetables


class TimetableRecommendView(APIView):
    """POST /timetables/recommend/ — 시간표 조합 추천 top-3.

    Body (모두 옵셔널 except year/semester):
      year, semester   — 학기 (#97 결정 2 — 미지정 시 400 + MISSING_YEAR_SEMESTER)
      prefer_off_days, banned_days, no_morning, no_evening, lunch_break,
      max_credits, min_credits  — UserPreference field-level override (#97 결정 5)
    """

    @extend_schema(
        request=TimetableRecommendRequestSerializer,
        responses={200: TimetableResponseSerializer, 400: TimetableResponseSerializer},
    )
    def post(self, request):
        payload = request.data or {}
        year = payload.get('year')
        semester = payload.get('semester')
        if year is None or semester is None:
            return Response(
                {'plans': [], 'note': Note.MISSING_YEAR_SEMESTER},
                status=400,
            )

        # year/semester를 제외한 나머지 키만 prefs override 후보로 전달
        prefs_payload = {k: v for k, v in payload.items() if k not in ('year', 'semester')}
        result = recommend_timetables(
            request.user, int(year), int(semester), prefs_payload,
        )
        return Response(TimetableResponseSerializer(result).data)


class UserPreferenceView(generics.RetrieveUpdateAPIView):
    """GET/PUT /timetables/preference/ — 본인 prefs CRUD.

    GET — 없으면 default 값으로 row 생성해 반환 (#97 결정 5: get_or_create lazy).
    PUT/PATCH — 필드 갱신.
    """
    serializer_class = UserPreferenceSerializer

    def get_object(self):
        pref, _ = UserPreference.objects.get_or_create(user=self.request.user)
        return pref
