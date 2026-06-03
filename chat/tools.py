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
from common.matching import SYNONYM_MAP, _contains_term, score_combined
from courses.models import Course, CourseOffering
from information.models import Information
from notices.models import Notice
from timetables.services.pipeline import PREFERENCE_FIELDS, recommend_timetables

logger = logging.getLogger(__name__)


# spec 5.3.1 — 다음학기 추천 결과를 AI에 전달할 때 상한 (토큰·응답 길이 가드).
# themes 상세 추천과 같은 풀에서 뽑도록 courses 엔진의 canonical 상수를 공유 (#164).
MAX_RECOMMEND_COURSES = MAX_NEXT_SEMESTER_RECOMMENDATIONS
# Step 3 — Notice/Information 검색 결과 상한
MAX_SEARCH_RESULTS = 5
# 과목 조회(lookup_course) 상한 — 매칭 과목 수 / 과목당 분반 수 (토큰 가드).
MAX_LOOKUP_COURSES = 5
MAX_LOOKUP_SECTIONS = 10
# 분야 과목 탐색(find_courses_by_topic) 상한 — 한 분야 트랙이 길 수 있어 넉넉히.
MAX_TOPIC_COURSES = 15


# ─── OpenAI tools 스키마 ──────────────────────────────────────────────

TOOLS_SCHEMA = [
    {
        'type': 'function',
        'function': {
            'name': 'get_next_semester_courses',
            'description': (
                '사용자의 다음 학기(또는 지정된 학기) 추천 수강과목 **목록**을 반환한다. '
                '"뭐 들어야 해", "다음 학기 과목 추천", "전공 추천" 처럼 들을 과목을 '
                '나열·추천하는 질문에서 호출. 명지대 실제 개설 과목·시간·교수 데이터 기반. '
                '주의: "시간표 짜줘/추천"처럼 충돌 없는 시간표 조합을 원하면 이 tool이 아니라 '
                'get_timetable_recommendations를 쓴다 (이 tool 결과로 분반을 직접 조합하지 말 것).'
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
            'name': 'get_timetable_recommendations',
            'description': (
                '충돌 검사·사용자 선호·다양성까지 모두 적용된 **시간표 조합 추천 top-3**를 반환한다. '
                '"시간표 짜줘", "시간표 추천", "21학점으로 시간표", "아침 수업 빼고 짜줘" 처럼 '
                '실제로 들고 다닐 수 있는 한 학기 시간표(조합)를 원하는 질문에서 호출. '
                '각 plan의 과목들은 **시간 충돌이 이미 검증된 조합**이므로 그대로 제시하면 된다 — '
                '분반·시간을 임의로 다시 섞지 말 것. 단순히 들을 과목만 나열하면 '
                'get_next_semester_courses를 쓴다.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'target_year': {
                        'type': 'integer',
                        'description': '대상 학년도 (예: 2026). 미지정 시 사용자 현재 학기 기반 자동 결정.',
                    },
                    'target_semester': {
                        'type': 'integer',
                        'enum': [1, 2],
                        'description': '대상 학기 (1=1학기, 2=2학기). 미지정 시 자동.',
                    },
                    # 아래는 사용자가 자연어로 요구한 선호만 채워 보낸다 (미지정 키는 DB 기본값 유지).
                    # 챗에서 받은 값은 응답 1회용 — DB에 저장하지 않는다.
                    'max_credits': {
                        'type': 'integer',
                        'description': '학기당 최대 학점. "21학점으로/빡세게" 같은 요구 시 설정.',
                    },
                    'min_credits': {
                        'type': 'integer',
                        'description': '학기당 최소 학점. "여유있게/적게" 같은 요구 시 설정.',
                    },
                    'no_morning': {
                        'type': 'boolean',
                        'description': '1교시(오전 이른 시간) 수업 제외. "아침 빼줘" 등.',
                    },
                    'no_evening': {
                        'type': 'boolean',
                        'description': '야간(18시 이후) 수업 제외. "저녁 수업 빼줘" 등.',
                    },
                    'lunch_break': {
                        'type': 'boolean',
                        'description': '점심시간(12~14시) 공강 선호. "점심 비워줘" 등.',
                    },
                    'prefer_off_days': {
                        'type': 'array',
                        'items': {'type': 'string', 'enum': ['월', '화', '수', '목', '금']},
                        'description': '공강(수업 없는 날)으로 원하는 요일. "금요일 공강" → ["금"].',
                    },
                    'banned_days': {
                        'type': 'array',
                        'items': {'type': 'string', 'enum': ['월', '화', '수', '목', '금']},
                        'description': '아예 수업을 넣지 않을 요일. "월요일엔 수업 싫어" → ["월"].',
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
            'name': 'lookup_course',
            'description': (
                '**특정 과목**을 이름이나 과목번호로 조회해 해당 학기 개설 여부와 분반 정보'
                '(교수·강의시간·강의실·제한인원)를 반환한다. 추천 풀과 무관하게 임의의 과목을 찾는다. '
                '"객체지향 개설됐어?", "데이터베이스 누가 가르쳐?", "이 과목 분반 뭐 있어?", '
                '"A교수랑 B교수 시간 비교" 처럼 **특정 과목의 개설/분반/교수/시간**을 묻는 질문에서 호출. '
                '들을 과목을 추천받는 게(get_next_semester_courses) 아니라, 콕 집은 과목을 조회하는 것이다. '
                '강의평·난이도 같은 데이터는 없다.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': '조회할 과목명 또는 과목번호 (예: "객체지향프로그래밍", "컴정102").',
                    },
                    'target_year': {
                        'type': 'integer',
                        'description': '조회 대상 학년도. 미지정 시 사용자 다음 학기 기준 자동 결정.',
                    },
                    'target_semester': {
                        'type': 'integer',
                        'enum': [1, 2],
                        'description': '조회 대상 학기 (1=1학기, 2=2학기). 미지정 시 자동.',
                    },
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'find_courses_by_topic',
            'description': (
                '특정 **분야·진로 키워드**(보안/AI/게임/클라우드/데이터분석/웹 등)와 관련된 '
                '명지대 실제 과목을 찾아 선수과목·권장 학년과 함께 반환한다. '
                '"보안 쪽 가려면 뭐 들어야 해?", "AI 관련 과목 뭐 있어?", "게임 개발 트랙 추천" 처럼 '
                '**관심 분야로 진입하기 위한 과목/순서**를 묻는 질문에서 호출. '
                '동의어로 확장해(예: 보안→정보보호/사이버) 과목명·태그를 매칭한다. '
                '다음 학기 개인 추천(get_next_semester_courses)과 달리, 분야 기준으로 커리큘럼을 탐색한다.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'topic': {
                        'type': 'string',
                        'description': '분야/진로 키워드 (예: "보안", "인공지능", "게임", "클라우드").',
                    },
                },
                'required': ['topic'],
            },
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
        if name == 'get_timetable_recommendations':
            return _get_timetable_recommendations(user, arguments)
        if name == 'get_graduation_progress':
            return _get_graduation_progress(user)
        if name == 'get_completion_status':
            return _get_completion_status(user)
        if name == 'get_course_history':
            return _get_course_history(user)
        if name == 'lookup_course':
            return _lookup_course(user, arguments)
        if name == 'find_courses_by_topic':
            return _find_courses_by_topic(user, arguments)
        if name == 'search_notices':
            return _search_notices(arguments)
        if name == 'search_information':
            return _search_information(arguments)
    except Exception as e:
        logger.exception('chat tool dispatch 실패: name=%s args=%s', name, arguments)
        return {'error': f'내부 호출 실패: {e.__class__.__name__}'}
    return {'error': f'알 수 없는 tool: {name}'}


# get_timetable_recommendations가 payload로 넘길 prefs 키 — timetables 엔진과 동기화.
_TIMETABLE_PREF_KEYS = frozenset(PREFERENCE_FIELDS)


def _resolve_term(user, args: dict[str, Any]):
    """args의 target_year/semester를 확정하고 fallback 학기까지 해석 (#205 일관 처리).

    반환: (requested_year, requested_semester, target_year, target_semester, is_fallback_term)
      - requested_*: 사용자 관점의 '다음 학기' (fallback 전, 안내 표기용)
      - target_*: 추천 데이터의 실제 출처 학기 (개설 정보 없으면 작년 같은 학기로 fallback)
    get_next_semester_courses / get_timetable_recommendations 공유.
    """
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
    target_year, target_semester, is_fallback_term = _resolve_offering_term(
        target_year, target_semester,
    )
    return requested_year, requested_semester, target_year, target_semester, is_fallback_term


def _fallback_term_note(requested_year, requested_semester,
                        target_year, target_semester, action: str) -> str:
    """fallback 학기 안내 문구 — 추천/시간표/조회 tool 공유 (#7 중복 제거, #205 표기 구분).

    사용자의 '다음 학기'(requested)와 데이터 출처 학기(target, 작년 같은 학기)를 구분해,
    AI가 fallback 학기를 '다음 학기'로 잘못 부르지 않게 한다.
    action: 동작 표현 (예: '추천한 것이다.' / '시간표를 짠 것이다.' / '조회한 것이다.').
    """
    return (
        f' 단, 사용자의 다음 학기는 {requested_year}-{requested_semester}학기인데 아직 개설 정보가 '
        f'없다. 그래서 작년 같은 학기인 {target_year}-{target_semester}학기 개설 과목으로 {action} '
        f'사용자에게 "다음 학기({requested_year}-{requested_semester})는 아직 개설 정보가 없어 '
        f'{target_year}-{target_semester}학기 기준"이라고 명확히 안내하고, '
        f'{target_year}-{target_semester}학기를 "다음 학기"라고 부르지 말 것.'
    )


def _offering_payload(o) -> dict[str, Any]:
    """CourseOffering 한 분반 → JSON dict (section_no/professor/schedules).

    한 과목엔 교수·시간이 다른 여러 분반이 있고 학생은 그중 하나만 듣는다. offering 경계를
    유지해 서로 다른 분반의 시간이 한 과목으로 섞이지 않게 한다 (#199).
    """
    return {
        'section_no': o.section_no,        # 강좌번호 — 학기 안 분반 유일 식별자
        'professor': o.professor,          # 교수는 분반 속성 (Course.professor 아님)
        'capacity': o.capacity,            # 제한인원 (실시간 여석 아님, None 가능)
        'offering_note': o.note or '',     # 비고 (예: 'IPP 우선수강')
        'is_online': o.is_online,          # 사이버강의면 schedules 빈 배열 — 시간 무관 (#222)
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


def _get_timetable_recommendations(user, args: dict[str, Any]) -> dict[str, Any]:
    """5.3.6 시간표 조합 추천 top-3 — timetables 엔진(충돌검사+다양성) 결과를 챗에 노출 (#1).

    raw offering을 LLM에 던져 직접 조합시키던 방식(시간 충돌 위험)을 대체. recommend_timetables가
    bitmap 충돌검사·prefs hard filter·Jaccard 다양성까지 끝낸 plan을 준다. 학기 fallback은
    엔진이 안 하므로 get_next_semester_courses와 동일하게 챗 tool이 책임진다.
    """
    (requested_year, requested_semester,
     target_year, target_semester, is_fallback_term) = _resolve_term(user, args)

    # 사용자가 자연어로 명시한 선호만 payload로 — 미지정 키는 엔진 merge_prefs가 DB값 유지.
    # 챗에서 받은 값은 DB에 저장하지 않음 (응답 1회용 override).
    payload = {k: args[k] for k in _TIMETABLE_PREF_KEYS if k in args}

    result = recommend_timetables(user, target_year, target_semester, payload)
    plans = result.get('plans', [])

    note = (
        '각 plan은 시간 충돌이 이미 검증된 시간표 조합이다 — 그대로 제시하고 분반·시간을 다시 '
        '섞지 말 것. plans는 점수순 top-3(서로 다른 조합). total_credits=조합 총학점, '
        'credits_by_category=카테고리별 학점. reason_codes는 이 조합이 좋은 이유 코드 — '
        'covers_required_major(전공필수 포함), covers_short_categories(부족 카테고리 보완), '
        'includes_backlog_required(밀린 필수 포함), off_day_satisfied(원하는 공강 요일 확보), '
        'no_morning_satisfied(오전 수업 없음), no_evening_satisfied(야간 없음), '
        'lunch_break_satisfied(점심 공강 확보), credits_in_target_range(목표 학점 범위 내), '
        'matches_interest_areas(관심분야 과목 포함), credits_below_target(목표 학점 미달). '
        '이 코드를 자연어로 풀어 plan마다 왜 추천했는지 1~2줄로 설명할 것. '
        'offering의 is_online=true면 사이버(원격)강의라 정해진 시간이 없다(schedules 빈 배열). '
        '시간표에서 요일·시간 대신 "사이버강의"로 안내하고, 시간 충돌 없이 포함된 것임을 알릴 것. '
        'status는 현재 졸업요건 이수현황 — 시간표 제시 전 현재 상황을 1~2줄 브리핑하는 데 쓰고 '
        'status에 있는 값만 말하며 없는 수치는 지어내지 말 것.'
    )
    # 엔진 fallback note(머신 코드) 해설 — plan이 부족한 이유를 AI가 안내하게 함.
    engine_note = result.get('note')
    if engine_note == 'insufficient_candidates':
        note += ' 단, 개설/후보 과목이 부족해 충분한 조합을 만들지 못했다. 만든 만큼만 안내할 것.'
    elif engine_note == 'low_diversity_pool':
        note += ' 단, 서로 충분히 다른 조합이 부족해 3개 미만만 나왔다. 있는 만큼만 안내할 것.'

    # fallback 학기면 requested(다음 학기) vs target(작년 같은 학기)을 반드시 구분 안내 (#205, #7 helper).
    if is_fallback_term:
        note += _fallback_term_note(
            requested_year, requested_semester, target_year, target_semester,
            '시간표를 짠 것이다.',
        )

    return {
        # 사용자 관점의 다음 학기 (fallback 전). AI가 "다음 학기" 표기에 이 값을 쓰게 함.
        'requested_year': requested_year,
        'requested_semester': requested_semester,
        # 시간표를 짠 실제 출처 학기 (fallback 시 작년 같은 학기).
        'target_year': target_year,
        'target_semester': target_semester,
        'fallback_term': is_fallback_term,
        'plan_count': len(plans),
        'engine_note': engine_note,
        'note': note,
        # 현재 졸업요건 이수현황 — 시간표 제시 전 "현재 상황" 브리핑용 (목록 tool과 동일 정신).
        'status': _compact_status(user),
        'plans': [
            {
                'total_credits': p['total_credits'],
                'credits_by_category': p['credits_by_category'],
                'reason_codes': p['reason_codes'],
                # plan 안 offering은 충돌검사 통과한 분반 하나로 이미 확정 — 그대로 직렬화.
                'offerings': [
                    {
                        'course_code': o.course.course_code,
                        'name': o.course.name,
                        'category': o.course.category,
                        'credits': o.course.credits,
                        **_offering_payload(o),
                    }
                    for o in p['offerings']
                ],
            }
            for p in plans
        ],
    }


def _get_next_semester_courses(user, args: dict[str, Any]) -> dict[str, Any]:
    (requested_year, requested_semester,
     target_year, target_semester, is_fallback_term) = _resolve_term(user, args)

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
    # fallback이면 AI가 사용자에게 기준 학기를 안내하도록 지시 문구를 덧붙임 (#205, #7 helper).
    if is_fallback_term:
        note += _fallback_term_note(
            requested_year, requested_semester, target_year, target_semester,
            '추천한 것이다.',
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
                # 학생은 그중 하나만 수강한다. offering 경계를 유지해 AI가 분반 하나를 통째로
                # 고르게 한다 (섞으면 실재하지 않는 시간표가 됨, #199). 충돌 없는 조합 자체가
                # 필요하면 get_timetable_recommendations 사용.
                # services가 target 학기로 필터·prefetch한 offerings (추가 쿼리 없음).
                'offerings': [_offering_payload(o) for o in c.offerings.all()],
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


def _compact_status(user) -> dict[str, Any] | None:
    """build_completion_status 결과를 추천 응답에 끼울 슬림 버전으로 축약.

    추천 tool은 과목 풀까지 함께 실어 토큰 압박이 있으므로, 브리핑에 필요한 핵심만 남긴다 —
    카테고리별 (completed/required/remaining) + 총계 + 채플. 영역(areas)·필수과목(required_courses)
    상세는 제외 (그 디테일은 독립 tool get_completion_status에서 full로 제공).

    브리핑은 추천에 곁들이는 부가 정보다 — 집계가 실패해도 추천 자체는 살아야 한다.
    그래서 best-effort로 처리하고 실패 시 None을 반환한다 (status 부가 집계 하나가 추천
    응답 전체를 죽이던 회귀 차단, #224). 프롬프트는 status 부재 시 브리핑을 생략하도록
    이미 지시돼 있으므로 None이어도 안전하다.
    """
    try:
        full = build_completion_status(user)
    except Exception:
        logger.exception(
            '추천 브리핑용 이수현황 집계 실패 — status 생략 (user=%s)',
            getattr(user, 'id', None),
        )
        return None
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
        'chapel은 채플 이수 회수. 사용자에게 잔여 위주로 간결히 안내할 것. '
        # 졸업 가능 판정 (#5) — "졸업 가능해?" 류 질문은 이 feasibility로 답할 것.
        'feasibility는 졸업 가능 판정: on_track=true면 남은 학기(remaining_semesters) 안에 '
        '남은 학점을 채울 수 있다(학기당 최대 per_semester_cap학점 가정). on_track=false면 일정상 '
        '추가 학기가 필요하다. on_track=null이면 학년/학기 미입력으로 일정 판정 불가. '
        'blockers는 졸업을 막는 요인 — credits_over_capacity(남은 학기로 학점 부족), '
        'unmet_required_courses(미이수 전공필수/학문기초 과목, meta.courses), chapel_short(채플 부족). '
        '"졸업 가능해?"엔 on_track + blockers를 근거로 답하고, blockers의 수치/과목만 말하며 '
        '없는 근거(남은 학기·상한 등)를 지어내지 말 것. on_track=null이면 학년·학기 입력을 권할 것.'
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


def _lookup_course(user, args: dict[str, Any]) -> dict[str, Any]:
    """특정 과목 조회 — 이름/과목번호로 찾아 해당 학기 개설·분반 정보를 반환 (#2).

    추천 풀(get_next_semester_courses의 상위 N개)에 없는 임의 과목도 조회 가능. "개설됐어?",
    "누가 가르쳐?", "분반 뭐 있어?", 교수 비교 류 질문의 데이터 소스. 그동안 챗엔 임의 과목
    조회 수단이 없어 추천 풀 밖 과목은 환각 위험이 있었다.
    """
    query = (args.get('query') or '').strip()
    if not query:
        return {'error': '조회할 과목명 또는 과목번호(query)가 필요합니다.'}

    # 개설 여부를 판단할 학기 — 다음학기 추천과 동일 규칙(자동결정 + fallback)으로 일관 (#205).
    (requested_year, requested_semester,
     target_year, target_semester, is_fallback_term) = _resolve_term(user, args)

    # 과목번호/이름 부분일치. 매칭 과다 시 상한으로 자름 (토큰 가드).
    matched = list(
        Course.objects
        .filter(Q(course_code__icontains=query) | Q(name__icontains=query))
        .prefetch_related('prerequisites__prerequisite')  # "듣기 전에 뭐 필요?" 응답용 (#8)
        .order_by('course_code')[:MAX_LOOKUP_COURSES + 1]
    )
    truncated = len(matched) > MAX_LOOKUP_COURSES
    matched = matched[:MAX_LOOKUP_COURSES]

    courses_payload = []
    for c in matched:
        offs = (
            CourseOffering.objects
            .filter(course=c, year=target_year, semester=target_semester)
            .prefetch_related('schedules')
            .order_by('section_no')[:MAX_LOOKUP_SECTIONS]
        )
        offerings = [_offering_payload(o) for o in offs]
        courses_payload.append({
            'course_code': c.course_code,
            'name': c.name,
            'category': c.category,
            'credits': c.credits,
            'department': c.department,
            # 해당 학기에 실제 개설(분반 존재) 여부 — False면 "그 학기엔 개설 안 됨".
            'offered_in_term': bool(offerings),
            # 선수과목 — "이 과목 듣기 전에 뭐 들어야 해?" 응답용 (#8). 없으면 [].
            'prerequisites': [
                {'course_code': p.prerequisite.course_code, 'name': p.prerequisite.name}
                for p in c.prerequisites.all()
            ],
            'offerings': offerings,
        })

    note = (
        '특정 과목 조회 결과. offered_in_term=해당 학기 개설 여부(false면 그 학기엔 개설 안 됨), '
        'offerings=분반 목록(section_no=강좌번호, professor=교수, schedules=요일·시간·강의실, '
        'capacity=제한인원[실시간 여석 아님], is_online=사이버강의 여부). '
        'is_online=true면 원격강의라 정해진 시간이 없으니(schedules 빈 배열) "사이버강의"로 안내할 것. '
        'prerequisites=선수과목(이 과목 듣기 전에 들어야 하는 '
        '과목, 없으면 빈 배열) — "듣기 전에 뭐 필요해?"엔 이걸로 답할 것. '
        '교수 비교/분반 안내 시 이 데이터만 쓸 것. '
        '강의평·난이도·수강 경쟁률 같은 정보는 없으니 지어내지 말 것. '
        f'조회 학기는 {target_year}-{target_semester}학기 기준이다. '
        'match_count=0이면 그 이름/번호의 과목을 찾지 못했다고 안내할 것.'
    )
    if truncated:
        note += f' 매칭 과목이 많아 {MAX_LOOKUP_COURSES}개만 반환했다 — 더 구체적인 과목명/번호를 요청할 것.'
    # 다음 학기 개설 정보가 아직 없어 작년 같은 학기로 조회한 경우 (#205 표기 구분, #7 helper).
    if is_fallback_term:
        note += _fallback_term_note(
            requested_year, requested_semester, target_year, target_semester,
            '조회한 것이다.',
        )

    return {
        'query': query,
        'requested_year': requested_year,
        'requested_semester': requested_semester,
        'target_year': target_year,
        'target_semester': target_semester,
        'fallback_term': is_fallback_term,
        'match_count': len(courses_payload),
        'note': note,
        'courses': courses_payload,
    }


# 분야 키워드 토큰 분리 — common.matching과 동일 구분자(콤마/공백/슬래시/가운뎃점).
_TOPIC_SPLIT_RE = re.compile(r'[\s,/·]+')


def _expand_topic_terms(topic: str) -> set[str]:
    """분야 키워드를 토큰화 + 동의어 확장 (보안→정보보호/사이버 등, common.SYNONYM_MAP 재사용)."""
    raw = {t for t in _TOPIC_SPLIT_RE.split(topic.lower()) if t}
    terms = set(raw)
    for tok in raw:
        terms |= SYNONYM_MAP.get(tok, set())
    return terms


def _find_courses_by_topic(user, args: dict[str, Any]) -> dict[str, Any]:
    """분야·진로 키워드로 명지대 실제 과목 탐색 — grounded 트랙 제안용 (#4).

    "보안 가려면 뭐 들어?" 류에서 LLM이 명지대 실커리큘럼과 무관한 과목 시퀀스를 지어내던
    위험 해소. 동의어 확장(common.SYNONYM_MAP) 후 Course.name/tags 매칭, 선수과목·권장학년
    동봉 → LLM이 데이터 위에서 순서를 제안하게 한다. 개설학기·교수는 lookup_course로 별도.
    """
    topic = (args.get('topic') or '').strip()
    if not topic:
        return {'error': '조회할 분야/키워드(topic)가 필요합니다.'}

    terms = _expand_topic_terms(topic)
    if not terms:
        return {'topic': topic, 'matched_terms': [], 'count': 0,
                'note': '유효한 키워드가 없습니다.', 'courses': []}

    # 후보 수집 — 이름 부분일치(DB icontains 프리필터) ∪ 태그 토큰 매칭(파이썬, 대소문자 무관).
    # 태그는 JSONField라 DB 측 매칭이 대소문자·백엔드 의존이라 파이썬에서 토큰 비교한다.
    name_q = Q()
    for term in terms:
        name_q |= Q(name__icontains=term)
    candidate_ids = set(Course.objects.filter(name_q).values_list('id', flat=True))
    for cid, tags in (
        Course.objects.exclude(tags=[]).exclude(tags__isnull=True)
        .values_list('id', 'tags')
    ):
        if {str(t).lower() for t in (tags or [])} & terms:
            candidate_ids.add(cid)

    courses = (
        Course.objects.filter(id__in=candidate_ids)
        .prefetch_related('prerequisites__prerequisite')
        # 권장 학년 → 카테고리 → 과목번호 순 = 자연스러운 수강 순서 힌트
        .order_by('year_open', 'category', 'course_code')
    )

    matched = []
    for c in courses:
        name_l = (c.name or '').lower()
        tag_toks = {str(t).lower() for t in (c.tags or [])}
        # DB 프리필터(icontains)를 _contains_term으로 재검증 — 짧은 영문 오매칭(ai⊂brain) 제거.
        hit = sorted({t for t in terms if (t in tag_toks) or _contains_term(name_l, t)})
        if not hit:
            continue
        matched.append({
            'course_code': c.course_code,
            'name': c.name,
            'category': c.category,
            'credits': c.credits,
            'recommended_grade': c.year_open,   # 권장 학년 (0=학년 무관)
            'matched_terms': hit,               # 어떤 키워드로 걸렸는지 (투명성)
            'prerequisites': [
                {'course_code': p.prerequisite.course_code, 'name': p.prerequisite.name}
                for p in c.prerequisites.all()
            ],
        })
        if len(matched) >= MAX_TOPIC_COURSES:
            break

    return {
        'topic': topic,
        'matched_terms': sorted(terms),   # 동의어 확장된 검색어 전체
        'count': len(matched),
        'note': (
            '분야 키워드를 동의어 확장해 명지대 실제 과목 중 이름/태그가 맞는 것만 반환(grounded). '
            'recommended_grade=권장 학년(0=무관), prerequisites=선수과목 목록. '
            '듣는 순서는 prerequisites(먼저 들어야 하는 과목)와 recommended_grade를 근거로 제안할 것. '
            '이 목록에 없는 과목명을 지어내지 말 것. 특정 과목의 개설 학기·교수·시간이 필요하면 '
            'lookup_course로 이어서 확인. count=0이면 해당 분야 매칭 과목을 못 찾았다고 안내할 것.'
        ),
        'courses': matched,
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

    # 동의어 확장 (#217) — contest-guide와 매칭 기준 통일. 공모전 Information은 본문이 없어
    # title+categories만 매칭 재료라, "AI"→"인공지능" 같은 어휘 격차를 동의어로 메운다.
    token_set = set(tokens)
    expanded = set(token_set)
    for t in token_set:
        expanded |= SYNONYM_MAP.get(t, set())

    # DB 프리필터 — 확장 검색어 중 하나라도 title에 부분일치 (synonym-only 매칭도 후보에 포함).
    title_q = Q()
    for term in expanded:
        title_q |= Q(title__icontains=term)

    # 만료된 정보는 제외 (end_date 지남)
    today = date.today()
    qs = (
        Information.objects
        .filter(title_q)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .order_by('end_date', '-id')[:MAX_SEARCH_RESULTS * 3]
    )

    scored = []
    for info in qs:
        # 키워드당 1점 캡 — categories 토큰 완전일치 OR title 동의어 부분일치 (common.matching 재사용).
        # contest-guide(score_combined)와 동일 기준 → 진입점별 매칭 불일치 해소.
        score = score_combined(token_set, info.categories or [], info.title or '')
        if score:   # DB icontains 프리필터가 통과시킨 latin 오매칭(_contains_term 미통과)은 0점 → 제외
            scored.append((score, info))

    # 점수 DESC, 마감 임박 우선
    scored.sort(key=lambda x: (
        -x[0],
        x[1].end_date or date.max,
    ))
    top = [i for s, i in scored[:MAX_SEARCH_RESULTS]]

    return {
        'count': len(top),
        'query': query,
        'note': '교내외 정보 검색 결과 (마감 미경과만, 관심 키워드 동의어 확장 매칭). URL은 원문 링크.',
        'results': [_information_to_payload(i) for i in top],
    }
