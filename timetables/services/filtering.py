"""5.3.6 시간표 추천 파이프라인 STEP 3·4 — 분반 확장 + 사용자 prefs hard filter.

STEP 1·2 (과목 hard filter + 점수 prune)는 courses/services.recommend_next_semester_courses 재활용.
이 모듈은 그 결과를 받아 분반 단위로 펼치고 prefs로 거른다.
"""
from datetime import time as dt_time

from courses.models import CourseOffering


# Hard filter 경계 (#97 결정 3)
MORNING_CUTOFF = dt_time(10, 0)   # < 10:00 = 1교시 (09:00 시작) 제외 기준
EVENING_CUTOFF = dt_time(18, 0)   # >= 18:00 = 야간 시작 제외 기준


def expand_to_offerings(courses, year: int, semester: int):
    """STEP 3: Course 리스트 → 해당 학기 CourseOffering 리스트 펼침.

    한 Course에 분반이 여러 개면 각각 별개 후보 (분반 단위, #97 결정 3).
    schedules prefetch — STEP 4 / DFS 충돌 검사 시 N+1 쿼리 방지.
    """
    course_ids = [c.id for c in courses]
    return list(
        CourseOffering.objects
        .filter(course_id__in=course_ids, year=year, semester=semester)
        .select_related('course')
        .prefetch_related('schedules')
    )


def passes_prefs_hard_filter(offering, prefs: dict) -> bool:
    """STEP 4: 사용자 prefs hard filter — 위반 schedule이 하나라도 있으면 False.

    검사:
      - no_morning: start_time < 10:00 (1교시 09:00 시작) 분반 제외
      - no_evening: start_time >= 18:00 (야간) 분반 제외
      - banned_days: 해당 요일에 schedule 있으면 분반 제외
    """
    no_morning = prefs.get('no_morning', False)
    no_evening = prefs.get('no_evening', False)
    banned_days = set(prefs.get('banned_days') or [])

    for sch in offering.schedules.all():
        if no_morning and sch.start_time < MORNING_CUTOFF:
            return False
        if no_evening and sch.start_time >= EVENING_CUTOFF:
            return False
        if sch.day_of_week in banned_days:
            return False
    return True


def filter_offerings_by_prefs(offerings, prefs: dict):
    """offering 리스트를 STEP 4 prefs hard filter로 거른다."""
    return [o for o in offerings if passes_prefs_hard_filter(o, prefs)]
