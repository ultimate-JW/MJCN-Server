from collections import defaultdict

from django.db.models import Q
from rest_framework import status
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

        # 자유선택 overflow 자동 합산 (graduation_requirements.md §6) — 다른 카테고리 required 초과분이 자유선택 채움.
        # 예) 전공 70 required인데 71학점 들으면 초과 1학점이 자유선택으로 자동 인정.
        overflow = 0
        for cat in ['전공필수', '전공선택', '공통교양', '핵심교양', '학문기초교양', '일반교양']:
            cat_required = sum(r.required_credits for r in requirements.filter(category=cat))
            cat_done = completed_by_category.get(cat, 0)
            if cat_done > cat_required:
                overflow += cat_done - cat_required
        completed_by_category['자유선택'] = completed_by_category.get('자유선택', 0) + overflow

        total_required = 0
        total_completed = 0
        categories = []

        # 학칙 7분류 순회 (#47 Phase 3). 핵심교양은 GR이 4영역으로 분해돼 있어 sum 필요.
        for cat in ['전공필수', '공통교양', '핵심교양', '학문기초교양', '전공선택', '일반교양', '자유선택']:
            cat_reqs = requirements.filter(category=cat)
            required = sum(r.required_credits for r in cat_reqs)
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

        # 졸업 총학점 — GR 어디서든 total_required 가져옴 (모든 row 동일값)
        first_req = requirements.first()
        graduation_total = first_req.total_required if first_req else 0

        # 채플 이수 회수 (graduation_requirements.md §2.1)
        # 학번별 required: 1996~1998 = 2회 / 1999학번 이후 = 4회 / 학번 미입력 시 4회 default
        if user.admission_year and 1996 <= user.admission_year <= 1998:
            chapel_required = 2
        else:
            chapel_required = 4
        chapel_completed = user.chapel_count or 0
        chapel = {
            'completed': chapel_completed,
            'required': chapel_required,
            'remaining': max(0, chapel_required - chapel_completed),
        }

        grand_total_completed = total_completed
        data = {
            'categories': categories,
            'chapel': chapel,
            'total_completed': grand_total_completed,
            'total_required': graduation_total,
            'total_remaining': max(0, graduation_total - grand_total_completed),
        }
        serializer = CompletionStatusSerializer(data)
        return Response(serializer.data)


# 졸업까지 진척도 (spec 5.3.5)
# 다음학기 추천
class NextSemesterRecommendView(APIView):
    """
    GET /api/v1/courses/recommend/next/ - 다음학기 수강과목 추천 (spec 5.3.1, #36)

    spec 5.3.1 규칙 기반 점수 알고리즘으로 정렬된 단일 리스트 응답.
    Hard Filter(이수/수강 중 + 학기 Offering 매칭)는 services에서 처리,
    Soft Constraint는 점수에 반영.

    쿼리 파라미터 (선택):
      year     — 추천 대상 연도 (예: 2026)
      semester — 추천 대상 학기 (1/2/3/4)
      미지정 시 사용자의 현재 학기 기반 자동 결정 (services 참조).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        target_year, target_semester = self._parse_term(request)
        if isinstance(target_year, Response):
            return target_year  # 검증 에러 응답 그대로 반환

        # services 레이어에서 점수 계산 + 정렬까지 마친 (score, Course) 튜플 리스트
        results = recommend_next_semester_courses(
            request.user,
            target_year=target_year,
            target_semester=target_semester,
        )

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

    @staticmethod
    def _parse_term(request):
        """?year=&semester= 파싱. 미지정은 (None, None) — services에서 자동 결정.

        잘못된 형식은 (400 Response, None) 반환. semester는 1/2/3/4만 허용.
        """
        year_raw = request.query_params.get('year')
        sem_raw = request.query_params.get('semester')
        try:
            year = int(year_raw) if year_raw not in (None, '') else None
            sem = int(sem_raw) if sem_raw not in (None, '') else None
        except ValueError:
            return Response(
                {'detail': 'year/semester는 정수여야 합니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            ), None
        if sem is not None and sem not in (1, 2, 3, 4):
            return Response(
                {'detail': 'semester는 1/2/3/4 중 하나여야 합니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            ), None
        return year, sem


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
        "note": "insufficient_data"   # fallback 발생 시에만 포함 (머신 코드, LLM이 사용자 표현 결정)
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
        # Fallback — spec은 최소 2안이지만, 데이터 부족 시 복제 X.
        # note는 머신 코드로 — 사용자 표현은 LLM/프론트가 결정 (#25)
        if len(plans) < 2:
            response['note'] = 'insufficient_data'
        return Response(response)
