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
from .tools import TOOLS_SCHEMA, dispatch_tool_call, serialize_tool_result


# tool calling 안전 가드 — 무한 루프 방지. 보통 1~2회면 끝남.
_MAX_TOOL_ROUNDS = 3


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


def generate_assistant_reply(user, history: Iterable[ChatMessage]) -> str:
    """대화 히스토리 + 사용자 컨텍스트 + tool calling으로 assistant 응답 생성 (spec 5.2).

    호출 측이 컨텍스트 윈도우(최근 N개)를 잘라서 넘기는 책임을 진다.

    동작:
      1. system prompt = CHAT_SYSTEM + build_user_context(user)
      2. OpenAI chat completion 호출 (tools=TOOLS_SCHEMA)
      3. 응답이 tool_calls면 dispatch_tool_call로 실행 → messages에 tool 결과 추가 → 재호출
      4. 최대 _MAX_TOOL_ROUNDS회까지 반복 (무한 루프 방어)
      5. 최종 텍스트 응답 반환

    tool 호출은 AI 판단 — 일반 잡담은 tool 안 부르고 일반 응답만 함.
    "시간표 짜줘"·"몇 학기 남았어" 류의 데이터 의존 질문에서만 tool 호출.
    """
    system_parts = [prompts.CHAT_SYSTEM]
    user_context = prompts.build_user_context(user)
    if user_context:
        system_parts.append(user_context)
    api_messages: list = [{'role': 'system', 'content': '\n\n'.join(system_parts)}]
    for m in history:
        api_messages.append({'role': m.role, 'content': m.content})

    client = get_client()

    for _ in range(_MAX_TOOL_ROUNDS):
        try:
            response = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=api_messages,
                tools=TOOLS_SCHEMA,
            )
        except Exception as e:
            raise AIClientError(f'OpenAI 호출 실패 (generate_assistant_reply): {e}') from e

        msg = response.choices[0].message
        tool_calls = getattr(msg, 'tool_calls', None) or []

        if not tool_calls:
            # 일반 응답 — tool 안 부름. 최종 자연어 응답으로 반환.
            return (msg.content or '').strip()

        # tool calls 있음 → assistant tool_call 메시지 + 각 tool 결과를 history에 append
        api_messages.append(_assistant_tool_call_message(msg, tool_calls))
        for call in tool_calls:
            name = call.function.name
            try:
                arguments = json.loads(call.function.arguments or '{}')
            except json.JSONDecodeError:
                arguments = {}
            result = dispatch_tool_call(user, name, arguments)
            api_messages.append({
                'role': 'tool',
                'tool_call_id': call.id,
                'content': serialize_tool_result(result),
            })

    # _MAX_TOOL_ROUNDS 초과 — 마지막으로 tool 없이 한 번 더 호출해 자연어 응답 강제
    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=api_messages,
        )
    except Exception as e:
        raise AIClientError(f'OpenAI 호출 실패 (final answer): {e}') from e
    return (response.choices[0].message.content or '').strip()


def _assistant_tool_call_message(msg, tool_calls) -> dict:
    """OpenAI 응답의 assistant message(tool_calls 포함)를 messages 배열용 dict로 직렬화."""
    return {
        'role': 'assistant',
        'content': msg.content or '',
        'tool_calls': [
            {
                'id': c.id,
                'type': 'function',
                'function': {
                    'name': c.function.name,
                    'arguments': c.function.arguments or '{}',
                },
            }
            for c in tool_calls
        ],
    }
