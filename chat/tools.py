"""chat AI function calling — OpenAI tools 정의 + dispatcher (spec 5.2 학교 데이터).

AI가 사용자 질문 분석 후 필요하다고 판단하면 아래 tool을 호출한다.
백엔드 dispatcher가 `courses.services`를 직접 호출하고 결과를 JSON으로 직렬화해
다시 AI에 전달, AI가 자연어로 최종 응답 생성.

view 우회 직접 호출 — DRF Request/Response 의존성 없음.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from django.db.models import Q

from courses.services import calc_graduation_progress, recommend_next_semester_courses
from information.models import Information
from notices.models import Notice

logger = logging.getLogger(__name__)


# spec 5.3.1 — 다음학기 추천 결과를 AI에 전달할 때 상한 (토큰·응답 길이 가드)
MAX_RECOMMEND_COURSES = 10
# Step 3 — Notice/Information 검색 결과 상한
MAX_SEARCH_RESULTS = 5


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
    {
        'type': 'function',
        'function': {
            'name': 'search_notices',
            'description': (
                '명지대 공지사항을 키워드로 검색해 상위 N건을 반환. '
                '장학금·등록금·수강신청·학사·일반·행사·진로·학생활동 등 학교 공지 관련 질문에서 호출. '
                'title·tags·content를 키워드 매칭한다.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': '검색 키워드 (예: "장학금", "수강신청 일정").',
                    },
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_information',
            'description': (
                '교내외 정보(공모전·대외활동·부트캠프·지원사업 등)를 키워드로 검색해 '
                '아직 마감되지 않은 상위 N건을 반환. 공모전·대외활동·지원사업 류 질문에서 호출. '
                'title·categories를 키워드 매칭한다.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': '검색 키워드 (예: "공모전", "디자인 대외활동").',
                    },
                },
                'required': ['query'],
            },
        },
    },
]


# ─── dispatcher ───────────────────────────────────────────────────────

def dispatch_tool_call(user, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """tool name + arguments → 서비스 함수/DB 조회 호출 → JSON serializable dict.

    AI가 잘못된 arguments를 보내거나 내부 호출이 실패해도 예외를 raise하지 않고
    `{"error": "..."}` 형태로 반환 — AI가 그 결과를 보고 사용자에게 적절히 안내.
    """
    try:
        if name == 'get_next_semester_courses':
            return _get_next_semester_courses(user, arguments)
        if name == 'get_graduation_progress':
            return _get_graduation_progress(user)
        if name == 'search_notices':
            return _search_notices(arguments)
        if name == 'search_information':
            return _search_information(arguments)
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


# ─── Step 3: Notice / Information 검색 ────────────────────────────────

_TOKEN_RE = re.compile(r'[\s,./?!()\[\]{}\-_:;\'"]+')


def _tokenize_query(query: str) -> list[str]:
    """쿼리를 공백·구두점으로 단순 분해. 2자 이상만 유지."""
    if not query:
        return []
    tokens = [t.strip().lower() for t in _TOKEN_RE.split(query)]
    return [t for t in tokens if len(t) >= 2]


def _search_notices(args: dict[str, Any]) -> dict[str, Any]:
    query = (args.get('query') or '').strip()
    tokens = _tokenize_query(query)
    if not tokens:
        return {'count': 0, 'results': [], 'note': '쿼리가 비어있음'}

    # title icontains (전체 토큰 OR) + tags 매칭
    title_q = Q()
    for t in tokens:
        title_q |= Q(title__icontains=t)

    qs = (
        Notice.objects
        .filter(title_q)
        .order_by('-published_at')[:MAX_SEARCH_RESULTS * 3]  # 후처리 정렬 위해 넉넉히
    )
    scored = []
    token_set = set(tokens)
    for notice in qs:
        tags_set = {t.lower() for t in (notice.tags or [])}
        tag_hits = len(token_set & tags_set)
        title_hits = sum(1 for t in tokens if t in (notice.title or '').lower())
        scored.append((tag_hits * 2 + title_hits, notice))

    scored.sort(key=lambda x: (-x[0], -x[1].published_at.timestamp()))
    top = [n for s, n in scored[:MAX_SEARCH_RESULTS]]

    return {
        'count': len(top),
        'query': query,
        'note': '명지대 공지 검색 결과. URL은 원문 링크.',
        'results': [
            {
                'title': n.title,
                'url': n.url,
                'source': n.source,
                'published_at': n.published_at.isoformat() if n.published_at else None,
                'end_date': n.end_date.isoformat() if n.end_date else None,
                'tags': list(n.tags or []),
            }
            for n in top
        ],
    }


def _search_information(args: dict[str, Any]) -> dict[str, Any]:
    query = (args.get('query') or '').strip()
    tokens = _tokenize_query(query)
    if not tokens:
        return {'count': 0, 'results': [], 'note': '쿼리가 비어있음'}

    title_q = Q()
    for t in tokens:
        title_q |= Q(title__icontains=t)

    # 만료된 정보는 제외 (end_date 지남)
    today = date.today()
    qs = (
        Information.objects
        .filter(title_q)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .order_by('end_date', '-id')[:MAX_SEARCH_RESULTS * 3]
    )

    scored = []
    token_set = set(tokens)
    for info in qs:
        cats_set = {c.lower() for c in (info.categories or [])}
        cat_hits = len(token_set & cats_set)
        title_hits = sum(1 for t in tokens if t in (info.title or '').lower())
        scored.append((cat_hits * 2 + title_hits, info))

    # 점수 DESC, 마감 임박 우선
    scored.sort(key=lambda x: (
        -x[0],
        x[1].end_date or date.max,
    ))
    top = [i for s, i in scored[:MAX_SEARCH_RESULTS]]

    return {
        'count': len(top),
        'query': query,
        'note': '교내외 정보 검색 결과 (마감 미경과만). URL은 원문 링크.',
        'results': [
            {
                'title': i.title,
                'url': getattr(i, 'url', '') or '',
                'categories': list(i.categories or []),
                'end_date': i.end_date.isoformat() if i.end_date else None,
            }
            for i in top
        ],
    }
