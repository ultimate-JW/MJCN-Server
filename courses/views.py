from collections import defaultdict

from django.db.models import Q
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Course, GraduationRequirement
from .serializers import (
    CompletionStatusSerializer,
    CourseListSerializer,
    CurriculumPlanSerializer,
    NextSemesterRecommendationSerializer,
)
from .services import (
    calc_graduation_progress,
    generate_curriculum_plans,
    recommend_next_semester_courses,
)


# 과목 검색
class CourseSearchView(ListAPIView):
    """GET /api/v1/courses/ - 과목 검색 (페이지네이션 적용)"""
    permission_classes = [IsAuthenticated]
    serializer_class = CourseListSerializer

    def get_queryset(self):
        # course_code는 유니크 PK 역할이라 페이지 간 순서 안정성 확보용 정렬 키
        queryset = Course.objects.prefetch_related('schedules', 'prerequisites').order_by('course_code')

        params = self.request.query_params
        q = params.get('q')
        college = params.get('college')
        department = params.get('department')
        major = params.get('major')
        category = params.get('category')
        credits = params.get('credits')
        year_open = params.get('year_open')
        semester_open = params.get('semester_open')

        if q:
            queryset = queryset.filter(
                Q(name__icontains=q) | Q(course_code__icontains=q)
            )
        if college:
            queryset = queryset.filter(college=college)
        if department:
            queryset = queryset.filter(department=department)
        if major:
            queryset = queryset.filter(major=major)
        if category:
            queryset = queryset.filter(category=category)
        if credits:
            queryset = queryset.filter(credits=int(credits))
        if year_open:
            queryset = queryset.filter(year_open=int(year_open))
        if semester_open:
            queryset = queryset.filter(semester_open=int(semester_open))

        return queryset


# 이수현황
class CompletionStatusView(APIView):
    """GET /api/v1/courses/status/ - 이수현황 분석"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        from accounts.models import CourseHistory, CurrentCourse

        # 카테고리별 이수학점 합산
        completed_by_category = defaultdict(int)
        for h in CourseHistory.objects.filter(user=user):
            completed_by_category[h.category] += h.credits

        # 현재 수강 중인 과목도 포함 (course_code로 Course 매칭)
        for cc in CurrentCourse.objects.filter(user=user):
            course = Course.objects.filter(course_code=cc.course_code).first()
            if course:
                completed_by_category[course.category] += course.credits

        # 졸업요건 조회
        requirements = GraduationRequirement.objects.filter(
            department=user.major,
            admission_year=user.admission_year,
        )

        total_required = 0
        total_completed = 0
        categories = []

        for cat in ['전공필수', '전공선택', '교양필수', '교양선택']:
            req = requirements.filter(category=cat).first()
            required = req.required_credits if req else 0
            completed = completed_by_category.get(cat, 0)
            remaining = max(0, required - completed)
            total_required += required
            total_completed += completed
            categories.append({
                'category': cat,
                'completed': completed,
                'required': required,
                'remaining': remaining,
            })

        # 일반선택: 총 졸업학점 - 위 카테고리 필요학점 합
        first_req = requirements.first()
        graduation_total = first_req.total_required if first_req else 0
        general_required = max(0, graduation_total - total_required)
        general_completed = completed_by_category.get('일반선택', 0)

        categories.append({
            'category': '일반선택',
            'completed': general_completed,
            'required': general_required,
            'remaining': max(0, general_required - general_completed),
        })

        grand_total_completed = total_completed + general_completed
        data = {
            'categories': categories,
            'total_completed': grand_total_completed,
            'total_required': graduation_total,
            'total_remaining': max(0, graduation_total - grand_total_completed),
        }
        serializer = CompletionStatusSerializer(data)
        return Response(serializer.data)


# 졸업까지 진척도 (spec 5.3.5)
class GraduationProgressView(APIView):
    """
    GET /api/v1/courses/graduation-progress/ - 졸업까지 진척도

    NOTE: spec 5.8에 따르면 최종적으로 dashboard 메인 응답에 통합될
    필드(`graduation_progress_percent`)이다. dashboard 앱이 만들어지면
    이 View는 제거하고 `services.calc_graduation_progress`를 dashboard
    응답 빌더에서 호출하면 된다.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            'graduation_progress_percent': calc_graduation_progress(request.user),
        })


# 다음학기 추천
class NextSemesterRecommendView(APIView):
    """
    GET /api/v1/courses/recommend/next/ - 다음학기 수강과목 추천 (spec 5.3.1)

    spec 5.3.1 규칙 기반 점수 알고리즘으로 정렬된 단일 리스트 응답.
    Hard Filter(이수/수강 중)는 services에서 처리, Soft Constraint는 점수에 반영.

    TODO(별도 이슈): 학교 PDF로 받은 "다음 학기 실제 개설 과목" 정보가 DB에
    들어오면 후보를 그 학기 개설분으로 한정하고 본 view에 학기 인자(쿼리 파라미터)
    노출. 지금은 시드/임시 데이터 기반이라 학기 적합성은 점수로만 반영.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # services 레이어에서 점수 계산 + 정렬까지 마친 (score, Course) 튜플 리스트
        results = recommend_next_semester_courses(request.user)

        # 응답 dict 변환 — Course 객체는 schedules가 prefetch된 상태
        items = [
            {
                'score': score,
                'course_code': course.course_code,
                'name': course.name,
                'category': course.category,
                'credits': course.credits,
                'professor': course.professor,
                'schedules': list(course.schedules.all()),
            }
            for score, course in results
        ]

        serializer = NextSemesterRecommendationSerializer(items, many=True)
        return Response(serializer.data)


# 카테고리 → 응답 키 매핑 (spec 5.3.2 4키 분리, #25)
#   일반선택은 4키 밖이라 추천 결과에서 제외됨 (이슈 #25 명시)
_CATEGORY_TO_KEY = {
    '전공필수': 'major_required',
    '전공선택': 'major_elective',
    '교양필수': 'liberal_required',
    '교양선택': 'liberal_elective',
}


def _serialize_course(course):
    return {
        'course_code': course.course_code,
        'name': course.name,
        'category': course.category,
        'credits': course.credits,
        'professor': course.professor,
        'schedules': [
            {
                'day_of_week': s.day_of_week,
                'start_time': s.start_time,
                'end_time': s.end_time,
                'building': s.building,
                'room': s.room,
            }
            for s in course.schedules.all()
        ],
    }


def _split_semester_by_category(semester):
    """학기 dict의 courses 리스트를 4 카테고리 키로 분리.

    빈 카테고리도 키 유지 (빈 배열) — 프론트가 키 존재 체크 안 해도 됨.
    매핑에 없는 카테고리(예: 일반선택)는 응답에서 누락.
    """
    buckets = {key: [] for key in _CATEGORY_TO_KEY.values()}
    for course in semester['courses']:
        key = _CATEGORY_TO_KEY.get(course.category)
        if key:
            buckets[key].append(_serialize_course(course))
    return {
        'year': semester['year'],
        'semester': semester['semester'],
        **buckets,
    }


# 졸업까지 전체 커리큘럼 추천
class CurriculumRecommendView(APIView):
    """POST /api/v1/courses/recommend/curriculum/ - 전체 커리큘럼 추천 (spec 5.3.2, #25)

    Body (모두 옵셔널, 없으면 합리적 기본값):
      - max_credits        : int, 학기당 학점 상한 베이스 (기본 18)
      - category_weights   : {카테고리: float}, 카테고리 가중치 배수 (기본 모두 1.0)
      - interest_weight    : float, 관심사 매칭 가중치 배수 (기본 1.0)
      - include_summer     : bool, 하계 계절학기(semester_open=3) 포함 (기본 false)
      - include_winter     : bool, 동계 계절학기(semester_open=4) 포함 (기본 false)
      - num_plans          : int, 변형 plan 개수 2~5 (기본 3)

    응답:
      {
        "plans": [
          {
            "plan_number": int, "max_credits": int,
            "semesters": [
              {
                "year": int, "semester": int,         # semester: 1/2/3/4
                "major_required": [...], "major_elective": [...],
                "liberal_required": [...], "liberal_elective": [...]
              }, ...
            ]
          }, ...
        ],
        "note": "..."   # 데이터 부족 등 fallback 발생 시에만 포함
      }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        body = request.data or {}

        plans = generate_curriculum_plans(
            request.user,
            max_credits=int(body.get('max_credits') or 18),
            category_weights=body.get('category_weights') or {},
            interest_weight=float(body.get('interest_weight') or 1.0),
            include_summer=bool(body.get('include_summer', False)),
            include_winter=bool(body.get('include_winter', False)),
            num_plans=int(body.get('num_plans') or 3),
        )

        # 학기별 4 카테고리 분리
        payload = [
            {
                'plan_number': p['plan_number'],
                'max_credits': p['max_credits'],
                'semesters': [_split_semester_by_category(s) for s in p['semesters']],
            }
            for p in plans
        ]
        serializer = CurriculumPlanSerializer(payload, many=True)

        response = {'plans': serializer.data}
        # Fallback — spec은 최소 2안이지만, 데이터 부족 시 복제 X. 호출자에게 명시
        if len(plans) < 2:
            response['note'] = '추천 가능한 과목 데이터가 부족하여 plan을 충분히 생성하지 못했습니다.'
        return Response(response)
