"""3단계 LLM 파이프라인 함수 (spec 9.1.1).

각 함수는 한 단계만 책임진다.
- 호출 실패는 AIClientError 그대로 전파 (오케스트레이터에서 처리)
- 응답 검증 실패는 AIResponseParseError로 통일

실행 순서 (spec 9.1.1):
  Stage 1: summarize (spec 9.1.2)
  Stage 2: classify  (spec 9.1.3)
  Stage 3: build_cards (spec 9.1.4 — 행동형/정보형 공통 프롬프트)
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from .client import AIResponseParseError, call_json, call_text
from .prompts import (
    BUILD_CARDS_SYSTEM,
    CLASSIFY_SYSTEM,
    SUMMARIZE_SYSTEM,
    build_user_message,
)

logger = logging.getLogger(__name__)

VALID_TYPES = {'정보형', '행동형'}


def truncate_content(content: str, max_chars: int | None = None) -> str:
    """LLM 입력용 본문 잘라내기 (단순 앞부분 절단).

    spec 9.1.1: '본문 길이 처리: truncate'
    """
    limit = max_chars if max_chars is not None else settings.OPENAI_NOTICE_CONTENT_MAX_CHARS
    if len(content) <= limit:
        return content
    return content[:limit]


# --- Stage 1 ---

def summarize(content: str) -> str:
    """공지 본문 → 100자 이내 한 문장 요약."""
    truncated = truncate_content(content)
    summary = call_text(SUMMARIZE_SYSTEM, truncated)
    if not summary:
        raise AIResponseParseError('요약이 비어있음')
    # 100자 초과해도 모델이 가끔 넘기는 경우 있음 → DB 컬럼은 200자 여유
    return summary


# --- Stage 2 ---

def classify(content: str) -> str:
    """공지 본문 → '정보형' or '행동형'."""
    truncated = truncate_content(content)
    data = call_json(CLASSIFY_SYSTEM, truncated)
    notice_type = data.get('type')
    if notice_type not in VALID_TYPES:
        raise AIResponseParseError(
            f"분류 결과가 유효하지 않음: {notice_type!r}"
        )
    return notice_type


# --- Stage 3 ---

def build_cards(content: str, notice_type: str) -> list[dict[str, Any]]:
    """공지 본문 + 유형 → cards 리스트 (title + items).

    행동형/정보형 모두 공통 프롬프트(BUILD_CARDS_SYSTEM)를 사용한다.
    {type} 변수는 user_message로 전달되어 프롬프트 내부의 [유형별 규칙]이
    분기되어 적용됨 (행동형은 "🚨 지금 해야 할 행동" 카드 우선 배치).
    """
    if notice_type not in VALID_TYPES:
        raise ValueError(f'invalid notice_type: {notice_type!r}')

    truncated = truncate_content(content)
    user_message = build_user_message(notice_type, truncated)
    data = call_json(BUILD_CARDS_SYSTEM, user_message)

    cards = data.get('cards')
    if not isinstance(cards, list):
        raise AIResponseParseError(f'cards가 list가 아님: {type(cards).__name__}')

    cleaned: list[dict[str, Any]] = []
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            raise AIResponseParseError(f'cards[{i}]가 dict가 아님')
        title = card.get('title')
        items = card.get('items')
        if not isinstance(title, str) or not title.strip():
            raise AIResponseParseError(f'cards[{i}].title이 비어있거나 문자열이 아님')
        if not isinstance(items, list) or not all(isinstance(it, str) for it in items):
            raise AIResponseParseError(f'cards[{i}].items가 string list가 아님')
        cleaned.append({'title': title.strip(), 'items': [it.strip() for it in items]})

    if not cleaned:
        raise AIResponseParseError('cards가 비어있음')
    return cleaned
