"""공모전 테마 상세 추천 서비스 (이슈 #184, spec 5.5 / 5.10.4).

build_contest_guide: 기존 Information(Wevity 크롤링) 위에 관심사 매칭 + fallback을
얹어 테마 상세 화면 한 벌(조언 / 카드 / 우선순위)을 만든다. 추천 로직 신설이 아니라
common.matching 엔진(동의어 확장 + 1점 캡)을 재구성하는 계층.

핵심 정책 (CLAUDE.md 공모전 섹션):
- 노출 최소 3 ~ 최대 5. 매칭分 우선 + 일반分(마감 임박순) backfill. 빈 화면 금지.
- state 3단계: matched(매칭 ≥3) / partial_match(1~2) / no_match(0, 마감일순).
- 매칭 텍스트 = title + organizer. 캡(관심사당 1점)은 matched_keywords가 보장.
"""
from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from common.matching import extract_user_keywords, matched_keywords

from .advice import build_contest_advice, build_contest_quick_questions
from .models import Information

# 노출 개수 경계 (확정 2026-06-02). 후보가 MIN 미만이면 있는 만큼만 — 빈 화면보다 우선.
DISPLAY_MIN = 3
DISPLAY_MAX = 5
# 'D-n 이하'면 마감 임박 배지 부여
DEADLINE_SOON_DAYS = 7
# state 분기 — 매칭 건수 임계
STATE_MATCHED_MIN = 3   # 매칭 ≥3 → matched
# 마감일 없는 항목을 정렬 최후미로 보내는 sentinel
_FAR = 10 ** 9


def _deadline_key(item):
    """마감 임박 정렬 키 — dday 작을수록 위, 마감일 없으면 최후미."""
    return item.dday if item.dday is not None else _FAR


def build_contest_guide(user) -> dict:
    """공모전 테마 상세 응답 한 벌 조립."""
    today = timezone.localdate()

    # 후보: 모집 중(활성 + 마감 안 지남) 공모전
    qs = Information.objects.filter(is_active=True).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    )
    candidates = [c for c in qs if '공모전' in (c.categories or [])]

    # 각 후보에 매칭 관심사 / 점수 / dday 부여 (매칭 텍스트 = title + organizer)
    user_kw = extract_user_keywords(user)
    for c in candidates:
        search_text = f'{c.title} {c.organizer}'
        c.matched_interests = matched_keywords(user_kw, c.categories or [], search_text)
        c.match_score = len(c.matched_interests)
        c.dday = (c.end_date - today).days if c.end_date else None

    # 매칭分: 점수 ↓ → 마감 임박 / 일반分: 마감 임박
    matched_items = sorted(
        (c for c in candidates if c.match_score > 0),
        key=lambda c: (-c.match_score, _deadline_key(c)),
    )
    general_items = sorted(
        (c for c in candidates if c.match_score == 0),
        key=_deadline_key,
    )

    # backfill: 매칭分 우선, 모자라면 일반分으로 상한 5까지 채움
    selected = list(matched_items[:DISPLAY_MAX])
    if len(selected) < DISPLAY_MAX:
        selected += general_items[: DISPLAY_MAX - len(selected)]

    # state — 매칭 '건수' 기준 (노출 개수 아님). 분석/디버깅용
    n_matched = len(matched_items)
    if n_matched >= STATE_MATCHED_MIN:
        state = 'matched'
    elif n_matched >= 1:
        state = 'partial_match'
    else:
        state = 'no_match'

    # 조언 신호: 매칭 관심사 목록(중복 제거, 등장 순) + 가장 먼저 마감되는 매칭 공모전
    interests_overall: list[str] = []
    for c in matched_items:
        for i in c.matched_interests:
            if i not in interests_overall:
                interests_overall.append(i)
    matched_with_dday = [c for c in matched_items if c.dday is not None]
    soonest = min(matched_with_dday, key=lambda c: c.dday) if matched_with_dday else None
    advice = build_contest_advice(
        state,
        interests_overall,
        n_matched,
        soonest.title if soonest else None,
        soonest.dday if soonest else None,
    )

    # 우선순위: 카드 정보 중복 없이 card_id 참조 + 근거 배지(머신코드)
    priority = []
    for rank, c in enumerate(selected, start=1):
        reasons = [
            {'code': 'interest_match', 'meta': {'interest': i}}
            for i in c.matched_interests
        ]
        if c.dday is not None and c.dday <= DEADLINE_SOON_DAYS:
            reasons.append({'code': 'deadline_soon', 'meta': {'dday': c.dday}})
        priority.append({'rank': rank, 'card_id': c.id, 'reasons': reasons})

    return {
        'state': state,
        'advice': advice,
        'cards': selected,            # Information 인스턴스 (dday 속성 부여됨)
        'priority': priority,
        'quick_questions': build_contest_quick_questions(user),  # ⑥ 띵똥이 물어보기 칩
        'note': 'no_match' if state == 'no_match' else None,
    }
