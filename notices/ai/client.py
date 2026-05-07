"""OpenAI 클라이언트 래퍼 (공지 AI 처리용).

settings에서 키/모델/타임아웃/재시도 횟수 로드.
JSON mode 응답 파싱 + 단순 텍스트 응답 둘 다 지원.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings
from openai import OpenAI

logger = logging.getLogger(__name__)


class AIClientError(Exception):
    """LLM 호출/파싱 중 발생한 모든 오류의 베이스."""


class AIResponseParseError(AIClientError):
    """JSON mode 응답이 기대한 스키마와 다를 때."""


_client: OpenAI | None = None


def get_client() -> OpenAI:
    """프로세스 내 단일 OpenAI 클라이언트 인스턴스 반환.

    openai SDK 클라이언트는 내부에 httpx connection pool을 가지므로
    재사용이 비용/속도 면에서 유리.
    """
    global _client
    if _client is None:
        api_key = getattr(settings, 'OPENAI_API_KEY', '')
        if not api_key:
            raise AIClientError(
                'OPENAI_API_KEY 미설정. .env에 OPENAI_API_KEY=... 추가 필요.'
            )
        _client = OpenAI(
            api_key=api_key,
            timeout=settings.OPENAI_REQUEST_TIMEOUT,
            max_retries=settings.OPENAI_MAX_RETRIES,
        )
    return _client


def reset_client() -> None:
    """테스트에서 mock 주입 후 정리하거나, 키 변경 시 재초기화 용도."""
    global _client
    _client = None


def call_text(system: str, user: str, model: str | None = None) -> str:
    """단순 텍스트 응답을 받는 호출 (Stage 2 요약용)."""
    client = get_client()
    response = client.chat.completions.create(
        model=model or settings.OPENAI_MODEL,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
    )
    return (response.choices[0].message.content or '').strip()


def call_json(system: str, user: str, model: str | None = None) -> dict[str, Any]:
    """JSON mode 응답을 dict로 파싱해서 반환 (Stage 1 분류, Stage 3 카드)."""
    client = get_client()
    try:
        response = client.chat.completions.create(
            model=model or settings.OPENAI_MODEL,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
            response_format={'type': 'json_object'},
        )
    except Exception as e:
        raise AIClientError(f'OpenAI 호출 실패: {e}') from e

    raw = (response.choices[0].message.content or '').strip()
    if not raw:
        raise AIResponseParseError('빈 응답')
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise AIResponseParseError(
            f'JSON 파싱 실패: {e}. 응답 앞부분: {raw[:200]!r}'
        ) from e
