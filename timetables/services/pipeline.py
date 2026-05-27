"""5.3.6 시간표 추천 파이프라인 — 7-step 오케스트레이터 (#97).

STEP 1·2: courses/services.recommend_next_semester_courses (5.3.1 점수+정렬 재활용)
STEP 3·4: filtering.expand_to_offerings + filter_offerings_by_prefs
STEP 5:   combining.generate_combinations (DFS)
STEP 6:   scoring.score_combination (조합 점수 + reason)
STEP 7:   diversity.select_diverse_top (Jaccard greedy + top-N)

note 발급:
- insufficient_candidates: 각 STEP에서 후보 0 → 빈 plans 반환
- low_diversity_pool: 다양성 통과 plans 1~2개 (TOP_N 미달)
"""
from courses.services import _build_short_categories, recommend_next_semester_courses

from timetables.codes import Note
from timetables.models import UserPreference
from timetables.services.combining import generate_combinations
from timetables.services.diversity import select_diverse_top
from timetables.services.filtering import (
    expand_to_offerings,
    filter_offerings_by_prefs,
)
from timetables.services.scoring import score_combination


# 내부 튜닝 상수 (#25 안 B 정책 — 외부 노출 X)
CANDIDATE_POOL_SIZE = 20   # STEP 2 점수 상위 K
TOP_N = 3                  # STEP 7 반환 plan 수 (#97 결정 9 — 시연 톤)

PREFERENCE_FIELDS = (
    'prefer_off_days', 'banned_days',
    'no_morning', 'no_evening', 'lunch_break',
    'max_credits', 'min_credits',
)


# DAY_ORDER — 응답 offerings 정렬용 (요일 ASC)
DAY_ORDER = {'월': 0, '화': 1, '수': 2, '목': 3, '금': 4}


def merge_prefs(payload: dict, db_pref: UserPreference) -> dict:
    """#97 결정 5: field-level merge. 키 없음 → DB값 유지 / 키 있음 → override (빈 배열·false 포함)."""
    return {
        field: payload[field] if field in payload else getattr(db_pref, field)
        for field in PREFERENCE_FIELDS
    }


def get_or_create_preference(user) -> UserPreference:
    """#97 결정 5: 추천 요청 시 lazy 생성 (signal 회피). 모델 default(18/15/false) 값 row 만들고 반환."""
    pref, _ = UserPreference.objects.get_or_create(user=user)
    return pref


def recommend_timetables(user, year: int, semester: int, payload: dict) -> dict:
    """7-step 파이프라인 실행. spec 5.3.6 응답 본문 반환.

    Args:
        user: User instance
        year, semester: 학기 (caller 검증 책임 — view에서 None → 400 MISSING_YEAR_SEMESTER)
        payload: dict — year/semester 외 prefs override

    Returns:
        {'plans': [...], 'note': str | None}
    """
    db_pref = get_or_create_preference(user)
    prefs = merge_prefs(payload, db_pref)

    # STEP 1·2: 과목 점수 정렬 + K=20 prune
    scored_courses = recommend_next_semester_courses(
        user, target_year=year, target_semester=semester,
    )   # list[(score, Course)]
    top_courses = scored_courses[:CANDIDATE_POOL_SIZE]
    if not top_courses:
        return {'plans': [], 'note': Note.INSUFFICIENT_CANDIDATES}

    course_scores = {c.id: s for s, c in top_courses}
    courses = [c for _, c in top_courses]

    # STEP 3: 분반 확장
    offerings = expand_to_offerings(courses, year, semester)
    if not offerings:
        return {'plans': [], 'note': Note.INSUFFICIENT_CANDIDATES}

    # STEP 4: prefs hard filter
    filtered = filter_offerings_by_prefs(offerings, prefs)
    if not filtered:
        return {'plans': [], 'note': Note.INSUFFICIENT_CANDIDATES}

    # STEP 5: DFS 조합 생성
    max_credits = prefs.get('max_credits', 18)
    raw_combos = list(generate_combinations(filtered, max_credits=max_credits))
    if not raw_combos:
        return {'plans': [], 'note': Note.INSUFFICIENT_CANDIDATES}

    # STEP 6: 조합 점수
    short_categories = _build_short_categories(user)
    interest_cats = set(user.interests.values_list('category', flat=True))

    scored = []
    for combo in raw_combos:
        s, reasons, meta = score_combination(
            combo, course_scores, prefs,
            short_categories=short_categories,
            user_interest_categories=interest_cats,
        )
        scored.append((s, combo, reasons, meta))

    # 점수 DESC
    scored.sort(key=lambda x: x[0], reverse=True)

    # STEP 7: 다양성 dedup + top-N
    selected = select_diverse_top(scored, n=TOP_N)

    plans = [_build_plan_dict(item) for item in selected]

    # fallback note: 결과 적으면 low_diversity_pool / 0이면 insufficient
    if not plans:
        return {'plans': [], 'note': Note.INSUFFICIENT_CANDIDATES}
    if len(plans) < TOP_N:
        return {'plans': plans, 'note': Note.LOW_DIVERSITY_POOL}
    return {'plans': plans, 'note': None}


def _offering_sort_key(offering):
    """offering 정렬 키 — 가장 빠른 schedule의 (요일 idx, 시작시간)."""
    schedules = list(offering.schedules.all())
    if not schedules:
        return (99, None)
    return min(
        (DAY_ORDER.get(s.day_of_week, 99), s.start_time)
        for s in schedules
    )


def _build_plan_dict(scored_item) -> dict:
    """(score, combo, reasons, meta) → plan dict.

    offerings는 CourseOffering 인스턴스 그대로 (serializer에서 변환).
    credits_by_category는 0 키 생략(응답 슬림).
    """
    score, combo, reasons, meta = scored_item
    by_cat = {}
    for o in combo:
        by_cat[o.course.category] = by_cat.get(o.course.category, 0) + o.course.credits
    return {
        'score': score,
        'total_credits': meta['credits'],
        'credits_by_category': by_cat,
        'offerings': sorted(combo, key=_offering_sort_key),
        'reason_codes': reasons,
    }
