"""chat AI function calling — OpenAI tools 정의 + dispatcher (spec 5.2 학교 데이터).

AI가 사용자 질문 분석 후 필요하다고 판단하면 아래 tool을 호출한다.
백엔드 dispatcher가 `courses.services`를 직접 호출하고 결과를 JSON으로 직렬화해
다시 AI에 전달, AI가 자연어로 최종 응답 생성.

view 우회 직접 호출 — DRF Request/Response 의존성 없음.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from courses.services import calc_graduation_progress, recommend_next_semester_courses

logger = logging.getLogger(__name__)


# spec 5.3.1 — 다음학기 추천 결과를 AI에 전달할 때 상한 (토큰·응답 길이 가드)
MAX_RECOMMEND_COURSES = 10


# ─── OpenAI tools 스키마 ──────────────────────────────────────────────

TOOLS_SCHEMA = [
    {
        'type': 'function',
        'function': {
            'name': 'get_next_semester_courses',
            'description': (
                '사용자의 다음 학기(또는 지정된 학기) 추천 수강과목 목록을 반환한다. '
                '시간표·시간표 추천·다음 학기 수강·과목 추천 류 질문에서 호출. '
                '명지대 실제 개설 과목·시간·교수 데이터 기반.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'target_year': {
                        'type': 'integer',
                        'description': '추천 대상 학년도 (예: 2026). 미지정 시 사용자 현재 학기 기반 자동 결정.',
                    },
                    'target_semester': {
                        'type': 'integer',
                        'enum': [1, 2],
                        'description': '추천 대상 학기 (1=1학기, 2=2학기). 미지정 시 자동.',
                    },
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_graduation_progress',
            'description': (
                '사용자의 졸업까지 진척도(%)를 반환한다. 졸업·이수율·남은 학기 류 질문에서 호출. '
                '입학연도·졸업희망일 기반 단순 계산이며 학점 비율은 별도(추후 추가).'
            ),
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
]


# ─── dispatcher ───────────────────────────────────────────────────────

def dispatch_tool_call(user, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """tool name + arguments → courses.services 호출 → JSON serializable dict.

    AI가 잘못된 arguments를 보내거나 내부 호출이 실패해도 예외를 raise하지 않고
    `{"error": "..."}` 형태로 반환 — AI가 그 결과를 보고 사용자에게 적절히 안내.
    """
    try:
        if name == 'get_next_semester_courses':
            return _get_next_semester_courses(user, arguments)
        if name == 'get_graduation_progress':
            return _get_graduation_progress(user)
    except Exception as e:
        logger.exception('chat tool dispatch 실패: name=%s args=%s', name, arguments)
        return {'error': f'내부 호출 실패: {e.__class__.__name__}'}
    return {'error': f'알 수 없는 tool: {name}'}


def _get_next_semester_courses(user, args: dict[str, Any]) -> dict[str, Any]:
    target_year = args.get('target_year')
    target_semester = args.get('target_semester')

    results = recommend_next_semester_courses(
        user, target_year=target_year, target_semester=target_semester,
    )
    top = results[:MAX_RECOMMEND_COURSES]

    return {
        'target_year': target_year,
        'target_semester': target_semester,
        'count': len(top),
        'note': (
            '관련도 상위 N개만 반환 (전체 결과 중 일부). '
            'category는 학칙 7분류(전공필수/전공선택/공통교양/핵심교양/'
            '학문기초교양/일반교양/자유선택) 기반.'
        ),
        'courses': [
            {
                'score': score,
                'course_code': c.course_code,
                'name': c.name,
                'category': c.category,
                'credits': c.credits,
                'professor': c.professor,
                'schedules': [
                    {
                        'day_of_week': s.day_of_week,
                        'start_time': s.start_time.isoformat() if s.start_time else None,
                        'end_time': s.end_time.isoformat() if s.end_time else None,
                        'building': s.building,
                        'room': s.room,
                    }
                    for s in c.schedules.all()
                ],
            }
            for score, c in top
        ],
    }


def _get_graduation_progress(user) -> dict[str, Any]:
    progress = calc_graduation_progress(user)
    return {
        'progress_percent': progress,
        'note': '입학년도와 졸업희망일 기반 시간 진척도. 학점 이수율은 별도.',
    }


def serialize_tool_result(result: dict[str, Any]) -> str:
    """dispatch 결과 dict → OpenAI에 보낼 string (JSON).

    한글 그대로 (`ensure_ascii=False`) 보내야 토큰 효율 + AI 이해도 좋음.
    """
    return json.dumps(result, ensure_ascii=False)
