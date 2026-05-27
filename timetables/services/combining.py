"""5.3.6 STEP 5 — 시간표 조합 DFS 생성.

입력: 분반 후보 리스트 (이미 STEP 4 prefs hard filter 통과)
출력: 시간 충돌 없는 + max_credits 이하 조합 (offering 튜플) generator

알고리즘:
1. 같은 Course의 분반들은 그룹화 (DFS 한 레벨에서 OR 선택 — 둘 다 동시 선택 차단)
2. course 단위로 DFS — 각 단계에서 (a) 이 course 안 듣기 (b) 분반 중 하나 듣기
3. 가지치기: 학점 초과 / 충돌 발생 시 즉시 중단 (hard)
4. min_credits는 soft (점수 패널티) — DFS에선 안 거름 (#97 결정 3)
"""
from collections import defaultdict

from timetables.utils.conflict import is_compatible


def group_offerings_by_course(offerings):
    """같은 Course의 offering들을 묶음. 키 = course_id, 값 = offering 리스트."""
    groups = defaultdict(list)
    for o in offerings:
        groups[o.course_id].append(o)
    return list(groups.values())


def generate_combinations(offerings, max_credits: int):
    """STEP 5: 충돌 없는 + max_credits 이하 시간표 조합 generator.

    yield 단위: tuple[CourseOffering, ...] — 빈 조합은 yield 안 함.
    한 Course의 두 분반이 같은 조합에 동시 등장하는 일 없음 (그룹 단위 OR 선택).
    """
    course_groups = group_offerings_by_course(offerings)
    n = len(course_groups)

    def dfs(idx, chosen, acc_bitmap, acc_credits):
        if idx == n:
            if chosen:
                yield tuple(chosen)
            return

        # (a) 이 course 안 듣기 — 다음 course로
        yield from dfs(idx + 1, chosen, acc_bitmap, acc_credits)

        # (b) 이 course의 분반 중 하나 선택
        for offering in course_groups[idx]:
            cost = offering.course.credits
            if acc_credits + cost > max_credits:
                continue   # 학점 초과 가지치기
            if not is_compatible(acc_bitmap, offering.time_bitmap):
                continue   # 충돌 가지치기
            chosen.append(offering)
            yield from dfs(
                idx + 1, chosen,
                acc_bitmap | offering.time_bitmap,
                acc_credits + cost,
            )
            chosen.pop()

    yield from dfs(0, [], 0, 0)
