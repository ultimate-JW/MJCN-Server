"""
courses 앱 서비스 레이어.

View / dashboard / chat 등 여러 곳에서 재사용 가능한 도메인 로직을 모아둔
순수 함수 모음이다. 모델 조회는 하되 DRF Request / Response에 의존하지 않는다.

주요 함수:
  - _resolve_graduation_date(user)     : 졸업일 결정 (spec 5.3.4)
  - calc_graduation_progress(user)     : 졸업까지 진척도 % (spec 5.3.5)
"""

from datetime import date

from .models import AcademicCalendar


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
    if not user.admission_year:
        return 0

    graduation_date, _ = _resolve_graduation_date(user)
    if graduation_date is None:
        return 0

    start_date = date(user.admission_year, 3, 1)
    today = date.today()

    if today <= start_date:
        return 0
    if today >= graduation_date:
        return 100

    total_days = (graduation_date - start_date).days
    if total_days <= 0:
        return 0

    pct = round((today - start_date).days / total_days * 100)
    return max(0, min(100, pct))
