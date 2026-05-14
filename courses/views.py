from collections import defaultdict
from datetime import date

from django.db.models import Q
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AcademicCalendar, Course, CoursePrerequisite, GraduationRequirement
from .serializers import (
    CompletionStatusSerializer,
    CourseListSerializer,
    CurriculumPlanSerializer,
    NextSemesterRecommendationSerializer,
)


def _resolve_graduation_date(user):
    """
    사용자의 졸업 희망 (연/월)을 기준으로 졸업일을 결정한다.

    졸업 시기 구분 (graduation_month는 2 또는 8만 유효 — 프론트에서 강제):
      - 동계졸업 (graduation_month=2): 직전 학년도 2학기 종료 시점
      - 하계졸업 (graduation_month=8): 당해 학년도 1학기 종료 시점

    우선순위:
      1) 해당 학기의 AcademicCalendar.semester_end 가 등록되어 있으면 그 값 사용
      2) 없으면 임시 규칙: 동계졸업 → 2/10, 하계졸업 → 8/20

    AcademicCalendar 조회 키:
      - 동계졸업 → year=graduation_year-1, semester=2
      - 하계졸업 → year=graduation_year, semester=1

    반환: (graduation_date, is_estimated)
      - is_estimated=False → AcademicCalendar에 등록된 실제 일정 사용
      - is_estimated=True  → 폴백 규칙 사용
      - 사용자가 졸업 희망 연/월을 설정 안 했거나, graduation_month가 2/8이
        아닌 잘못된 값이면 (None, None)

    NOTE: 현재 학교측 공식 학사일정 API 제공이 거절되어 AcademicCalendar는
    관리자(Django admin)가 수동 등록하는 형태로 운영된다. 향후 학교 API가
    열리면 별도 sync 작업이 이 테이블을 채워주는 식으로 확장 예정 —
    그 시점에도 본 헬퍼의 로직은 변경 불필요.
    """
    if not user.graduation_year or user.graduation_month not in (2, 8):
        return None, None

    if user.graduation_month == 2:
        # 동계졸업 — 학사일정은 graduation_year - 1 년의 2학기
        cal_year = user.graduation_year - 1
        cal_semester = 2
        fallback = date(user.graduation_year, 2, 10)
    else:
        # 하계졸업 — 학사일정은 graduation_year 년의 1학기
        cal_year = user.graduation_year
        cal_semester = 1
        fallback = date(user.graduation_year, 8, 20)

    cal = AcademicCalendar.objects.filter(year=cal_year, semester=cal_semester).first()
    if cal and cal.semester_end:
        return cal.semester_end, False
    return fallback, True


class CourseSearchView(APIView):
    """GET /api/v1/courses/ - 과목 검색"""
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

        # 졸업일 계산 (spec 5.3.3)
        graduation_date, is_estimated = _resolve_graduation_date(user)

        data = {
            'categories': categories,
            'total_completed': grand_total_completed,
            'total_required': graduation_total,
            'total_remaining': max(0, graduation_total - grand_total_completed),
            'graduation_date': graduation_date,
            'is_estimated': is_estimated,
        }
        serializer = CompletionStatusSerializer(data)
        return Response(serializer.data)


# 다음학기 추천
class NextSemesterRecommendView(APIView):
    """GET /api/v1/courses/recommend/next/ - 다음학기 수강과목 추천"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        from accounts.models import CourseHistory, CurrentCourse

        completed_names = set(
            CourseHistory.objects.filter(user=user).values_list('course_name', flat=True)
        )
        current_names = set(
            CurrentCourse.objects.filter(user=user).values_list('course_name', flat=True)
        )
        taken_names = completed_names | current_names

        next_year, next_semester = self._next_semester(user.semester)

        # 해당 전공 + 교양 과목 중 미이수 과목
        candidates = Course.objects.filter(
            Q(major=user.major) | Q(category__in=['교양필수', '교양선택']),
            year_open=next_year,
            semester_open=next_semester,
        ).prefetch_related('schedules').exclude(name__in=taken_names)

        # 선수과목 미이수 과목 제외
        filtered = []
        for course in candidates:
            prereq_names = set(
                CoursePrerequisite.objects.filter(course=course)
                .values_list('prerequisite__name', flat=True)
            )
            if prereq_names.issubset(completed_names):
                filtered.append(course)

        # 카테고리별 분류
        category_map = {
            '전공필수': 'major_required',
            '전공선택': 'major_elective',
            '교양필수': 'liberal_required',
            '교양선택': 'liberal_elective',
        }
        result = {v: [] for v in category_map.values()}

        for course in filtered:
            key = category_map.get(course.category)
            if key:
                result[key].append(_serialize_course(course))

        serializer = NextSemesterRecommendationSerializer(result)
        return Response(serializer.data)

    def _next_semester(self, current_semester):
        current_year = date.today().year
        if current_semester in (1, 2):
            return current_year, 2
        return current_year + 1, 1


# 졸업까지 전체 커리큘럼 추천
class CurriculumRecommendView(APIView):
    """GET /api/v1/courses/recommend/curriculum/ - 전체 커리큘럼 추천"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        from accounts.models import CourseHistory, CurrentCourse

        completed_names = set(
            CourseHistory.objects.filter(user=user).values_list('course_name', flat=True)
        )
        current_names = set(
            CurrentCourse.objects.filter(user=user).values_list('course_name', flat=True)
        )
        taken_names = completed_names | current_names

        # 카테고리별 이수학점
        completed_credits = defaultdict(int)
        for h in CourseHistory.objects.filter(user=user):
            completed_credits[h.category] += h.credits

        # 졸업요건
        requirements = GraduationRequirement.objects.filter(
            department=user.major,
            admission_year=user.admission_year,
        )
        remaining_by_cat = {}
        for req in requirements:
            done = completed_credits.get(req.category, 0)
            remaining_by_cat[req.category] = max(0, req.required_credits - done)

        # 미이수 과목 풀
        available = list(
            Course.objects.filter(
                Q(major=user.major) | Q(category__in=['교양필수', '교양선택']),
            ).exclude(name__in=taken_names).prefetch_related('schedules')
        )

        remaining_semesters = self._calc_remaining_semesters(user)

        plans = self._generate_plans(
            available, remaining_by_cat, remaining_semesters,
            completed_names, taken_names, user,
        )

        serializer = CurriculumPlanSerializer(plans, many=True)
        return Response(serializer.data)

    def _calc_remaining_semesters(self, user):
        now = date.today()
        current_year = now.year
        current_sem = 1 if 3 <= now.month <= 8 else 2

        if user.graduation_year and user.graduation_month:
            grad_sem = 1 if user.graduation_month <= 8 else 2
            semesters = (user.graduation_year - current_year) * 2 + (grad_sem - current_sem)
            return max(1, semesters)
        return max(1, (4 - user.grade) * 2 + (2 if user.semester <= 2 else 1))

    def _generate_plans(self, available, remaining_by_cat, remaining_semesters, completed_names, taken_names, user):
        courses_by_cat = defaultdict(list)
        for course in available:
            courses_by_cat[course.category].append(course)

        priority = ['전공필수', '교양필수', '전공선택', '교양선택']
        credit_targets = [18, 21, 15]
        plans = []

        for plan_idx, target in enumerate(credit_targets):
            semesters = []
            used_names = set()
            year, sem = self._first_semester(user)
            local_remaining = dict(remaining_by_cat)

            for _ in range(remaining_semesters):
                semester_courses = []
                semester_credits = 0

                for cat in priority:
                    if local_remaining.get(cat, 0) <= 0:
                        continue
                    for course in courses_by_cat.get(cat, []):
                        if course.name in taken_names or course.name in used_names:
                            continue
                        if semester_credits + course.credits > target:
                            continue
                        prereq_names = set(
                            CoursePrerequisite.objects.filter(course=course)
                            .values_list('prerequisite__name', flat=True)
                        )
                        if not prereq_names.issubset(completed_names | used_names):
                            continue

                        semester_courses.append(_serialize_course(course))
                        semester_credits += course.credits
                        used_names.add(course.name)
                        local_remaining[cat] = local_remaining.get(cat, 0) - course.credits

                if semester_courses:
                    semesters.append({
                        'year': year,
                        'semester': sem,
                        'courses': semester_courses,
                    })

                if sem == 1:
                    sem = 2
                else:
                    sem = 1
                    year += 1

            if semesters:
                plans.append({
                    'plan_number': plan_idx + 1,
                    'semesters': semesters,
                })

        if len(plans) < 2:
            plans = plans * 2
        return plans[:5]

    def _first_semester(self, user):
        now = date.today()
        if user.semester in (1, 2):
            return now.year, 2
        return now.year + 1, 1


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
