"""theme id=2(career 카테고리) 상세를 학년별 '대학생활 가이드'로 동적 치환 (시연용, 2026-06-03).

themes 앱은 본래 정적 CMS라 ThemeDetailView가 request.user를 보지 않는다. 이 모듈이 학년
(User.grade)별 콘텐츠 상수를 제공하고, ThemeDetailView가 **career 카테고리에 한해** 응답의
title/description/items를 이 값으로 덮어쓴다. 마이그레이션·모델·API 스키마 변경 없음 — 응답
dict만 치환하므로 시연 리스크 최소.

study_tips.py 와 동일한 분리 원칙(phrasing 상수 분리, 추후 LLM 생성으로 교체 여지).

themes 스키마 한계로 둘은 이번 시연 범위에서 빠진다:
  - 질문칩: ThemeItem에 자리 없음 → 제외.
  - 섹션 그룹: ThemeItem에 섹션 필드 없음 → 섹션 제목을 content 빈 guide 아이템(헤더)으로 끼워 표현.
"""
from __future__ import annotations

# 화면 제목 — 디자인 헤더가 "{학년}학년 대학생활 가이드" 형식이라 동적 생성.
GUIDE_TITLE = '{grade}학년 대학생활 가이드'

# 학년(1~4) → 콘텐츠. 각 학년: description(②한마디) + sections[(섹션제목, [(카드제목, 본문), ...])].
# 섹션1은 카드 3개(현재 단계 핵심), 섹션2는 카드 2개(앞으로 준비). 디자인 텍스트 그대로.
GRADE_GUIDES = {
    1: {
        'description': (
            '대학 생활은 처음부터 완벽하게 할 필요 없어요. 다양한 경험을 해보면서 '
            '나에게 맞는 공부 방법과 관심 분야를 찾아가는 것만으로도 충분한 시작입니다.'
        ),
        'sections': [
            ('대학생활 적응', [
                ('학교 제도 익히기',
                 '수강신청, 학점 관리, 장학금, 비교과 프로그램 등 학교 생활의 기본 제도를 익혀보세요.'),
                ('학습 습관 만들기',
                 '시험 직전 벼락치기보다 매주 조금씩 복습하는 습관이 대학 생활을 훨씬 편하게 만들어줍니다.'),
                ('다양한 경험하기',
                 '동아리, 학생회, 비교과 프로그램 등을 통해 새로운 사람과 경험을 만나보세요.'),
            ]),
            ('앞으로 준비하면 좋은 것', [
                ('관심 분야 탐색',
                 '전공 수업과 다양한 활동을 경험하며 내가 흥미를 느끼는 분야를 찾아보세요.'),
                ('학교 지원제도 활용',
                 '학습지원, 상담, 진로 프로그램 등 학교가 제공하는 다양한 지원을 적극 활용해보세요.'),
            ]),
        ],
    },
    2: {
        'description': (
            '2학년은 대학 생활의 방향을 조금씩 구체화하는 시기예요. '
            '전공 역량을 쌓고 관심 분야를 좁혀가며 경험을 늘려보세요.'
        ),
        'sections': [
            ('전공 역량 강화', [
                ('핵심 전공 과목 집중',
                 '앞으로의 진로와 연결될 수 있는 전공 기초 과목을 탄탄하게 다져보세요.'),
                ('프로젝트 경험 시작',
                 '수업 과제나 팀 프로젝트를 통해 협업 경험을 쌓아보세요.'),
                ('관심 분야 구체화',
                 '흥미를 느끼는 분야를 정하고 관련 활동이나 학습을 시작해보세요.'),
            ]),
            ('앞으로 준비하면 좋은 것', [
                ('포트폴리오 재료 만들기',
                 '프로젝트, 발표, 공모전 등 기록으로 남길 수 있는 경험을 쌓아보세요.'),
                ('대외활동 탐색',
                 '관심 분야와 관련된 프로그램이나 공모전을 찾아보는 것도 좋은 경험입니다.'),
            ]),
        ],
    },
    3: {
        'description': (
            '3학년은 학교 안에서 쌓은 경험을 학교 밖으로 확장하는 시기입니다. '
            '다양한 실전 경험을 통해 자신만의 강점을 만들어보세요.'
        ),
        'sections': [
            ('경험 확장하기', [
                ('프로젝트 심화',
                 '관심 분야의 프로젝트를 수행하며 실무 역량을 키워보세요.'),
                ('공모전 및 대외활동 참여',
                 '학교 밖 활동을 통해 다양한 사람들과 협업 경험을 쌓아보세요.'),
                ('포트폴리오 정리',
                 '지금까지 수행한 활동과 프로젝트를 정리해두면 이후 큰 도움이 됩니다.'),
            ]),
            ('앞으로 준비하면 좋은 것', [
                ('인턴십 정보 탐색',
                 '관심 분야의 채용 및 인턴십 정보를 미리 살펴보세요.'),
                ('진로 방향 구체화',
                 '취업, 대학원, 창업 등 앞으로의 방향을 고민해보는 시기입니다.'),
            ]),
        ],
    },
    4: {
        'description': (
            '이제까지 쌓아온 경험을 정리하고 다음 단계로 나아갈 준비를 할 시기입니다. '
            '조급해하기보다 하나씩 차근차근 준비해보세요.'
        ),
        'sections': [
            ('졸업 준비', [
                ('졸업요건 점검',
                 '필수 과목과 이수 학점을 다시 한번 확인해보세요.'),
                ('경험 정리',
                 '프로젝트, 대외활동, 자격증 등 지금까지의 경험을 정리해보세요.'),
                ('진로 준비',
                 '취업 또는 진학을 목표로 필요한 준비를 진행해보세요.'),
            ]),
            ('마지막 체크포인트', [
                ('일정 관리',
                 '지원 마감일, 졸업 일정 등 중요한 일정을 놓치지 않도록 관리해보세요.'),
                ('네트워크 활용',
                 '교수님, 선배, 동기와의 관계는 앞으로도 큰 도움이 될 수 있습니다.'),
            ]),
        ],
    },
}


def _clamp_grade(grade):
    """grade를 1~4로 정규화. None·비정수·범위밖 방어 (grade는 nullable이라 항상 유효값 반환)."""
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return 1                      # 미입력/이상값 → 1학년 (가장 보수적·무해)
    if g < 1:
        return 1
    if g > 4:
        return 4                      # 초과학기/졸업예정 → 4학년 가이드로 clamp
    return g


def build_college_life_guide(grade):
    """학년별 대학생활 가이드를 themes 상세 응답 형태로 조립한다.

    반환: {title, description, items} — ThemeDetailView가 응답 dict의 동명 키를 이걸로 덮어씀.
    items[]는 기존 ThemeItem 직렬화 스키마(id/title/content/external_url/item_type/order)와 동일.
    섹션 제목은 content 빈 guide 아이템(헤더)으로 카드 앞에 끼운다.
    """
    g = _clamp_grade(grade)
    guide = GRADE_GUIDES[g]

    items = []
    idx = 0       # 합성 id (응답 전용, DB row 아님)
    order = 0
    for sec_title, cards in guide['sections']:
        idx += 1
        order += 10
        # 섹션 헤더 — content 빈 guide 아이템 (스키마에 섹션 필드가 없어 헤더로 대체)
        items.append({
            'id': idx, 'title': sec_title, 'content': '',
            'external_url': '', 'item_type': 'guide', 'order': order,
        })
        for card_title, body in cards:
            idx += 1
            order += 10
            items.append({
                'id': idx, 'title': card_title, 'content': body,
                'external_url': '', 'item_type': 'guide', 'order': order,
            })

    return {
        'title': GUIDE_TITLE.format(grade=g),
        'description': guide['description'],
        'items': items,
    }
