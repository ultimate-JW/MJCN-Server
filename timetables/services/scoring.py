"""5.3.6 STEP 6 — 조합 단위 점수 + reason_codes 발급.

과목 단위 점수(5.3.1 calculate_recommendation_score)는 caller가 미리 dict로 계산해 넘긴다.
이 모듈은 합 + prefs 가산 + 부족 카테고리 cover 가산 + min_credits 패널티 + reason 동봉.

내부 튜닝 상수(#25 안 B 정책 — 외부 노출 X):
"""
from courses.models import TIMETABLE_SLOT_BITS_PER_DAY
from timetables.codes import Reason
from timetables.utils.conflict import merge_bitmaps


BONUS_OFF_DAY = 15        # prefer_off_days 충족 요일 1개당
BONUS_NO_MORNING = 10     # 결과적으로 1교시 없음 (안내성)
BONUS_NO_EVENING = 5      # 결과적으로 18시 이후 없음
BONUS_LUNCH_BREAK = 8     # 12-14시 사이 연속 1시간 공강 (수업 있는 요일 중 1요일 이상)
BONUS_BACKLOG_COVER = 20  # 부족 카테고리 전부 cover
PENALTY_CREDIT_LOW = 15   # min_credits 미달 (#97 결정 3 — soft 하한)

DAY_NAMES = ['월', '화', '수', '목', '금']
LUNCH_MASK_2SLOTS = [0b0011, 0b0110, 0b1100]   # 12-14시(slot 6,7,8,9) 내 인접 2 slot 공강 패턴


def has_morning(combo_bitmap: int) -> bool:
    """1교시(09:00~10:00 = slot 0,1) 점유 schedule이 있는지."""
    for day in range(5):
        if (combo_bitmap >> (day * TIMETABLE_SLOT_BITS_PER_DAY)) & 0b11:
            return True
    return False


def has_evening(combo_bitmap: int) -> bool:
    """18시 이후(slot 18~25, 8 slot) 점유 schedule이 있는지."""
    EVENING_MASK = 0xFF << 18
    for day in range(5):
        day_bits = (combo_bitmap >> (day * TIMETABLE_SLOT_BITS_PER_DAY)) & 0xFFFFFFFF
        if day_bits & EVENING_MASK:
            return True
    return False


def days_without_class(combo_bitmap: int) -> set:
    """수업 없는 요일 set ('월'~'금')."""
    free = set()
    for day in range(5):
        if not ((combo_bitmap >> (day * TIMETABLE_SLOT_BITS_PER_DAY)) & 0xFFFFFFFF):
            free.add(DAY_NAMES[day])
    return free


def has_lunch_break(combo_bitmap: int) -> bool:
    """수업 있는 요일 중 최소 1요일에 12-14시 연속 1시간(2 slot) 공강 있으면 True.

    수업 없는 요일은 점심 의미 X (안 셈) — 가산이 의미있게 동작.
    """
    for day in range(5):
        day_bits = (combo_bitmap >> (day * TIMETABLE_SLOT_BITS_PER_DAY)) & 0xFFFFFFFF
        if day_bits == 0:
            continue
        lunch_bits = (day_bits >> 6) & 0b1111   # slot 6,7,8,9 → bit 0,1,2,3
        if any((lunch_bits & m) == 0 for m in LUNCH_MASK_2SLOTS):
            return True
    return False


def score_combination(
    combo,
    course_scores: dict,
    prefs: dict,
    *,
    short_categories: set = None,
    user_interest_categories: set = None,
):
    """STEP 6: combination 단위 점수 + reason_codes + 메타.

    Args:
        combo: tuple[CourseOffering, ...] — 충돌 없는 조합
        course_scores: {course_id: 5.3.1 score} — 과목 단위 점수 (caller가 미리 계산)
        prefs: merge된 prefs dict (5번 결정)
        short_categories: 부족 카테고리 set (None이면 cover 가산 X)
        user_interest_categories: 사용자 관심사 set (None이면 매칭 reason X)

    Returns:
        (score: int, reasons: list[dict], meta: dict)
        meta = {'credits': total_credits, 'bitmap': combo_bitmap}
    """
    bitmap = merge_bitmaps([o.time_bitmap for o in combo])
    total_credits = sum(o.course.credits for o in combo)

    score = sum(course_scores.get(o.course_id, 0) for o in combo)
    reasons = []

    # prefer_off_days 가산
    prefer_off_days = set(prefs.get('prefer_off_days') or [])
    if prefer_off_days:
        free = days_without_class(bitmap)
        satisfied = prefer_off_days & free
        if satisfied:
            score += BONUS_OFF_DAY * len(satisfied)
            reasons.append({'code': Reason.OFF_DAY_SATISFIED, 'meta': {'days': sorted(satisfied)}})

    # 1교시 없음 — prefs 명시 안 했어도 결과 좋으면 안내성 가산
    if not has_morning(bitmap):
        if not prefs.get('no_morning', False):
            score += BONUS_NO_MORNING
        reasons.append({'code': Reason.NO_MORNING_SATISFIED})

    # 야간 없음 — 동일 패턴
    if not has_evening(bitmap):
        if not prefs.get('no_evening', False):
            score += BONUS_NO_EVENING
        reasons.append({'code': Reason.NO_EVENING_SATISFIED})

    # 점심시간 — prefs 명시 시에만 가산·발급
    if prefs.get('lunch_break', False) and has_lunch_break(bitmap):
        score += BONUS_LUNCH_BREAK
        reasons.append({'code': Reason.LUNCH_BREAK_SATISFIED})

    # 부족 카테고리 cover
    if short_categories:
        combo_cats = {o.course.category for o in combo}
        if short_categories.issubset(combo_cats):
            score += BONUS_BACKLOG_COVER
            reasons.append({
                'code': Reason.COVERS_SHORT_CATEGORIES,
                'meta': {'categories': sorted(short_categories)},
            })

    # 학점 범위
    min_c = prefs.get('min_credits', 15)
    max_c = prefs.get('max_credits', 18)
    if total_credits < min_c:
        score -= PENALTY_CREDIT_LOW
        reasons.append({
            'code': Reason.CREDITS_BELOW_TARGET,
            'meta': {'credits': total_credits, 'min': min_c},
        })
    elif total_credits <= max_c:
        reasons.append({
            'code': Reason.CREDITS_IN_TARGET_RANGE,
            'meta': {'credits': total_credits},
        })

    # 관심사 매칭 (#24 InterestArea 재활용)
    if user_interest_categories:
        matched_areas = set()
        matched_count = 0
        for o in combo:
            inter = set(o.course.tags or []) & user_interest_categories
            if inter:
                matched_count += 1
                matched_areas |= inter
        if matched_count:
            reasons.append({
                'code': Reason.MATCHES_INTEREST_AREAS,
                'meta': {'count': matched_count, 'areas': sorted(matched_areas)},
            })

    return score, reasons, {'credits': total_credits, 'bitmap': bitmap}
