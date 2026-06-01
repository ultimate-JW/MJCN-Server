"""공모전 테마 상세 ② 조언 박스 문구 조립 (이슈 #184, 규칙 기반 · LLM 없음).

신호 추출은 services.build_contest_guide에서, phrasing만 이 모듈에서 담당.
나중에 LLM phrasing으로 갈아끼우려면 build_contest_advice만 교체하면 된다.

문구 2종 (CLAUDE.md 공모전 섹션 C):
  [매칭 있음/일부] 관심사 + 건수 + 가장 먼저 마감되는 공모전
  [매칭 없음]      관심사 일치 없음 안내 + 마감 임박 항목 모음 (실패 아님)
"""
from __future__ import annotations


def _prettify(keyword: str) -> str:
    """배지/문구 표시용 — 순수 ASCII 토큰은 대문자(ai→AI), 한글은 원형 유지."""
    return keyword.upper() if keyword.isascii() else keyword


def build_contest_advice(
    state: str,
    interests: list[str],
    count: int,
    soonest_title: str | None,
    soonest_dday: int | None,
) -> dict:
    """(state, 신호) → 조언 박스 2줄.

    Args:
        state: 'matched' | 'partial_match' | 'no_match'
        interests: 매칭된 관심사(소문자 키워드) 리스트 — 상위 2개만 표시
        count: 매칭된 공모전 수
        soonest_title / soonest_dday: 매칭 공모전 중 가장 먼저 마감되는 항목
    """
    if state == 'no_match':
        # 추천 실패 아님 — 마감 임박 항목을 모아준다는 안내 (관심사 추천이라 표시 X)
        return {
            'line1': '현재 관심사와 직접 일치하는 공모전은 없어요.',
            'line2': '대신 지금 모집 중인 공모전 중 마감이 가까운 항목을 모아봤어요.',
        }

    # matched / partial_match — 관심사 상위 2개를 가운뎃점으로 잇기
    interest_str = '·'.join(_prettify(i) for i in interests[:2]) or '관심'
    line1 = f'{interest_str} 관련 공모전 {count}건을 찾았어요.'
    # 가장 먼저 마감되는 공모전 한 건 콕 집기 (마감일 없으면 둘째 줄 생략)
    if soonest_title and soonest_dday is not None:
        line2 = f"그중 '{soonest_title}'이 D-{soonest_dday}으로 가장 먼저 마감돼요."
    else:
        line2 = ''
    return {'line1': line1, 'line2': line2}
