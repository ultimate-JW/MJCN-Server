"""시간표 조합 충돌 검사 유틸 (#97 5.3.6).

`CourseOffering.time_bitmap`이 단일 분반의 5요일×26 slot bitmap을 반환한다 (courses/models.py).
여기서는 여러 bitmap을 합성·검사하는 combination-level 헬퍼만 모은다.

DFS 패턴 (combining.py에서 사용):
    acc = 0
    for offering in candidates:
        if is_compatible(acc, offering.time_bitmap):
            acc |= offering.time_bitmap      # push
            recurse(...)
            acc ^= offering.time_bitmap      # pop (XOR — OR로 추가한 비트만 정확히 제거)
"""


def is_compatible(acc_bitmap: int, candidate_bitmap: int) -> bool:
    """누적 bitmap과 새 후보 bitmap이 시간 충돌 없는지."""
    return (acc_bitmap & candidate_bitmap) == 0


def has_any_conflict(bitmaps) -> bool:
    """bitmap int 시퀀스 안에 시간 충돌이 있는지. n번 비교로 O(n)."""
    acc = 0
    for b in bitmaps:
        if (acc & b) != 0:
            return True
        acc |= b
    return False


def merge_bitmaps(bitmaps) -> int:
    """여러 bitmap을 OR로 합쳐 한 정수로. 조합 누적 상태 계산용."""
    acc = 0
    for b in bitmaps:
        acc |= b
    return acc
