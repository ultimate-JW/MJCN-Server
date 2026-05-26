"""chat 앱 AI 서비스 레이어 (spec 5.2).

- classify_and_title: 첫 메시지 1회로 (title, category) 동시 생성
- generate_assistant_reply: 멀티턴 대화 응답 (컨텍스트 = 최근 N개 메시지)

notices/ai/client의 get_client 싱글톤만 재사용. notices의 call_text는 단일
user 입력만 받는 형태라 멀티턴 대화에는 부적합.
"""

import json
from typing import Iterable

from django.conf import settings

from notices.ai.client import AIClientError, AIResponseParseError, get_client

from . import prompts
from .models import CHAT_CATEGORIES, ChatMessage


_VALID_CATEGORIES = {value for value, _ in CHAT_CATEGORIES}


def classify_and_title(first_user_message: str) -> tuple[str, str]:
    """첫 메시지 → (title, category). OpenAI JSON mode 1회 호출."""
    client = get_client()
    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {'role': 'system', 'content': prompts.TITLE_CATEGORY_SYSTEM},
                {'role': 'user', 'content': first_user_message},
            ],
            response_format={'type': 'json_object'},
        )
    except Exception as e:
        raise AIClientError(f'OpenAI 호출 실패 (classify_and_title): {e}') from e

    raw = (response.choices[0].message.content or '').strip()
    if not raw:
        raise AIResponseParseError('classify_and_title: 빈 응답')

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AIResponseParseError(
            f'classify_and_title: JSON 파싱 실패: {e}. 응답 앞부분: {raw[:200]!r}'
        ) from e

    title = (data.get('title') or '').strip()[:100]
    category = (data.get('category') or '기타').strip()
    if category not in _VALID_CATEGORIES:
        category = '기타'
    return title, category


def generate_assistant_reply(history: Iterable[ChatMessage]) -> str:
    """대화 히스토리(시간 순) → assistant 응답 텍스트.

    호출 측이 컨텍스트 윈도우(최근 N개)를 잘라서 넘기는 책임을 진다.
    """
    api_messages = [{'role': 'system', 'content': prompts.CHAT_SYSTEM}]
    for m in history:
        api_messages.append({'role': m.role, 'content': m.content})

    client = get_client()
    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=api_messages,
        )
    except Exception as e:
        raise AIClientError(f'OpenAI 호출 실패 (generate_assistant_reply): {e}') from e

    return (response.choices[0].message.content or '').strip()
