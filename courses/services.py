"""
courses 앱 서비스 레이어.

View / dashboard / chat 등 여러 곳에서 재사용 가능한 도메인 로직을 모아둔
순수 함수 모음이다. 모델 조회는 하되 DRF Request / Response에 의존하지 않는다.

주요 함수:
  - _resolve_graduation_date(user)            : 졸업일 결정 (spec 5.3.4)
  - calc_graduation_progress(user)            : 졸업까지 진척도 % (spec 5.3.5)
  - calculate_recommendation_score(...)       : 다음학기 추천 점수 (spec 5.3.1)
  - recommend_next_semester_courses(user)     : 다음학기 추천 호출자 (spec 5.3.1)
  - generate_curriculum_plans(user, **knobs)  : 전체 커리큘럼 추천 (spec 5.3.2)
"""

from datetime import date

from django.db.models import Prefetch, Q

from .required_courses import MAJOR_DEPT_PREFIXES, MAJOR_REQUIRED_BY_MAJOR
from .models import (
    AcademicCalendar,
    Course,
    CourseOffering,
    CoursePrerequisite,
    GraduationRequirement,
)


def _foreign_major_versions_to_exclude(user_major):
    """학생 전공의 전공필수 8과목 이름과 같은 이름인데 다른 학과코드(타과·교양 버전)인 Course.course_code set.

    예) 컴공 학생 → 'C언어' 이름의 기컴101(교양)을 추천 후보에서 차단.
    전공 버전(컴정101)은 그대로 후보에 남음.
    학칙 §9 동일과목 정신 + UX (학생이 같은 이름 두 개 동시 노출 시 혼란 방지).
    """
    if not user_major or user_major not in MAJOR_REQUIRED_BY_MAJOR:
        return set()
    names = MAJOR_REQUIRED_BY_MAJOR[user_major]
    prefixes = MAJOR_DEPT_PREFIXES.get(user_major, ())
    if not prefixes:
        return set()
    prefix_q = Q()
    for p in prefixes:
        prefix_q |= Q(course_code__startswith=p)
    return set(
        Course.objects.filter(name__in=names).exclude(prefix_q).values_list('course_code', flat=True)
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
BONUS_DESIGNATED_REQUIRED = 15     # 학칙 의무 영역(공통/핵심/학문기초) 가산 (#47 Phase 3, 기존 BONUS_LIBERAL_REQUIRED 대체)
BONUS_GRADE_SEMESTER_MATCH = 10    # 과목 권장 "학년/학기"가 사용자 현재 "학년/학기"와 정확히 일치
BONUS_BACKLOG_REQUIRED = 10        # 권장 학년이 지났는데 안 들은 필수/지정 과목
PENALTY_GRADE_EXCEEDED = 10        # 권장 학년이 사용자 학년보다 위 (상위 학년 과목)
PENALTY_PREREQUISITE_MISSING = 15  # 동일 학과 학생이 선수과목을 안 들음 (타과생은 영향 없음)

# 학칙 의무 영역 — 학생이 지정된 과목을 들어야 하는 카테고리 (graduation_requirements.md §4.2~4.4)
# 일반교양/자유선택/전공선택은 "학점만 채우면 됨"이라 제외, 전공필수는 별도 +25 가산
DESIGNATED_CATEGORIES = {'공통교양', '핵심교양', '학문기초교양'}

# 권장 학년이 지난 필수/지정 과목 가산점 적용 대상 카테고리
BACKLOG_REQUIRED_CATEGORIES = ('전공필수',) + tuple(DESIGNATED_CATEGORIES)

# 정렬 2차 키 — 동점 시 카테고리 우선순위 (숫자낮을수록 상위)
CATEGORY_PRIORITY = {
    '전공필수': 1,
    '공통교양': 2,
    '핵심교양': 3,
    '학문기초교양': 4,
    '전공선택': 5,
    '일반교양': 6,
    '자유선택': 7,
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
      - short_categories: 졸업요건상 잔여학점이 남은 (category, liberal_subtype, core_area) 트리플 set.
        liberal_subtype은 4종(공통/핵심/학문기초/일반) 또는 None(전공·자유선택).
        core_area는 핵심교양 4영역(역사·철학/사회·공동체/문화·예술/과학기술·정보) 또는 None.
        교양은 같은 category='교양선택' 안에서도 4종을 따로 카운트하고, 핵심교양은
        4영역을 또 따로 카운트한다 (#47 Phase 2).
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
      +15  학칙 의무 영역(공통교양/핵심교양/학문기초교양) 카테고리
      +10  course.year_open == user_grade AND course.semester_open == user_semester
      +10  course.year_open < user_grade AND category ∈ {전공필수, 공통교양, 핵심교양, 학문기초교양}
            (밀린 필수 과목 — 졸업 지연 방지용 우선 노출)
      -10  course.year_open > user_grade
      -15  user_major == course.major AND 선수과목 미이수

    학년 무관 sentinel:
      course.year_open == 0 은 "전학년 대상" (강의시간표 import에서 매핑, #36).
      위 학년 비교 분기(==/</> 셋 다)는 전부 skip — 어떤 학년 학생에게도
      중립 노출. 카테고리/관심사/선수 가감산은 정상 적용.
    """
    score = 100  # 기준점. 여기에 항목별 가감산

    # 관심사 매칭 가산
    tags = list(course_tags) if course_tags else []
    if tags and set(tags) & set(user_interest_categories or []):
        score += BONUS_INTEREST_MATCH

    # 졸업요건 부족 카테고리 가산 — 키는 (category, liberal_subtype, core_area) 트리플
    # 교양 4종은 같은 category 안에서도 별개 진척도, 핵심교양은 4영역까지 별개 진척도 (#47 Phase 2)
    if (course.category, course.liberal_subtype, course.core_area) in short_categories:
        score += BONUS_CATEGORY_SHORT

    # 전공필수 가산
    if course.category == '전공필수':
        score += BONUS_MAJOR_REQUIRED

    # 학칙 의무 영역(공통/핵심/학문기초) 가산 — graduation_requirements.md §4.2~4.4 지정 과목 우선 (#47 Phase 3)
    if course.category in DESIGNATED_CATEGORIES:
        score += BONUS_DESIGNATED_REQUIRED

    # 학년/학기 적합성 — year_open=0 은 "전학년 대상" sentinel (#36 import에서 매핑)
    # 어떤 학년 학생에게도 동일하게 적합해야 하므로 학년 관련 가감산 전부 skip
    if user_grade is not None and course.year_open != 0:
        # 권장 학년/학기와 일치 가산
        if user_semester is not None and course.year_open == user_grade and course.semester_open == user_semester:
            score += BONUS_GRADE_SEMESTER_MATCH
        # 권장 학년이 사용자보다 위 감점
        if course.year_open > user_grade:
            score -= PENALTY_GRADE_EXCEEDED
        # 권장 학년이 지난 필수 영역(전공필수 + 공통/핵심/학문기초) 가산 — 졸업 지연 방지 (전공선택/일반/자유선택 제외)
        if course.year_open < user_grade and course.category in BACKLOG_REQUIRED_CATEGORIES:
            score += BONUS_BACKLOG_REQUIRED

    # 선수과목 미이수는 점수 계산 전에 hard filter로 제외됨 (#47 7번 — 5.3.1·5.3.2 일원화).
    # PENALTY_PREREQUISITE_MISSING은 호환용 상수로 남겨두되 점수식에서는 적용 안 함.

    return score


def _apply_free_election_overflow(taken_credits_by_key, requirements):
    """카테고리별 required 초과분을 자유선택 completed에 자동 합산 (graduation_requirements.md §6).

    학교 정책: 전공/교양에서 최소 이수학점을 초과한 학점은 자동으로 자유선택으로 인정.
    예) 컴공 전공 70 required인데 71학점 들으면 초과 1학점 → 자유선택 1학점 추가.

    taken_credits_by_key를 in-place mutate한다. 자유선택 키(('자유선택', None, None))에 overflow 합산.
    """
    cat_taken = {}
    for (cat, _, _), credits in taken_credits_by_key.items():
        cat_taken[cat] = cat_taken.get(cat, 0) + credits
    cat_required = {}
    for r in requirements:
        cat_required[r.category] = cat_required.get(r.category, 0) + r.required_credits

    overflow = sum(
        max(0, cat_taken.get(cat, 0) - cat_required.get(cat, 0))
        for cat in cat_required
        if cat != '자유선택'
    )
    free_key = ('자유선택', None, None)
    taken_credits_by_key[free_key] = taken_credits_by_key.get(free_key, 0) + overflow


def _build_short_categories(user):
    """
    졸업요건상 잔여학점이 남은 (category, liberal_subtype, core_area) 트리플 set을 빌드한다.

    department + admission_year 기준으로 GraduationRequirement를 조회하고,
    CourseHistory의 (category, liberal_subtype, core_area)별 합산 학점과 비교하여
    부족분이 있는 키만 set에 담는다. 교양 4종은 별개 진척도, 핵심교양은 4영역까지 별개
    진척도 (#47 Phase 2). 전공·자유선택 row는 liberal_subtype/core_area 모두 None.

    자유선택은 다른 카테고리에서 required 초과한 학점을 자동 합산해서 short 판정 (graduation_requirements.md §6).

    user.major 또는 admission_year 미입력 시 빈 set 반환 → 졸업요건 가산점 0점.
    """
    # 졸업요건 조회 불가 — 학과 또는 입학연도 미입력
    if not user.major or not user.admission_year:
        return set()

    requirements = list(GraduationRequirement.objects.filter(
        department=user.major,
        admission_year=user.admission_year,
    ))

    # 사용자 이수이력의 (category, liberal_subtype, core_area)별 학점 합산
    taken_credits_by_key = {}
    for history in user.course_histories.all():
        key = (history.category, history.liberal_subtype, history.core_area)
        taken_credits_by_key[key] = taken_credits_by_key.get(key, 0) + history.credits

    # 자유선택 overflow 합산 — 다른 카테고리 초과분이 자유선택 채움
    _apply_free_election_overflow(taken_credits_by_key, requirements)

    # 키별 필요학점 > 이수학점 인 경우만 short으로 분류
    return {
        (req.category, req.liberal_subtype, req.core_area) for req in requirements
        if taken_credits_by_key.get((req.category, req.liberal_subtype, req.core_area), 0) < req.required_credits
    }


def recommend_next_semester_courses(user, *, target_year=None, target_semester=None):
    """
    한 사용자에 대해 다음학기 추천 과목 리스트를 반환한다. (spec 5.3.1, #36)

    DRF Request/Response에 의존하지 않는 순수 도메인 함수. view 등 호출자가
    User 인스턴스를 넘기면 (점수, Course) 튜플 리스트를 정렬해 반환한다.

    학기 인자:
      target_year / target_semester 둘 다 None 이면 _curriculum_first_slot(user)
      로 자동 결정 (5.3.2와 동일 로직 — 1학기 듣는 중 → 같은 해 2학기, 2학기 듣는
      중 → 다음 해 1학기).
      값이 지정되면 그 학기에 개설된 CourseOffering 이 있는 Course 만 후보.
      Offering 자체가 없는 Course (기존 더미 시드 등)는 학기 정보 없음 → 통과.

    처리 흐름:
      1. 사용자 데이터 조회 — 수강이력 / 현재수강 / 관심사
      2. target 학기 결정 (인자 우선, 미지정 시 자동)
      3. Hard Filter — 이수/수강 중 제외 + 학기 Offering 매칭
      4. 졸업요건 잔여학점 분석 → 부족 카테고리 set 빌드
      5. 후보 과목 각각에 calculate_recommendation_score 호출
      6. 정렬 — score DESC → CATEGORY_PRIORITY ASC → course_code ASC
      7. (score, Course) 튜플 리스트 반환

    반환: list[tuple[int, Course]] — 정렬된 추천 결과 (상위가 가장 추천 강함)
    """
    # target 학기 결정 — 인자 우선, 둘 중 하나라도 빠지면 자동 결정값으로 보충
    if target_year is None or target_semester is None:
        auto_year, auto_sem = _curriculum_first_slot(user)
        if target_year is None:
            target_year = auto_year
        if target_semester is None:
            target_semester = auto_sem

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

    # 같은 이름 다른 코드 추천 제외 — 학칙 §9 동일과목 정신 (#47).
    # 예: 컴공 학생이 컴정101 C언어 들었으면 기컴101 C언어도 후보에서 제외.
    # 주의: foreign_major_versions는 코드 기준만 제외 (이름 제외에 포함하면 전공 버전도 같이 제외돼버림)
    excluded_names = set(
        Course.objects.filter(course_code__in=excluded_codes).values_list('name', flat=True)
    )
    # 학생 전공의 전공필수와 같은 이름의 타과·교양 버전은 코드 기준만 추가 제외 (#47)
    excluded_codes = excluded_codes | _foreign_major_versions_to_exclude(user.major)

    # Hard Filter — 이수/수강 중(같은 코드 + 같은 이름) 제외 + 학기 Offering 매칭
    # Offering 있는 Course 는 target 학기 매칭만 통과, Offering 자체 없으면 통과 (#36)
    # 후자는 기존 #24/#25 더미 시드 호환용 — 운영 데이터는 전부 Offering 동반
    has_offering_qs = CourseOffering.objects.values_list('course_id', flat=True)
    matching_qs = CourseOffering.objects.filter(
        year=target_year, semester=target_semester,
    ).values_list('course_id', flat=True)

    # prefetch_related로 선수과목 + target 학기 분반·시간표 N+1 회피 (#111).
    # offerings는 target_year/target_semester 매칭분만 prefetch — view가 그대로 직렬화.
    term_offerings_qs = CourseOffering.objects.filter(
        year=target_year, semester=target_semester,
    ).prefetch_related('schedules')
    candidates = list(
        Course.objects
        .exclude(course_code__in=excluded_codes)
        .exclude(name__in=excluded_names)
        .filter(Q(pk__in=matching_qs) | ~Q(pk__in=has_offering_qs))
        .distinct()
        .prefetch_related(
            'prerequisites',
            Prefetch('offerings', queryset=term_offerings_qs),
        )
    )

    # 졸업요건 부족 카테고리 (점수 +15용)
    short_categories = _build_short_categories(user)

    # 이수 완료한 Course.id 집합 (선수과목 검사용)
    # CourseHistory는 course_code만 저장하므로 Course.id로 변환 필요
    completed_course_ids = set(
        Course.objects.filter(course_code__in=taken_codes).values_list('id', flat=True)
    )

    # 후보별 점수 계산
    # 선수과목 hard filter — 동일 학과 학생이 선수과목 안 들었으면 후보에서 제외 (#47 7번, 5.3.1·5.3.2 일원화)
    # 타과생은 prereq 제한 면제 (학과 외부 학생이 prereq 모두 들었을 가능성 거의 없음 — 강제하면 결과 빈 학기)
    scored = []
    for course in candidates:
        prereq_ids = {cp.prerequisite_id for cp in course.prerequisites.all()}
        if user.major and course.major == user.major and prereq_ids and not prereq_ids.issubset(completed_course_ids):
            continue  # 선수과목 미이수 — 추천 결과에서 제외
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


# ────────────────────────────────────────
# 5.3.2 전체 커리큘럼 추천
# ────────────────────────────────────────

# 학기 매핑 (spec 5.3.2, #25)
#   semester_open=1 = 1학기
#   semester_open=2 = 2학기
#   semester_open=3 = 하계 계절학기 (1↔2 사이)
#   semester_open=4 = 동계 계절학기 (2↔1 사이)

# 노브 기본값 — LLM이 사용자 방향성을 못 받은 경우 폴백용
DEFAULT_MAX_CREDITS = 18           # 베이스 학기 학점 상한
DEFAULT_NUM_PLANS = 3              # 변형 plan 개수 (2~5)
DEFAULT_INTEREST_WEIGHT = 1.0      # 관심사 가중치 (1.0이면 5.3.1과 동일)

# 변형 plan을 만들 때 베이스 max_credits에 더할 오프셋
#   index 0=베이스 / 1=+3(빡센) / 2=-3(여유) / 3=+1 / 4=-1 — 최대 5안까지
CREDIT_OFFSETS = [0, +3, -3, +1, -1]

# 전공선택 쿼터 — 학기당 최소 6학점 보장 (#112).
# score-greedy가 전공필수·교양 backlog를 우선 채워서 전공선택이 0건 되는 갭 보완.
# 전공선택 잔여 < 6이면 잔여만큼만 채움 (overshoot 1회 허용).
MAJOR_ELECTIVE_QUOTA_CREDITS = 6
KEY_MAJOR_ELECTIVE = ('전공선택', None, None)  # remaining_by_cat 트리플 키


def _next_curriculum_slot(year, sem, *, include_summer, include_winter):
    """현재 학기 슬롯의 다음 학기 슬롯을 반환한다.

    진행 순서: 1 → 하계(3) → 2 → 동계(4) → 다음해 1 → ...
    include_summer/winter가 False면 해당 계절학기 슬롯을 건너뜀.
    """
    if sem == 1:
        return (year, 3) if include_summer else (year, 2)   # 1학기 → 하계 또는 2학기
    if sem == 3:
        return year, 2                                       # 하계 → 2학기
    if sem == 2:
        return (year, 4) if include_winter else (year + 1, 1)  # 2학기 → 동계 또는 다음해 1학기
    # sem == 4 (동계) → 다음해 1학기
    return year + 1, 1


def _curriculum_first_slot(user):
    """추천 시작 학기 슬롯 결정. 사용자 입력 학기를 우선 신뢰 (spec 5.3.4 정책 동일)."""
    today = date.today()
    if user.semester == 1:
        return today.year, 2          # 1학기 듣는 중 → 다음은 같은 해 2학기
    if user.semester == 2:
        return today.year + 1, 1      # 2학기 듣는 중 → 다음 해 1학기
    # 학기 정보 없을 때 calendar 기반 fallback
    if 3 <= today.month <= 8:
        return today.year, 2          # 봄 시즌이면 같은 해 2학기부터
    return today.year + 1, 1          # 가을 시즌이면 다음 해 1학기부터


def _remaining_regular_semesters(user):
    """졸업까지 남은 정규학기 수 (계절학기 제외)."""
    today = date.today()
    current_year = today.year
    current_sem = 1 if 3 <= today.month <= 8 else 2

    # 사용자가 graduation_year/month 입력했으면 거기 기반
    if user.graduation_year and user.graduation_month:
        grad_sem = 1 if user.graduation_month <= 8 else 2
        n = (user.graduation_year - current_year) * 2 + (grad_sem - current_sem)
        return max(1, n)
    # 미입력 fallback — grade/semester로 추정
    grade = user.grade or 1
    semester = user.semester or 1
    return max(1, (4 - grade) * 2 + (2 - semester))


def generate_curriculum_plans(
    user,
    *,
    max_credits=DEFAULT_MAX_CREDITS,  # 한 학기 최대 학점 (예: 18, 21)
    category_weights=None,            # 카테고리별 가중치 (예: 핵심교양 더 듣고싶으면 {"핵심교양": 1.5})
    interest_weight=DEFAULT_INTEREST_WEIGHT, # 관심사 매칭 가중치
    include_summer=False,             # 계절학기 포함 여부
    include_winter=False,
    num_plans=DEFAULT_NUM_PLANS,      # 만들 plan 개수
):  
    """졸업까지 학기별 추천 커리큘럼을 2~5안 반환한다 (spec 5.3.2, #25).

    학기 매핑: semester_open=1/2 정규, =3 하계, =4 동계.

    노브:
      max_credits         : 한 학기 학점 상한 베이스. 변형 plan은 ±오프셋 적용
      category_weights    : {카테고리: 배수} — 점수 함수 카테고리 가산점에 추가 배수
      interest_weight     : 관심사 매칭 시 추가 배수
      include_summer/winter: 계절학기 슬롯 포함 여부
      num_plans           : 생성할 변형 plan 개수 (2~5로 clamp)

    반환: list[dict] — 각 plan은 {plan_number, max_credits, semesters: [...]}.
          semesters의 각 학기는 {year, semester, courses: [Course, ...]}.
          courses를 4키(major_required 등)로 분리하는 건 view/serializer 책임.
          데이터 부족으로 1안만 만들어지면 1안만 반환 (복제 X).
    """
    num_plans = max(2, min(5, num_plans))               # spec 2~5 clamp
    category_weights = category_weights or {}
    context = _build_curriculum_context(
        user, include_summer=include_summer, include_winter=include_winter,
    )

    plans = []
    seen_signatures = set()                              # 동일 plan dedupe
    for offset in CREDIT_OFFSETS[:num_plans]:
        variant_credits = max(9, max_credits + offset)   # 너무 낮으면 의미 없음, 최소 9
        semesters = _build_single_plan(
            context, user=user,
            max_credits=variant_credits,
            category_weights=category_weights,
            interest_weight=interest_weight,
            include_summer=include_summer,
            include_winter=include_winter,
        )
        if not semesters:
            continue
        signature = _plan_signature(semesters)
        if signature in seen_signatures:                 # 변형이 같은 결과 내면 skip
            continue
        seen_signatures.add(signature)
        plans.append({
            'plan_number': len(plans) + 1,
            'max_credits': variant_credits,
            'semesters': semesters,
        })
        if len(plans) >= num_plans:
            break
    return plans


def _plan_signature(semesters):
    """plan dedupe용 시그니처 — 학기별 과목코드 sorted tuple."""
    return tuple(
        (s['year'], s['semester'], tuple(sorted(c.course_code for c in s['courses'])))
        for s in semesters
    )


def _build_curriculum_context(user, *, include_summer, include_winter):
    """plan 생성에 필요한 사용자 컨텍스트를 한 번에 빌드 (학기 루프 안 N+1 방지)."""
    interest_categories = set(user.interests.values_list('category', flat=True))

    # Hard filter — 이수 + 현재 수강 중 제외
    taken_codes = set(user.course_histories.values_list('course_code', flat=True))
    current_codes = set(user.current_courses.values_list('course_code', flat=True))
    excluded_codes = taken_codes | current_codes
    # 같은 이름 다른 코드 추천 제외 — 학칙 §9 동일과목 정신 (#47)
    excluded_names = set(
        Course.objects.filter(course_code__in=excluded_codes).values_list('name', flat=True)
    )
    # 학생 전공의 전공필수와 같은 이름의 타과·교양 버전은 코드 기준만 추가 제외 (학과별 교양 블랙리스트, #47)
    excluded_codes = excluded_codes | _foreign_major_versions_to_exclude(user.major)

    # 학기 슬롯 필터 — include_summer/winter에 따라 3/4 토글
    allowed_sems = [1, 2]
    if include_summer:
        allowed_sems.append(3)                          # 하계
    if include_winter:
        allowed_sems.append(4)                          # 동계

    # 후보 — 사용자 전공 또는 교양 4종 중에서 위 학기 슬롯에 열리는 것만 (#47 Phase 3)
    liberal_categories = ['공통교양', '핵심교양', '학문기초교양', '일반교양']
    candidates = list(
        Course.objects.filter(
            Q(major=user.major) | Q(category__in=liberal_categories),
            semester_open__in=allowed_sems,
        )
        .exclude(course_code__in=excluded_codes)
        .exclude(name__in=excluded_names)
        .prefetch_related('offerings__schedules', 'prerequisites')
    )

    # 졸업요건 잔여학점 — 키는 (category, liberal_subtype, core_area) 트리플 (#47 Phase 2).
    # 교양 4종 + 핵심교양 4영역까지 별개 진척도.
    completed_credits_by_key = {}
    for h in user.course_histories.all():
        key = (h.category, h.liberal_subtype, h.core_area)
        completed_credits_by_key[key] = completed_credits_by_key.get(key, 0) + h.credits
    remaining_by_cat = {}  # 변수명은 호환 유지, 값 키는 트리플
    if user.major and user.admission_year:
        requirements = list(GraduationRequirement.objects.filter(
            department=user.major, admission_year=user.admission_year,
        ))
        # 자유선택 overflow 합산 — 다른 카테고리 초과분이 자유선택 채움 (graduation_requirements.md §6)
        _apply_free_election_overflow(completed_credits_by_key, requirements)
        for req in requirements:
            key = (req.category, req.liberal_subtype, req.core_area)
            done = completed_credits_by_key.get(key, 0)
            remaining_by_cat[key] = max(0, req.required_credits - done)

    # 이수 Course.id set — plan_completed_ids의 시작점 (선수과목 검사용)
    completed_ids = set(
        Course.objects.filter(course_code__in=taken_codes).values_list('id', flat=True)
    )

    # 선수과목 인덱스 한 번에 — 루프 안에서 매번 쿼리 안 날리도록
    prereq_ids_by_course_id = {}
    for cp in CoursePrerequisite.objects.filter(
        course__in=candidates,
    ).values('course_id', 'prerequisite_id'):
        prereq_ids_by_course_id.setdefault(cp['course_id'], set()).add(cp['prerequisite_id'])

    first_year, first_sem = _curriculum_first_slot(user)

    return {
        'candidates': candidates,
        'completed_ids': completed_ids,
        'remaining_by_cat': remaining_by_cat,
        'prereq_ids_by_course_id': prereq_ids_by_course_id,
        'first_year': first_year,
        'first_sem': first_sem,
        'remaining_semesters': _remaining_regular_semesters(user),
        'interest_categories': interest_categories,
    }


def _build_single_plan(context, *, user, max_credits, category_weights, interest_weight,
                       include_summer, include_winter):
    """한 변형 plan 빌드 — 학기마다 점수 계산 후 학점 상한까지 채움.

    선수과목 정책: hard filter (5.3.1과 다름). 같은 plan 안에서 prereq 학기가
    먼저 와야 함. 미래 학기를 미리 계획하는 5.3.2 성격상 자연스러움.
    """
    remaining_by_cat = dict(context['remaining_by_cat'])  # 학기마다 갱신
    plan_used_codes = set()                                # 이 plan 안에서 추천된 과목
    plan_used_names = set()                                # 같은 이름 다른 코드 중복 방지 (학칙 §9, #47)
    plan_completed_ids = set(context['completed_ids'])     # 사용자 이수 + plan 진행분

    year, sem = context['first_year'], context['first_sem']
    regular_count = 0                                       # 정규학기만 카운트
    semesters = []

    while regular_count < context['remaining_semesters']:
        # 이 학기 후보 — 학기 슬롯 일치 + 미사용 + (같은 학과면) prereq 충족
        eligible = []
        for course in context['candidates']:
            if course.course_code in plan_used_codes:
                continue
            if course.name in plan_used_names:              # 같은 이름 다른 코드 중복 방지 (#47)
                continue
            if course.semester_open != sem:                 # 이번 학기에 안 열림
                continue
            prereq_ids = context['prereq_ids_by_course_id'].get(course.id, set())
            # 같은 학과 학생만 prereq 강제 (타과생은 5.3.1과 동일하게 자유)
            if user.major and course.major == user.major and prereq_ids:
                if not prereq_ids.issubset(plan_completed_ids):
                    continue
            eligible.append(course)

        # 잔여 카테고리 매 학기 갱신 — 추천 진행분이 차감된 상태. 키는 (category, liberal_subtype, core_area)
        short_cats = {key for key, rem in remaining_by_cat.items() if rem > 0}

        scored = []
        for course in eligible:
            base = calculate_recommendation_score(
                course,
                user_grade=user.grade,
                user_semester=user.semester,
                user_major=user.major,
                user_interest_categories=context['interest_categories'],
                short_categories=short_cats,
                completed_course_ids=plan_completed_ids,
                course_prerequisite_ids=context['prereq_ids_by_course_id'].get(course.id, set()),
                course_tags=course.tags,
            )
            # 카테고리 가중치 — 1.0이면 영향 없음. 1.5면 해당 카테고리 강력 우선
            cat_w = category_weights.get(course.category, 1.0)
            extra = (cat_w - 1.0) * BONUS_MAJOR_REQUIRED        # 가산 기준값(25)에 비례
            # 관심사 가중치 — 매칭된 경우에만 추가 스케일
            if interest_weight != 1.0 and course.tags:
                if set(course.tags) & context['interest_categories']:
                    extra += (interest_weight - 1.0) * BONUS_INTEREST_MATCH
            scored.append((base + extra, course))

        # 점수 DESC → 카테고리 우선순위 → course_code (5.3.1과 동일 키)
        scored.sort(key=lambda x: (
            -x[0], CATEGORY_PRIORITY.get(x[1].category, 99), x[1].course_code,
        ))

        sem_courses = []
        sem_credits = 0

        def _commit(course):
            # 채움 phase 공용 — sem 상태 + plan 누적 + 카테고리 잔여 동기화
            sem_courses.append(course)
            plan_used_codes.add(course.course_code)
            plan_used_names.add(course.name)             # 같은 이름 다른 코드 후속 학기 제외
            plan_completed_ids.add(course.id)            # 다음 학기 prereq 검사용
            cat_key = (course.category, course.liberal_subtype, course.core_area)
            if cat_key in remaining_by_cat:
                remaining_by_cat[cat_key] = max(0, remaining_by_cat[cat_key] - course.credits)

        # Phase 1 — 전공선택 쿼터 우선 채움 (#112): 학기당 최소 6학점.
        # score-greedy(Phase 2)가 전공필수·교양 backlog로 18학점 다 먹어버리는 케이스에서
        # 전공선택이 0건이 되는 갭 보완. 잔여 0이면 short_cats에서 빠져 이 phase skip.
        major_elective_target = min(
            MAJOR_ELECTIVE_QUOTA_CREDITS,
            remaining_by_cat.get(KEY_MAJOR_ELECTIVE, 0),
        )
        if major_elective_target > 0:
            elec_credits = 0
            for _score, course in scored:
                if elec_credits >= major_elective_target:
                    break
                if course.category != '전공선택':
                    continue
                if sem_credits + course.credits > max_credits:
                    continue
                _commit(course)
                sem_credits += course.credits
                elec_credits += course.credits

        # Phase 2 — 학점 상한까지 score-greedy 채움. 전공선택 쿼터분은 이미 plan_used_codes
        # 안에 있어 자동 skip.
        for _score, course in scored:
            if course.course_code in plan_used_codes:
                continue
            if sem_credits + course.credits > max_credits:
                continue
            _commit(course)
            sem_credits += course.credits

        if sem_courses:
            semesters.append({
                'year': year, 'semester': sem,
                'courses': sem_courses,
            })

        # 정규학기만 카운트 — 계절학기는 부수
        if sem in (1, 2):
            regular_count += 1

        year, sem = _next_curriculum_slot(
            year, sem, include_summer=include_summer, include_winter=include_winter,
        )

    return semesters
