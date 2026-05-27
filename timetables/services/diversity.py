"""5.3.6 STEP 7 — Jaccard greedy 다양성 dedup + top-N 선정.

두 시간표 과목 집합 Jaccard 유사도가 임계값(JACCARD_THRESHOLD) 이상이면 중복 취급.
점수 DESC 정렬 후 greedy — 다음 후보가 이미 선택된 모두와 유사도 < 임계값일 때만 채택.

임계값 0.7 의미 (6과목 시간표 기준):
- 1과목만 다름 = 5/7 ≈ 0.714 → 중복 (제외)
- 2과목 다름 = 4/8 = 0.5 → 별개 (인정)

#97 결정 8 — 코드 상수, 시드 돌려보고 튜닝.
"""
JACCARD_THRESHOLD = 0.7


def jaccard(a: set, b: set) -> float:
    """집합 A, B의 Jaccard 유사도. 둘 다 빈집합이면 0.0."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def select_diverse_top(scored_combos, n: int = 3, threshold: float = JACCARD_THRESHOLD):
    """점수 DESC 정렬된 시퀀스에서 다양성 dedup 후 상위 N 선택.

    Args:
        scored_combos: iterable of (score, combo, *extras)
            combo는 tuple[CourseOffering, ...]. 첫 원소(score)와 둘째 원소(combo)만 사용.
            점수 기준 DESC 정렬되어 있다고 가정 (caller 책임).
        n: 반환 개수 상한 (#97 결정 = 3)
        threshold: Jaccard 유사도가 이 값 이상이면 중복 취급

    Returns:
        선택된 item 리스트. 최대 n개. 다양성 통과 후 n 미만이면 그만큼만 반환.
        (fallback `low_diversity_pool` note는 caller가 결과 길이 보고 처리.)
    """
    selected = []
    selected_course_sets = []
    for item in scored_combos:
        combo = item[1]
        course_set = {o.course_id for o in combo}
        if all(jaccard(course_set, s) < threshold for s in selected_course_sets):
            selected.append(item)
            selected_course_sets.append(course_set)
            if len(selected) == n:
                break
    return selected
