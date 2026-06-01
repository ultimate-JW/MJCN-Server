"""취업·진로 로드맵 (테마 상세 2번째 화면) 문구 조립 (이슈 #171, Figma 221-5848).

본래 이 화면은 누적된 AI 챗에서 사용자의 진로 신호를 모아 LLM이 생성하는 것이 목표다.
누적 챗 인프라 전까지는 **직무별 템플릿**으로 채우되, 실제로 계산 가능한 두 조각은
서비스 레이어에서 grounded 값으로 주입한다:

  - ② 전공 기초 status  : 졸업요건상 전공필수 잔여 여부 (services._build_short_categories)
  - ③ STEP 5 학기 전략   : 5.3.1 추천 상위 과목 (recommend_next_semester_courses)

나머지(① 조언 phrasing / ② 실무·인턴 / ③ STEP 1~4·6 / ④ 질문칩)는 진로 도메인 지식이라
백엔드에 데이터가 없다 → 아래 ROADMAP_TEMPLATES에 직무별 텍스트로 보관.

# LLM 교체 예정 — 누적 챗 인프라가 생기면 ROADMAP_TEMPLATES 자리를 LLM 생성으로 교체.
#   advice.py 와 동일한 분리 원칙: 신호/grounded 계산은 services, phrasing만 이 모듈.

스코프(#171): 시연용 1직무('클라우드 개발자')만. 직무 확장은 LLM 몫이라 늘리지 않는다.
"""
from __future__ import annotations

# note 머신 코드 — 호출자(프론트/LLM)가 톤을 결정하므로 한국어 메시지 대신 코드로 (#25/#97 정신)
NOTE_NO_CAREER_GOAL = 'NO_CAREER_GOAL'          # target_job 미입력 — 로드맵 비활성
NOTE_UNSUPPORTED_CAREER_GOAL = 'UNSUPPORTED_CAREER_GOAL'  # 템플릿 없는 직무 (현재 1직무만 지원)

# STEP 5 학기 전략에 노출할 추천 과목 수 (Figma 221-5848 — 3과목)
CAREER_STEP5_SIZE = 3


# 직무별 로드맵 템플릿. 키 = User.target_job 값.
# readiness[0](전공 기초)의 status/message는 services가 grounded 값으로 덮어쓴다 (아래 build 참조).
# steps 중 use_courses=True인 STEP은 lines 대신 추천 과목(step5_courses)이 주입된다.
ROADMAP_TEMPLATES = {
    '클라우드 개발자': {
        # ① 띵똥이의 조언 — {basics}는 전공 기초 grounded 판정으로 치환 (아래 _BASICS_FRAGMENT)
        'advice': '현재 클라우드 개발자를 목표로 하고 계시네요. {basics} '
                  '이제는 "취업을 위한 실무 준비 단계"예요😊',
        # ② 현재 취업 준비도 평가 — 3칸
        'readiness': [
            # 전공 기초: status/message는 grounded (전공필수 잔여 여부). 여기 값은 기본(충분) 케이스.
            {'status': 'ok', 'label': '전공 기초',
             'message_ok': '클라우드 개발자 기준 충분합니다.',
             'message_warn': '아직 전공 기초 과목이 더 필요해요.'},
            # 실무 경험 / 인턴십: 백엔드에 데이터 없음 — 템플릿 고정 (스텁). # LLM 교체 예정
            {'status': 'warn', 'label': '실무 경험',
             'message': '프로젝트 경험이 부족합니다.'},
            {'status': 'tip', 'label': '인턴십 경험 (선택)',
             'message': '프로젝트가 부족하다면 인턴으로 보완할 수 있어요.'},
        ],
        # ③ 단계별 로드맵 STEP 1~6 — STEP 5만 use_courses(5.3.1 주입), 나머지는 template lines
        'steps': [
            {'step': 1, 'title': '방향 확정',
             'lines': ['클라우드 + 백엔드 병행 추천', 'AWS or GCP 선택']},
            {'step': 2, 'title': '기술 준비',
             'lines': ['AWS 기본 (EC2, S3)', 'Docker', 'Linux']},
            {'step': 3, 'title': '포트폴리오',
             'lines': ['배포 경험 있는 프로젝트 2개 이상 추천',
                       '추천 프로젝트 유형: AWS 배포 웹서비스 / 간단한 서버 프로젝트']},
            {'step': 4, 'title': '인턴십 (선택 전략)',
             'lines': ['필수는 아니지만, 실무 경험을 빠르게 쌓는 방법!',
                       '추천 상황: 프로젝트 경험이 부족할 때 / 실무 경험 어필이 어려울 때',
                       '활용 방법: 여름·겨울방학 인턴 지원 / 스타트업·중소기업 위주 지원 추천']},
            # STEP 5 학기 전략 — lines 대신 추천 과목(5.3.1) 주입 (grounded)
            {'step': 5, 'title': '학기 전략', 'use_courses': True},
            {'step': 6, 'title': '취업 준비',
             'lines': ['AWS 자격증 1개 (선택)', 'GitHub 정리']},
        ],
        # ④ 띵똥이에게 바로 물어보기 — 탭하면 prompt가 챗으로 전송 (#164 패턴, 진로 버전)
        'quick_questions': [
            {'label': '해외 인턴 준비 방법 보기',
             'prompt': '클라우드 개발자 해외 인턴은 어떻게 준비해?'},
            {'label': '영어 공부 루틴 추천 받기',
             'prompt': '개발자 취업을 위한 영어 공부 루틴 추천해줘'},
            {'label': '클라우드 취업 로드맵 다시보기',
             'prompt': '클라우드 개발자 취업 로드맵 다시 알려줘'},
        ],
    },
}

# ① 조언의 {basics} 치환 조각 — 전공 기초 grounded 판정에 따라 갈림
_BASICS_FRAGMENT_OK = '지금까지 이수 과목을 보면 기초는 충분해서'
_BASICS_FRAGMENT_WARN = '아직 전공 기초가 조금 더 필요하지만'


def build_career_roadmap(user, *, major_basics_ok, step5_courses):
    """직무별 로드맵 dict를 조립한다 (grounded 2조각 주입). 지원 안 하는 직무면 None.

    인자:
      major_basics_ok : 전공 기초 충족 여부 (services에서 졸업요건 기반 판정) — ②전공기초·①조언에 반영
      step5_courses   : STEP 5 학기 전략에 넣을 추천 Course 리스트 (5.3.1 결과)

    반환: {advice:{text}, readiness:[...], roadmap:[...], quick_questions:[...]} 또는 None.
      None이면 호출자가 NOTE_UNSUPPORTED_CAREER_GOAL/NO_CAREER_GOAL 빈 상태로 응답.
    """
    job = (getattr(user, 'target_job', '') or '').strip()
    template = ROADMAP_TEMPLATES.get(job)
    if template is None:
        return None  # 직무 미입력 또는 미지원 — 호출자가 빈 상태 처리

    name = (getattr(user, 'name', '') or '').strip()
    greeting = f'{name}님은 ' if name else ''

    # ① 조언 — {basics}를 전공 기초 grounded 판정으로 치환
    basics = _BASICS_FRAGMENT_OK if major_basics_ok else _BASICS_FRAGMENT_WARN
    advice_text = f'{greeting}{template["advice"].format(basics=basics)}'

    # ② 준비도 — 전공 기초(index 0)만 grounded로 status/message 결정, 나머지는 템플릿 그대로
    readiness = []
    for i, item in enumerate(template['readiness']):
        if i == 0:  # 전공 기초 — grounded
            readiness.append({
                'status': 'ok' if major_basics_ok else 'warn',
                'label': item['label'],
                'message': item['message_ok'] if major_basics_ok else item['message_warn'],
            })
        else:
            readiness.append({
                'status': item['status'],
                'label': item['label'],
                'message': item['message'],
            })

    # ③ STEP 1~6 — use_courses STEP은 step5_courses 주입, 나머지는 template lines
    roadmap = []
    for step in template['steps']:
        if step.get('use_courses'):
            roadmap.append({
                'step': step['step'],
                'title': step['title'],
                'lines': [],
                'courses': step5_courses,  # serializer가 RecommendedCourseSerializer로 직렬화
            })
        else:
            roadmap.append({
                'step': step['step'],
                'title': step['title'],
                'lines': step['lines'],
                'courses': [],
            })

    return {
        'advice': {'text': advice_text},
        'readiness': readiness,
        'roadmap': roadmap,
        'quick_questions': template['quick_questions'],
    }
