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

from courses.services import (
    MAX_NEXT_SEMESTER_RECOMMENDATIONS,
    _curriculum_first_slot,
    _resolve_offering_term,
    build_completion_status,
    calc_graduation_progress,
    recommend_next_semester_courses,
)
from information.models import Information
from notices.models import Notice

logger = logging.getLogger(__name__)


# spec 5.3.1 — 다음학기 추천 결과를 AI에 전달할 때 상한 (토큰·응답 길이 가드).
# themes 상세 추천과 같은 풀에서 뽑도록 courses 엔진의 canonical 상수를 공유 (#164).
MAX_RECOMMEND_COURSES = MAX_NEXT_SEMESTER_RECOMMENDATIONS
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
                '사용자의 졸업까지 **시간(날짜)** 진척도(%)를 반환한다. '
                '"졸업까지 얼마나 남았어?" 처럼 입학~졸업희망일 사이 경과 비율을 묻는 질문에서만 호출. '
                '학점 이수 현황(전공 몇 학점 등)은 이 tool이 아니라 get_completion_status를 쓴다.'
            ),
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_completion_status',
            'description': (
                '사용자의 졸업요건 대비 **학점 이수현황**을 반환한다. '
                '카테고리별(전공필수/전공선택/공통교양/핵심교양/학문기초교양/일반교양/자유선택) '
                '이수·필요·잔여 학점 + 채플 회수 + 총 이수/필요/잔여 학점. '
                '"내 이수현황", "전공 몇 학점 들었어", "졸업까지 학점 얼마나 남았어", '
                '"필수전공 다 들었나" 류 질문에서 호출. '
                '날짜 기준 진척도(%)는 get_graduation_progress로 별도.'
            ),
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_course_history',
            'description': (
                '사용자가 **이미 수강한(들은) 과목 목록**을 연도·학기 순으로 반환한다. '
                '과목명·과목번호·이수구분·학점·취득성적 포함. '
                '"내가 들은 과목", "수강한 과목 보여줘", "지금까지 들은 거", "전에 뭐 들었지" 류 '
                '**과거 수강이력** 질문에서 호출. '
                '다음 학기 추천(get_next_semester_courses)·학점 집계(get_completion_status)와 구분 — '
                '이건 실제로 들은 과목 "목록"이다. '
                '(현재 수강 중인 과목은 이 tool 범위 밖이다.)'
            ),
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_notices',
            'description': (
                '명지대학교 **교내(자체)** 공지사항을 키워드로 검색해 상위 N건을 반환한다. '
                '데이터 출처는 명지대 학사·일반·행사·장학·진로·학생활동 등 공식 게시판이다. '
                '장학금·등록금·수강신청·학사 일정·교내 행사·교내 공모전 등 '
                '명지대 안에서 발생하는 일에 대한 질문이면 이 tool을 호출한다. '
                'title·tags·content를 키워드 매칭한다.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': '검색 키워드 (예: "장학금", "수강신청 일정", "교내 공모전").',
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
                '**교외(외부)** 공모전·대외활동·부트캠프·지원사업 정보를 키워드로 검색해 '
                '아직 마감되지 않은 상위 N건을 반환한다. 데이터 출처는 wevity 등 외부 공모전 '
                '사이트 크롤링으로, 명지대 자체 행사·공지는 여기 포함되지 않는다. '
                '학교 밖 공모전·대외활동·인턴십 류 질문에만 호출한다. '
                '"교내" "학교 안" "명지대" 같은 의도가 명시되면 이 tool 대신 search_notices를 사용한다. '
                'title·categories를 키워드 매칭한다.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': '검색 키워드 (예: "공모전", "디자인 대외활동", "스타트업 지원사업").',
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
        if name == 'get_completion_status':
            return _get_completion_status(user)
        if name == 'get_course_history':
            return _get_course_history(user)
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

    # 학기 미지정 시 자동 결정 (5.3.1과 동일). recommend_next_semester_courses 내부도
    # 같은 보충을 하지만, fallback 판정·응답 명시를 위해 여기서 먼저 학기를 확정한다.
    if target_year is None or target_semester is None:
        auto_year, auto_sem = _curriculum_first_slot(user)
        if target_year is None:
            target_year = auto_year
        if target_semester is None:
            target_semester = auto_sem

    # 사용자 관점의 '다음 학기'는 fallback 전 학기(예: 2026-2)다. _resolve_offering_term이
    # target_year/semester를 fallback 값(2025-2)으로 덮어쓰기 때문에, 덮어쓰기 전에 보존한다.
    # 이게 빠져서 AI가 fallback 학기(2025-2)를 '다음 학기'로 잘못 안내하던 버그가 있었다 (#205).
    requested_year, requested_semester = target_year, target_semester

    # 다음 학기 개설 정보가 아직 없으면 작년 같은 학기로 fallback (#193 — 섹션 경로와 동일).
    # 챗 경로엔 그동안 이게 빠져 빈 추천만 나왔음. fallback 학기로 실제 과목을 주고,
    # AI가 "다음 학기는 ○-○지만 ○-○ 기준" 안내를 하도록 note에 명시한다.
    target_year, target_semester, is_fallback_term = _resolve_offering_term(
        target_year, target_semester,
    )

    results = recommend_next_semester_courses(
        user, target_year=target_year, target_semester=target_semester,
    )
    top = results[:MAX_RECOMMEND_COURSES]

    note = (
        '관련도 상위 N개만 반환 (전체 결과 중 일부, score 내림차순 = 추천 우선순위). '
        'category는 학칙 7분류(전공필수/전공선택/공통교양/핵심교양/'
        '학문기초교양/일반교양/자유선택) 기반. '
        'reasons는 추천 이유 코드 — major_required(전공필수), '
        'designated_required(졸업 필수 교양 영역: 공통/핵심/학문기초), '
        'category_short(졸업요건상 부족한 영역 보완), interest_match(관심분야 매칭), '
        'grade_semester_match(권장 학년·학기 과목), backlog_required(아직 안 들은 밀린 필수). '
        '이 코드를 자연어로 풀어 과목마다 추천 이유를 설명할 것. '
        '각 과목의 offerings는 교수·시간이 다른 분반(section_no) 목록임. '
        '시간표를 짤 때는 한 과목당 분반 하나만 골라 그 분반의 schedules만 사용할 것. '
        '서로 다른 분반의 시간을 한 과목으로 섞으면 실재하지 않는 시간표가 됨. '
        'status는 사용자의 현재 졸업요건 이수현황(카테고리별 잔여 학점·채플·총 잔여)임. '
        '과목을 나열하기 전에 status를 근거로 현재 상황을 먼저 1~2줄 브리핑할 것 '
        '(예: 학과·학년·학기, 이번 학기 제안 학점 합, 전공/교양 잔여 핵심). '
        'status에 있는 값만 말하고 없는 수치는 지어내지 말 것.'
    )
    # fallback이면 AI가 사용자에게 기준 학기를 안내하도록 지시 문구를 덧붙임.
    # 핵심: 사용자의 '다음 학기'는 requested(2026-2)이고, 추천 데이터는 target(2025-2)이다.
    # 둘을 반드시 구분해 안내하게 한다 — fallback 학기를 '다음 학기'로 표기하면 안 됨 (#205).
    if is_fallback_term:
        note += (
            f' 단, 사용자의 다음 학기는 {requested_year}-{requested_semester}학기인데 '
            f'아직 수강신청/개설 정보가 올라오지 않았다. 그래서 작년 같은 학기인 '
            f'{target_year}-{target_semester}학기 개설 과목을 기준으로 추천한 것이다. '
            f'사용자에게 "다음 학기({requested_year}-{requested_semester})는 아직 개설 정보가 없어 '
            f'{target_year}-{target_semester}학기 기준으로 추천한다"는 점을 반드시 명확히 안내할 것. '
            f'{target_year}-{target_semester}학기를 "다음 학기"라고 부르지 말 것.'
        )

    return {
        # 사용자 관점의 다음 학기 (fallback 전). AI가 "다음 학기" 표기에 이 값을 쓰게 함.
        'requested_year': requested_year,
        'requested_semester': requested_semester,
        # 추천 데이터의 실제 출처 학기 (fallback 시 작년 같은 학기).
        'target_year': target_year,
        'target_semester': target_semester,
        'fallback_term': is_fallback_term,
        'count': len(top),
        'note': note,
        # 현재 졸업요건 이수현황 — AI가 추천 나열 전 "현재 상황"을 브리핑하도록 동봉 (#브리핑).
        # 추천 풀과 같은 응답에 실어, 별도 tool 호출 없이도 브리핑이 항상 가능하게 함.
        'status': _compact_status(user),
        'courses': [
            {
                'score': score,
                # 추천 이유 머신 코드 (#202) — recommend_next_semester_courses가 course에 부착.
                # AI가 이 코드를 자연어로 풀어 "왜 추천됐는지" 설명 (점수·순위와 무관, 표시용).
                'reasons': getattr(c, 'recommend_reasons', []),
                'course_code': c.course_code,
                'name': c.name,
                'category': c.category,
                'credits': c.credits,
                # 분반(Offering) 단위로 묶는다 — 한 과목엔 교수·시간이 다른 여러 분반이 있고
                # 학생은 그중 하나만 수강한다. course 단위로 schedule을 평탄화하면(c.schedules.all())
                # 서로 다른 분반의 시간이 한 과목으로 섞여, 실재하지 않는 시간표가 나온다 (#199).
                # offering 경계를 유지해 AI가 분반 하나를 통째로 고르게 한다.
                'offerings': [
                    {
                        'section_no': o.section_no,        # 강좌번호 — 학기 안 분반 유일 식별자
                        'professor': o.professor,          # 교수는 분반 속성 (Course.professor 아님)
                        'schedules': [
                            {
                                'day_of_week': s.day_of_week,
                                'start_time': s.start_time.isoformat() if s.start_time else None,
                                'end_time': s.end_time.isoformat() if s.end_time else None,
                                'building': s.building,
                                'room': s.room,
                            }
                            for s in o.schedules.all()
                        ],
                    }
                    # services가 target 학기로 필터·prefetch한 offerings (추가 쿼리 없음)
                    for o in c.offerings.all()
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


def _compact_status(user) -> dict[str, Any]:
    """build_completion_status 결과를 추천 응답에 끼울 슬림 버전으로 축약.

    추천 tool은 과목 풀까지 함께 실어 토큰 압박이 있으므로, 브리핑에 필요한 핵심만 남긴다 —
    카테고리별 (completed/required/remaining) + 총계 + 채플. 영역(areas)·필수과목(required_courses)
    상세는 제외 (그 디테일은 독립 tool get_completion_status에서 full로 제공).
    """
    full = build_completion_status(user)
    return {
        'categories': [
            {
                'category': c['category'],
                'completed': c['completed'],
                'required': c['required'],
                'remaining': c['remaining'],
            }
            for c in full['categories']
        ],
        'chapel': full['chapel'],
        'total_completed': full['total_completed'],
        'total_required': full['total_required'],
        'total_remaining': full['total_remaining'],
    }


def _get_completion_status(user) -> dict[str, Any]:
    result = build_completion_status(user)
    result['note'] = (
        '졸업요건 대비 학점 이수현황 (학칙 7분류). categories[].remaining이 남은 학점, '
        'areas는 공통/핵심교양 영역별 분해, required_courses는 전공필수/학문기초 필수과목 이수 여부. '
        'chapel은 채플 이수 회수. 사용자에게 잔여 위주로 간결히 안내할 것.'
    )
    return result


def _get_course_history(user) -> dict[str, Any]:
    """사용자가 이미 수강한(들은) 과목 목록 — CourseHistory를 연도·학기순으로 반환 (#211).

    "내가 들은 과목" 류 질문 전용. get_completion_status는 카테고리별 집계 학점만 주고
    과목 '목록'은 안 주므로, 실제 이수 과목을 나열할 데이터가 챗에 없었다 — LLM이 추천/현재
    과목을 '들은 과목'으로 오라벨하던 원인. 현재 수강 중(CurrentCourse)은 범위 밖 (후속).
    """
    from accounts.models import CourseHistory

    rows = (
        CourseHistory.objects
        .filter(user=user)
        .order_by('year', 'semester', 'course_code')
    )
    courses = [
        {
            'course_name': h.course_name,
            'course_code': h.course_code,
            'year': h.year,
            'semester': h.semester,        # 1=1학기 / 2=2학기
            'category': h.category,        # 학칙 7분류 이수구분
            'credits': h.credits,
            'grade_received': h.grade_received or None,  # 성적 미입력이면 None
        }
        for h in rows
    ]
    return {
        'count': len(courses),
        'note': (
            '사용자가 이미 수강 완료한(들은) 과목 목록 — 연도·학기 오름차순. '
            'semester는 1=1학기 / 2=2학기. 추천·현재 수강 과목이 아니라 과거 이수 과목임. '
            '0건이면 등록된 수강이력이 없다고 안내할 것. 데이터에 없는 과목·성적은 지어내지 말 것.'
        ),
        'courses': courses,
    }


def serialize_tool_result(result: dict[str, Any]) -> str:
    """dispatch 결과 dict → OpenAI에 보낼 string (JSON).

    한글 그대로 (`ensure_ascii=False`) 보내야 토큰 효율 + AI 이해도 좋음.
    """
    return json.dumps(result, ensure_ascii=False)


# ─── Step 3: Notice / Information 검색 ────────────────────────────────

_TOKEN_RE = re.compile(r'[\s,./?!()\[\]{}\-_:;\'"]+')

# "새로 뜬 공지 있어?" 같은 의도(intent) 질문에서 키워드 없이 떠도는 일반어 (#131).
# title icontains 매칭에 기여 안 하고 의미만 흐리므로 토큰화 단계에서 제거 →
# 토큰 0개로 떨어지면 search 함수가 최신 N건 fallback으로 분기.
# 학교 게시판의 카테고리 자체(공지/정보/안내)는 title에 거의 안 들어가서 stopword에 포함.
_STOPWORDS = frozenset({
    '공지', '정보', '안내', '관련',
    '있어', '있나', '있는', '있을', '있다',
    '뜬', '뜨는', '나온', '나오는', '나왔',
    '최근', '오늘', '요즘', '이번',
    '새로', '신규',
    '알려', '알려줘', '보여', '보여줘', '찾아', '찾아줘', '확인', '검색',
    '주세요', '줄래',
    '뭐가', '뭔가', '어떤',
})


def _tokenize_query(query: str) -> list[str]:
    """쿼리를 공백·구두점으로 단순 분해. 2자 이상 + stopword 제외만 유지 (#131)."""
    if not query:
        return []
    tokens = [t.strip().lower() for t in _TOKEN_RE.split(query)]
    return [t for t in tokens if len(t) >= 2 and t not in _STOPWORDS]


def _notice_to_payload(notice: Notice) -> dict[str, Any]:
    return {
        'title': notice.title,
        'url': notice.url,
        'source': notice.source,
        'published_at': notice.published_at.isoformat() if notice.published_at else None,
        'end_date': notice.end_date.isoformat() if notice.end_date else None,
        'tags': list(notice.tags or []),
    }


def _recent_notices_fallback(query: str) -> dict[str, Any]:
    """키워드 없는 의도 질문 fallback — 최신 공지 N건 반환 (#131).

    "새로 뜬 공지 있어?" 처럼 stopword만 들어와 토큰이 빈 케이스에서 0건 응답 대신
    최신 published_at 정렬 N건을 제공. 검색 의도가 모호한 사용자에게도 무언가는 보여줌.
    """
    qs = Notice.objects.order_by('-published_at')[:MAX_SEARCH_RESULTS]
    return {
        'count': qs.count(),
        'query': query,
        'note': '키워드 없는 의도 질문 — 최신 공지 N건 (게시일 내림차순).',
        'results': [_notice_to_payload(n) for n in qs],
    }


def _search_notices(args: dict[str, Any]) -> dict[str, Any]:
    query = (args.get('query') or '').strip()
    tokens = _tokenize_query(query)
    if not tokens:
        # "새로 뜬 공지 있어?" 같이 의미 있는 키워드 없는 의도 질문 — 최신 N건 fallback (#131)
        return _recent_notices_fallback(query)

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
        'results': [_notice_to_payload(n) for n in top],
    }


def _information_to_payload(info: Information) -> dict[str, Any]:
    return {
        'title': info.title,
        'url': getattr(info, 'url', '') or '',
        'categories': list(info.categories or []),
        'end_date': info.end_date.isoformat() if info.end_date else None,
    }


def _recent_information_fallback(query: str) -> dict[str, Any]:
    """키워드 없는 의도 질문 fallback — 마감 미경과 임박 N건 반환 (#131)."""
    today = date.today()
    qs = (
        Information.objects
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .order_by('end_date', '-id')[:MAX_SEARCH_RESULTS]
    )
    return {
        'count': qs.count(),
        'query': query,
        'note': '키워드 없는 의도 질문 — 마감 미경과 정보 N건 (마감 임박순).',
        'results': [_information_to_payload(i) for i in qs],
    }


def _search_information(args: dict[str, Any]) -> dict[str, Any]:
    query = (args.get('query') or '').strip()
    tokens = _tokenize_query(query)
    if not tokens:
        # 의도 질문 — 마감 임박 N건 fallback (#131)
        return _recent_information_fallback(query)

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
        'results': [_information_to_payload(i) for i in top],
    }
