"""
courses 앱 서비스 레이어.

View / dashboard / chat 등 여러 곳에서 재사용 가능한 도메인 로직을 모아둔
순수 함수 모음이다. 모델 조회는 하되 DRF Request / Response에 의존하지 않는다.

주요 함수:
  - _resolve_graduation_date(user)            : 졸업일 결정 (spec 5.3.4)
  - calc_graduation_progress(user)            : 졸업까지 진척도 % (spec 5.3.5)
  - calculate_recommendation_score(...)       : 다음학기 추천 점수 (spec 5.3.1)
  - recommend_next_semester_courses(user)     : 다음학기 추천 호출자 (spec 5.3.1)
"""

from datetime import date

from .models import (
    AcademicCalendar,
    Course,
    GraduationRequirement,
)


# ────────────────────────────────────────
# 5.3.4 졸업일 추정
# ────────────────────────────────────────

def _estimate_graduation_year_month(user):
    """
    사용자가 graduation_year / graduation_month를 직접 입력하지 않은 경우,
    grade / semester + 오늘 시즌(봄/가을) 정보를 기반으로 졸업희망 (연/월)을
    자동 추정한다. (spec 5.3.4)

    추정 규칙:
      - 잔여 학기 R = (4 - grade) * 2 + (2 - semester)   (음수면 0으로 보정)
      - 오늘 시즌: 3~8월 → 봄, 9~2월 → 가을
        (1~2월은 직전 calendar year의 가을학기로 본다)
      - R번 시즌 교차하여 마지막 학기 시즌 결정
        - 봄 → 가을 (같은 학년도)
        - 가을 → 봄 (다음 calendar year)
      - 마지막 학기가 봄 → 그 해 8월 하계졸업 → (year, 8)
      - 마지막 학기가 가을 → 다음 해 2월 동계졸업 → (year + 1, 2)

    엇학기 (예: 봄 시즌에 4-2 입력) 케이스는 사용자 입력 학기를
    "현재 듣고 있는 학기"로 우선 신뢰한다.

    반환: (graduation_year, graduation_month) 또는 (None, None)
      - grade 또는 semester가 None이면 추정 불가 → (None, None)
    """
    if user.grade is None or user.semester is None:
        return None, None

    R = max((4 - user.grade) * 2 + (2 - user.semester), 0)

    today = date.today()
    if 3 <= today.month <= 8:
        season = 'spring'
        year = today.year
    elif today.month >= 9:
        season = 'fall'
        year = today.year
    else:
        # 1~2월 — 직전 calendar year의 가을학기
        season = 'fall'
        year = today.year - 1

    for _ in range(R):
        if season == 'spring':
            season = 'fall'
            # year 그대로 (봄→가을은 같은 학년도)
        else:
            season = 'spring'
            year += 1  # 가을→봄은 다음 calendar year

    if season == 'spring':
        return year, 8           # 봄학기 종료 → 그 해 8월 하계
    return year + 1, 2           # 가을학기 종료 → 다음 해 2월 동계


def _resolve_graduation_date(user):
    """
    사용자의 졸업 희망 (연/월)을 기준으로 졸업일을 결정한다. (spec 5.3.4)

    우선순위:
      1. 사용자가 graduation_year / graduation_month 입력 (graduation_month ∈ {2, 8})
      2. 미입력 또는 잘못된 값이면 _estimate_graduation_year_month로 자동 추정
      3. 추정도 불가능하면 (None, None)

    졸업일 결정:
      - AcademicCalendar.semester_end 등록되어 있으면 그 값 (is_estimated=False)
      - 미등록 시 폴백 — 동계 2/10, 하계 8/20 (is_estimated=True)

    AcademicCalendar 조회 키:
      - 동계졸업(month=2) → year = graduation_year - 1, semester = 2
      - 하계졸업(month=8) → year = graduation_year, semester = 1

    추정값은 응답 시점 계산용으로만 사용하며 DB에 저장하지 않는다.

    반환: (graduation_date, is_estimated)
    """
    grad_year = user.graduation_year
    grad_month = user.graduation_month

    if not grad_year or grad_month not in (2, 8):
        grad_year, grad_month = _estimate_graduation_year_month(user)
        if grad_year is None:
            return None, None

    if grad_month == 2:
        cal_year = grad_year - 1
        cal_semester = 2
        fallback = date(grad_year, 2, 10)
    else:
        cal_year = grad_year
        cal_semester = 1
        fallback = date(grad_year, 8, 20)

    cal = AcademicCalendar.objects.filter(year=cal_year, semester=cal_semester).first()
    if cal and cal.semester_end:
        return cal.semester_end, False
    return fallback, True


# ────────────────────────────────────────
# 5.3.5 졸업까지 진척도
# ────────────────────────────────────────

def calc_graduation_progress(user):
    """
    졸업까지 진척도(%)를 계산한다. (spec 5.3.5)

    공식: (오늘 - 시작일) / (졸업일 - 시작일) * 100  (반올림 정수)
      - 시작일: admission_year-03-01 (1학년 1학기 시작일)
      - 졸업일: _resolve_graduation_date(user) 결과 사용 (자동 추정 포함)

    예외:
      - admission_year 미입력 또는 졸업일 결정 불가 → 0
      - 오늘 ≤ 시작일 → 0
      - 오늘 ≥ 졸업일 → 100
      - 시작일 == 졸업일 (이상 케이스) → 0

    반환: int (0 ~ 100)
    """
    # 입학연도 미입력 — 시작일을 정할 수 없으므로 진척도 계산 불가
    if not user.admission_year:
        return 0

    # 졸업일 결정 실패 (graduation_year/month 미입력 + 자동 추정도 불가) → 분모 못 만듦
    graduation_date, _ = _resolve_graduation_date(user)
    if graduation_date is None:
        return 0

    start_date = date(user.admission_year, 3, 1)  # 1학년 1학기 시작일 = 입학년도 3/1
    today = date.today()

    # 입학 전 (admission_year 미래 입력 등) — 아직 시작 안 함
    if today <= start_date:
        return 0
    
    # 이미 졸업일 지남 — 100%로 고정
    if today >= graduation_date:
        return 100

    total_days = (graduation_date - start_date).days
    # 시작일 == 졸업일 같은 이상 케이스 (0으로 나눔 방지)
    if total_days <= 0:
        return 0

    pct = round((today - start_date).days / total_days * 100)
    return max(0, min(100, pct))  # 부동소수점 반올림 보정 — 0~100 범위 강제


# ────────────────────────────────────────
# 5.3.1 다음학기 추천 — 점수 계산
# ────────────────────────────────────────

# 점수 가감산 상수 (CLAUDE.md 이슈 #24 참조, 튜닝 대상)
BONUS_INTEREST_MATCH = 20          # 사용자 관심사 카테고리 일치
BONUS_CATEGORY_SHORT = 15          # 졸업요건상 잔여학점이 남은 카테고리에 속한 과목이면 가산
BONUS_MAJOR_REQUIRED = 25          # 전공필수 가산
BONUS_LIBERAL_REQUIRED = 15        # 교양필수 가산 
BONUS_GRADE_SEMESTER_MATCH = 10    # 과목 권장 "학년/학기"가 사용자 현재 "학년/학기"와 정확히 일치
BONUS_BACKLOG_REQUIRED = 10        # 권장 학년이 지났는데 안 들은 "전공필수/교양필수" 
PENALTY_GRADE_EXCEEDED = 10        # 권장 학년이 사용자 학년보다 위 (상위 학년 과목)
PENALTY_PREREQUISITE_MISSING = 15  # 동일 학과 학생이 선수과목을 안 들음 (타과생은 영향 없음)

# 권장 학년이 지난 필수 과목 가산점 적용 대상 카테고리
BACKLOG_REQUIRED_CATEGORIES = ('전공필수', '교양필수')

# 정렬 2차 키 — 동점 시 카테고리 우선순위 (숫자낮을수록 상위)
CATEGORY_PRIORITY = {
    '전공필수': 1,
    '교양필수': 2,
    '전공선택': 3,
    '교양선택': 4,
    '일반선택': 5,
}


def calculate_recommendation_score(
    course,
    *,
    user_grade,
    user_semester,
    user_major,
    user_interest_categories,
    short_categories,
    completed_course_ids,
    course_prerequisite_ids,
    course_tags=None,
):
    """
    한 과목에 대한 다음학기 추천 점수를 계산한다. (spec 5.3.1)

    순수 함수 — DB 조회를 하지 않는다. 호출자는 사전에 다음을 빌드해 넘긴다:
      - short_categories: 졸업요건상 잔여학점이 남은 카테고리 set
      - completed_course_ids: 사용자가 이수 완료한 강의 ID set
      - course_prerequisite_ids: 해당 course의 선수과목 강의 ID set
      - course_tags: course의 관심사 태그 리스트. 모델에 Course.tags가 추가되기
        전까지는 인자로 직접 넘긴다. None이면 빈 리스트로 처리.

    인자:
      course: Course 인스턴스 (category / year_open / semester_open / major 사용)
      user_grade: 사용자 현재 학년 (1~4 또는 None)
      user_semester: 사용자 현재 학기 (1, 2 또는 None)
      user_major: 사용자 전공 문자열 (빈 문자열이면 동일 학과 판정 안 함)
      user_interest_categories: InterestArea.category 모음 (예: {"IT/개발"})

    반환: int

    가감 규칙:
      +20  관심사 매칭 (course_tags ∩ user_interest_categories 비어있지 않음)
      +15  졸업요건 잔여 카테고리에 속함
      +25  전공필수 카테고리
      +15  교양필수 카테고리
      +10  course.year_open == user_grade AND course.semester_open == user_semester
      +10  course.year_open < user_grade AND category ∈ {전공필수, 교양필수}
            (밀린 필수 과목 — 졸업 지연 방지용 우선 노출)
      -10  course.year_open > user_grade
      -15  user_major == course.major AND 선수과목 미이수
    """
    score = 100  # 기준점. 여기에 항목별 가감산

    # 관심사 매칭 가산
    tags = list(course_tags) if course_tags else []
    if tags and set(tags) & set(user_interest_categories or []):
        score += BONUS_INTEREST_MATCH

    # 졸업요건 부족 카테고리 가산
    if course.category in short_categories:
        score += BONUS_CATEGORY_SHORT

    # 전공필수 가산
    if course.category == '전공필수':
        score += BONUS_MAJOR_REQUIRED

    # 교양필수 가산
    if course.category == '교양필수':
        score += BONUS_LIBERAL_REQUIRED

    # 학년/학기 적합성
    if user_grade is not None and user_semester is not None:
        # 권장 학년/학기와 일치 가산
        if course.year_open == user_grade and course.semester_open == user_semester:
            score += BONUS_GRADE_SEMESTER_MATCH
        # 권장 학년이 사용자보다 위 감점
        if course.year_open > user_grade:
            score -= PENALTY_GRADE_EXCEEDED

    # 권장 학년이 지난 "전공필수/교양필수" 가산
    #    (졸업 지연 방지 목적. 일반선택/전공선택은 해당 없음)
    if user_grade is not None and course.year_open < user_grade:
        if course.category in BACKLOG_REQUIRED_CATEGORIES:
            score += BONUS_BACKLOG_REQUIRED

    # 선수과목 미이수 감점
    if user_major and course.major and course.major == user_major:
        if course_prerequisite_ids and not set(course_prerequisite_ids).issubset(completed_course_ids):
            score -= PENALTY_PREREQUISITE_MISSING

    return score


def _build_short_categories(user):
    """
    졸업요건상 잔여학점이 남은 카테고리 set을 빌드한다.

    department + admission_year 기준으로 GraduationRequirement를 조회하고,
    CourseHistory의 카테고리별 합산 학점과 비교하여 부족분이 있는 카테고리만
    set에 담는다. 점수 계산의 `short_categories` 인자로 그대로 사용된다.

    user.major 또는 admission_year 미입력 시 빈 set 반환 → 졸업요건 가산점 0점.
    """
    # 졸업요건 조회 불가 — 학과 또는 입학연도 미입력
    if not user.major or not user.admission_year:
        return set()

    requirements = GraduationRequirement.objects.filter(
        department=user.major,
        admission_year=user.admission_year,
    )

    # 사용자 이수이력의 카테고리별 학점 합산
    taken_credits_by_category = {}
    for history in user.course_histories.all():
        taken_credits_by_category[history.category] = (
            taken_credits_by_category.get(history.category, 0) + history.credits
        )

    # 카테고리별 필요학점 > 이수학점 인 경우만 short으로 분류
    return {
        req.category for req in requirements
        if taken_credits_by_category.get(req.category, 0) < req.required_credits
    }


def recommend_next_semester_courses(user):
    """
    한 사용자에 대해 다음학기 추천 과목 리스트를 반환한다. (spec 5.3.1)

    DRF Request/Response에 의존하지 않는 순수 도메인 함수. view 등 호출자가
    User 인스턴스를 넘기면 (점수, Course) 튜플 리스트를 정렬해 반환한다.

    처리 흐름:
      1. 사용자 데이터 조회 — 수강이력 / 현재수강 / 관심사
      2. Hard Filter — 이미 이수했거나 현재 수강 중인 course_code 제외
      3. 졸업요건 잔여학점 분석 → 부족 카테고리 set 빌드
      4. 후보 과목 각각에 calculate_recommendation_score 호출
      5. 정렬 — score DESC → CATEGORY_PRIORITY ASC → course_code ASC
      6. (score, Course) 튜플 리스트 반환

    반환: list[tuple[int, Course]] — 정렬된 추천 결과 (상위가 가장 추천 강함)
    """
    # 사용자 관심사 카테고리 집합 — InterestArea.category 값 모음
    user_interest_categories = set(
        user.interests.values_list('category', flat=True)
    )

    # 이수 / 현재 수강 중 course_code 집합 (Hard Filter용)
    taken_codes = set(
        user.course_histories.values_list('course_code', flat=True)
    )
    current_codes = set(
        user.current_courses.values_list('course_code', flat=True)
    )
    excluded_codes = taken_codes | current_codes

    # Hard Filter — 이미 이수했거나 수강 중인 과목은 후보에서 제외
    # prefetch_related로 선수과목 + 시간표 N+1 쿼리 방지 (view에서 schedules 직렬화)
    candidates = list(
        Course.objects.exclude(course_code__in=excluded_codes)
        .prefetch_related('prerequisites', 'schedules')
    )

    # 졸업요건 부족 카테고리 (점수 +15용)
    short_categories = _build_short_categories(user)

    # 이수 완료한 Course.id 집합 (선수과목 검사용)
    # CourseHistory는 course_code만 저장하므로 Course.id로 변환 필요
    completed_course_ids = set(
        Course.objects.filter(course_code__in=taken_codes).values_list('id', flat=True)
    )

    # 후보별 점수 계산
    scored = []
    for course in candidates:
        prereq_ids = {cp.prerequisite_id for cp in course.prerequisites.all()}
        score = calculate_recommendation_score(
            course,
            user_grade=user.grade,
            user_semester=user.semester,
            user_major=user.major,
            user_interest_categories=user_interest_categories,
            short_categories=short_categories,
            completed_course_ids=completed_course_ids,
            course_prerequisite_ids=prereq_ids,
            course_tags=course.tags,
        )
        scored.append((score, course))

    # 정렬 — score DESC → 카테고리 우선순위 ASC → course_code ASC
    scored.sort(key=lambda x: (
        -x[0],
        CATEGORY_PRIORITY.get(x[1].category, 99),
        x[1].course_code,
    ))
    return scored
