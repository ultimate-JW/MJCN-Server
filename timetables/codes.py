"""시간표 추천 응답 머신 코드 상수 (#97 5.3.6).

자연어 변환은 LLM/프론트 책임 (이슈 #25 fallback note 정신 일관).
오타 방지 목적의 enum-like 상수 모음. 단순 class attr로 두고 import 시 모듈 참조.
"""


# 응답 최상위 note — plan 0~3개 와 무관하게 응답 전체 메타
class Note:
    MISSING_YEAR_SEMESTER = 'MISSING_YEAR_SEMESTER'     # year/semester 미지정 → HTTP 400
    INSUFFICIENT_CANDIDATES = 'insufficient_candidates'  # K=20 못 채움 (시드 부족)
    LOW_DIVERSITY_POOL = 'low_diversity_pool'            # 다양성 통과 plan 3개 미만


# plan별 reason_codes — 응답에 객체 {code, meta?} 배열로 동봉
class Reason:
    # (a) 졸업요건 cover
    COVERS_REQUIRED_MAJOR = 'covers_required_major'           # meta: {count}
    COVERS_SHORT_CATEGORIES = 'covers_short_categories'       # meta: {categories}
    INCLUDES_BACKLOG_REQUIRED = 'includes_backlog_required'   # meta: {count}

    # (b) 사용자 prefs 충족
    OFF_DAY_SATISFIED = 'off_day_satisfied'                   # meta: {days}
    NO_MORNING_SATISFIED = 'no_morning_satisfied'             # meta 없음
    NO_EVENING_SATISFIED = 'no_evening_satisfied'             # meta 없음
    LUNCH_BREAK_SATISFIED = 'lunch_break_satisfied'           # meta 없음
    CREDITS_IN_TARGET_RANGE = 'credits_in_target_range'       # meta: {credits}

    # (c) 관심사 / 학점 안내
    MATCHES_INTEREST_AREAS = 'matches_interest_areas'         # meta: {count, areas}
    CREDITS_BELOW_TARGET = 'credits_below_target'             # meta: {credits, min}
