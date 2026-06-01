"""교환학생·해외 인턴십 가이드 테마 상세 문구 조립 (이슈 #180, Figma 221-6066).

#171 취업·진로 로드맵(career_roadmap.py)의 형제 화면이지만 **개인화 축이 다르다**:
로드맵은 User.target_job 기반, 본 화면은 **학년·학기(메인) + 관심사(보조)** 기반.

받쳐줄 추천 엔진이 없는 진로 판단 콘텐츠라 전부 규칙 기반 템플릿으로 채운다 (LLM 없음).
career_roadmap.py / advice.py 와 동일한 분리 원칙 — 신호 추출(interest_signal)과
phrasing(아래 템플릿)을 나눠 추후 LLM 생성으로 교체 가능하게.

기획 전문: .작업일지/2026-06-02_교환학생_해외인턴_가이드_기획.md
"""
from __future__ import annotations

# 옵션 표시 메타 — 두 선택지의 라벨/아이콘 (디자인 🎓 교환학생 / 🌍 해외 인턴십)
OPT_EXCHANGE = '교환학생'
OPT_INTERN = '해외 인턴십'
ICON = {OPT_EXCHANGE: '🎓', OPT_INTERN: '🌍'}


# ─── 관심사 신호 (③④ 공유) ──────────────────────────────────────────────
# InterestArea.category 라벨 + custom_text 를 한 텍스트로 합쳐 부분일치 스캔.
# 12-row 버킷 테이블 대신 두 방향 키워드 세트만 둔다 → category 추가돼도 안 깨짐.

# 글로벌·어학 지향 → 교환학생 신호 (드물고 의미 있는 신호라 ③ 점수에 반영)
GLOBAL_KEYWORDS = {
    '어학', '영어', '외국어', '글로벌', '국제', '해외', '교환', '유학', '통번역', '무역', '통상',
}
# 실무·취업 지향 → 해외인턴 신호. ※ 12개 category 중 대부분이 매칭돼 거의 전원 True →
# ③ 점수에는 미반영(인턴 쏠림 방지), ④ bullet 트리거로만 사용.
HANDSON_KEYWORDS = {
    '개발', '프로그래밍', '클라우드', '데이터', 'ai', '인공지능', '보안', 'it',
    '디자인', '마케팅', '광고', '금융', '회계', '콘텐츠', '미디어', '건축', '바이오',
    '취업', '인턴', '실무', '포트폴리오', '창업', '스타트업',
}


def interest_signal(user) -> dict:
    """사용자 관심사 → {global, handson} 두 방향 신호. 둘 다 True 가능, 둘 다 False = 미매칭."""
    interests = getattr(user, 'interests', None)
    if interests is None:
        return {'global': False, 'handson': False}
    # category(고정 12개) + custom_text(자유) 합쳐 소문자화 — 한 번에 두 세트 스캔
    text = ' '.join(
        f'{(i.category or "")} {(i.custom_text or "")}' for i in interests.all()
    ).lower()
    return {
        'global': any(k in text for k in GLOBAL_KEYWORDS),
        'handson': any(k in text for k in HANDSON_KEYWORDS),
    }


# ─── 학년·학기 / 스테이지 헬퍼 ───────────────────────────────────────────

def _normal_semester(semester) -> int:
    """계절학기(3=하계/4=동계)는 직전 정규학기로 환산 (1 또는 2). 미입력 시 1."""
    return 1 if semester in (1, 3) else 2


def _stage(grade) -> str:
    """학년 → ④⑤ 스테이지. 1~2=early / 3=mid / 4=late. 미입력 시 mid(중립)."""
    if grade in (1, 2):
        return 'early'
    if grade == 4:
        return 'late'
    return 'mid'  # 3학년 + 방어 fallback


# ─── ② 띵똥이의 조언 ─────────────────────────────────────────────────────
# (학년, 정규학기) 8칸 템플릿. {name}/{major}/{grade}/{sem} 슬롯 치환. LLM 없음.

THEME_STAGE_ADVICE = {
    (1, 1): "{name}님은 현재 {major} {grade}-{sem}학기로, 지금은 교환학생이나 해외 인턴십을 결정하기보다 대학 생활의 기반을 다지는 시기예요. "
            "다만 지금부터 어학 점수와 시야를 넓혀두면 나중에 선택지가 훨씬 넓어져요.",
    (1, 2): "{name}님은 현재 {major} {grade}-{sem}학기로 관심 분야를 탐색하는 단계예요. "
            "교환학생이나 해외 프로그램에 관심이 있다면 어학 준비와 파견 정보를 미리 확인해두는 것을 추천해요.",
    (2, 1): "{name}님은 현재 {major} {grade}-{sem}학기로 전공 기초를 다지는 시기예요. "
            "교환학생을 생각한다면 지금 어학 요건과 학점 인정 규정을 미리 확인해두는 게 좋아요.",
    (2, 2): "{name}님은 현재 {major} {grade}-{sem}학기로 교환학생이나 해외 프로그램 지원을 실제로 고민하기 시작할 시기예요. "
            "관심이 있다면 어학 요건과 학점 인정 규정을 함께 확인해두면 좋아요.",
    (3, 1): "{name}님은 현재 {major} {grade}-{sem}학기로 지금 시점에서는 \"해외 경험\"보다 취업 준비 완성도가 더 중요한 단계예요. "
            "하지만 방향에 따라 교환학생이나 해외 인턴도 충분히 좋은 선택이 될 수 있어요.",  # 디자인 원문
    (3, 2): "{name}님은 현재 {major} {grade}-{sem}학기로 진로를 구체화할 시기예요. "
            "졸업 일정과 취업 준비를 함께 고려해 교환학생과 해외 인턴 중 무엇이 맞을지 살펴보면 좋아요.",
    (4, 1): "{name}님은 현재 {major} {grade}-{sem}학기로 졸업과 진로를 함께 고려해야 하는 시기예요.\n\n"
            "교환학생이나 해외 인턴십을 선택할 때는 경험 자체보다 졸업 일정, 취업 준비, 경력 활용 가능성을 함께 살펴보는 것이 중요해요.",
    (4, 2): "{name}님은 현재 {major} {grade}-{sem}학기로 졸업이 가까워지고 있어요.\n\n"
            "해외 경험을 계획하고 있다면 남은 학업 일정과 진로 계획에 어떤 영향을 주는지 함께 검토해보는 것이 좋아요.",
}
_DEFAULT_STAGE_ADVICE = "{name}님께 맞춰 교환학생과 해외 인턴 방향을 함께 살펴볼게요."  # 학년/학기 미입력 방어


def build_advice(user) -> dict:
    """② 조언 — (학년, 정규학기) 템플릿에 이름/전공/학년/학기 슬롯 치환."""
    grade = getattr(user, 'grade', None)
    sem = _normal_semester(getattr(user, 'semester', None))
    name = (getattr(user, 'name', '') or '').strip() or '회원'
    major = (getattr(user, 'major', '') or '').strip() or '전공'
    template = THEME_STAGE_ADVICE.get((grade, sem), _DEFAULT_STAGE_ADVICE)
    # grade None이면 표시용으로도 sem만 노출되지 않게 안전 치환
    text = template.format(name=name, major=major, grade=grade if grade else '-', sem=sem)
    return {'stage_message': text}


# ─── ③ 나에게 필요한 선택일까 (별점) ──────────────────────────────────────
# 별점 결정 요소 = (학년, 정규학기) base + 관심사 global 신호. handson은 미반영.

NECESSITY_SCORE = {                          # (교환학생, 해외인턴십), 각 1~5점
    (1, 1): (2, 1), (1, 2): (3, 1),          # 저학년 — 교환 우상향
    (2, 1): (4, 2), (2, 2): (5, 2),          # 2-2 교환 peak(5)
    (3, 1): (2, 4), (3, 2): (2, 5),          # 3학년 인턴 우위, 3-2 인턴 peak(5). (3,1)은 디자인 원문
    (4, 1): (1, 3), (4, 2): (1, 2),          # 4학년 — 둘 다 하락, 인턴 우위
}

# 한줄이유 — 옵션 × 점수밴드(high 4~5 / mid 2~3 / low 1). 중립 톤(단정 금지).
NECESSITY_REASON = {
    OPT_EXCHANGE: {
        'high': '어학·국제 경험을 제대로 쌓을 수 있는 시기예요',
        'mid': '경험·언어에는 도움되지만, 취업에 직접 미치는 영향은 적은 편이에요',
        'low': '지금 시점에는 우선순위가 높지 않아요',
    },
    OPT_INTERN: {
        'high': '잘 살리면 강력한 스펙이 될 수 있어요',
        'mid': '실무 경험에는 도움되지만, 준비가 더 필요한 시기예요',
        'low': '지금 시점에는 우선순위가 높지 않아요',
    },
}


def _band(score: int) -> str:
    """점수 → 밴드 키. 4~5 high / 2~3 mid / 1 low."""
    if score >= 4:
        return 'high'
    if score >= 2:
        return 'mid'
    return 'low'


def build_necessity(user, signal: dict) -> list:
    """③ 별점 — base 점수 + global이면 교환 +1(상한 5). 각 옵션 {option, icon, score, reason}."""
    grade = getattr(user, 'grade', None)
    sem = _normal_semester(getattr(user, 'semester', None))
    exch, intern = NECESSITY_SCORE.get((grade, sem), (3, 3))  # 미입력 방어 — 중립 3/3
    if signal.get('global'):
        exch = min(exch + 1, 5)              # 글로벌 지향 학생만 교환 +1
    return [
        {'option': OPT_EXCHANGE, 'icon': ICON[OPT_EXCHANGE],
         'score': exch, 'reason': NECESSITY_REASON[OPT_EXCHANGE][_band(exch)]},
        {'option': OPT_INTERN, 'icon': ICON[OPT_INTERN],
         'score': intern, 'reason': NECESSITY_REASON[OPT_INTERN][_band(intern)]},
    ]


# ─── ④ 판단 기준에 따른 평가 ─────────────────────────────────────────────
# 학년 스테이지(early/mid/late) base + 관심사 매칭 bullet 1개 + note 1줄.

EVALUATION = {
    'early': {
        OPT_EXCHANGE: {'fits': ['다양한 문화와 대학 생활을 경험하고 싶을 때', '외국어 역량을 키우고 싶을 때'],
                       'caveat': '지금부터 준비하면 충분히 도전할 수 있어요'},
        OPT_INTERN: {'fits': ['실무가 어떤 건지 미리 경험해보고 싶을 때', '진로 방향을 구체화하고 싶을 때'],
                     'caveat': '아직 이르다면 교환학생과 함께 비교해보는 걸 추천해요'},
    },
    'mid': {  # 3학년
        OPT_EXCHANGE: {'fits': ['어학·국제 경험을 제대로 쌓고 싶을 때', '해외 대학 수업을 경험해보고 싶을 때'],
                       'caveat': '취업 준비 일정도 함께 고려하는 게 좋아요'},
        OPT_INTERN: {'fits': ['실무 경험을 쌓고 싶을 때', '차별화된 스펙을 만들고 싶을 때'],
                     'caveat': '포트폴리오를 먼저 갖추고 지원하면 효과가 커요'},
    },
    'late': {  # 4학년
        OPT_EXCHANGE: {'fits': ['졸업을 미루더라도 국제 경험을 꼭 쌓고 싶을 때'],
                       'caveat': '졸업·취업 일정에 미치는 영향을 꼭 함께 확인하세요'},
        OPT_INTERN: {'fits': ['방학을 활용해 단기 실무 경험을 쌓고 싶을 때', '취업 직전 실무 감각을 끌어올리고 싶을 때'],
                     'caveat': '남은 졸업요건과 일정이 빠듯하지 않은지 확인하세요'},
    },
}
# 관심사 매칭 시 추가 bullet (스테이지 공통) — global→교환 카드, handson→인턴 카드
ON_GLOBAL = {'bullet': '글로벌·국제 환경을 직접 경험하고 싶을 때', 'note': '관심 분야와 연관성이 높아요'}
ON_HANDSON = {'bullet': '관심 직무의 실무 경험을 쌓고 싶을 때', 'note': '현재 관심 분야와 연관성이 높아요'}


def build_evaluation(user, signal: dict) -> list:
    """④ 판단 기준 — 스테이지 base fits + (관심사 매칭 시 bullet 추가) + caveat + (note)."""
    stage = EVALUATION[_stage(getattr(user, 'grade', None))]
    out = []
    # 교환학생 카드 — global 신호 매칭 시 bullet/note 추가
    exch = stage[OPT_EXCHANGE]
    exch_fits = list(exch['fits'])
    exch_note = None
    if signal.get('global'):
        exch_fits.append(ON_GLOBAL['bullet'])
        exch_note = ON_GLOBAL['note']
    out.append({'option': OPT_EXCHANGE, 'icon': ICON[OPT_EXCHANGE],
                'fits': exch_fits, 'caveat': exch['caveat'], 'interest_note': exch_note})
    # 해외인턴 카드 — handson 신호 매칭 시 bullet/note 추가
    intern = stage[OPT_INTERN]
    intern_fits = list(intern['fits'])
    intern_note = None
    if signal.get('handson'):
        intern_fits.append(ON_HANDSON['bullet'])
        intern_note = ON_HANDSON['note']
    out.append({'option': OPT_INTERN, 'icon': ICON[OPT_INTERN],
                'fits': intern_fits, 'caveat': intern['caveat'], 'interest_note': intern_note})
    return out


# ─── ⑤ 지금 시점 기준 추천 (순위) ────────────────────────────────────────
# 학년 스테이지별 고정 ranked list. 별점과 자연 일관(저학년 교환 우위 / 3~4학년 인턴 우위).

CURRENT_RECOMMENDATION = {
    'early': [  # 1~2학년
        {'rank': 1, 'title': '교환학생 준비', 'note': '어학 요건·파견 정보를 미리 확인해두기 좋은 시기예요'},
        {'rank': 2, 'title': '전공 기초 + 대외활동', 'note': '관심 분야를 넓히며 기본기를 다져두세요'},
        {'rank': 3, 'title': '해외 인턴십', 'note': '아직 이르니 방향이 잡힌 뒤 고려해도 늦지 않아요'},
    ],
    'mid': [  # 3학년 — 디자인 원문
        {'rank': 1, 'title': '프로젝트 + 포트폴리오', 'note': '실무 경험을 쌓아 취업 준비의 기반을 만드세요'},
        {'rank': 2, 'title': '해외 인턴십', 'note': '3-2·4-1 방학 추천 — 포트폴리오를 갖춘 뒤 지원하는 게 베스트예요'},
        {'rank': 3, 'title': '교환학생', 'note': '지원 시기와 졸업 계획을 함께 고려해보세요'},
    ],
    'late': [  # 4학년
        {'rank': 1, 'title': '졸업요건 + 취업 준비', 'note': '남은 졸업요건을 점검하고 취업에 집중할 시기예요'},
        {'rank': 2, 'title': '해외 인턴십 (방학 단기)', 'note': '방학을 활용한 단기 인턴이 현실적이에요'},
        {'rank': 3, 'title': '교환학생', 'note': '졸업 일정에 미치는 영향을 꼭 함께 확인하세요'},
    ],
}


def build_current_recommendation(user) -> list:
    """⑤ 시점 추천 — 학년 스테이지별 고정 ranked list."""
    return [dict(item) for item in CURRENT_RECOMMENDATION[_stage(getattr(user, 'grade', None))]]


# ─── ⑥ 띵똥이에게 바로 물어보기 (칩) ─────────────────────────────────────
# 탭하면 prompt가 챗(POST /chat-rooms/{id}/messages/)으로 전송 (#164/#171 패턴).

QUICK_QUESTIONS_BASE = [
    {'label': '해외 인턴 준비 방법 보기', 'prompt': '해외 인턴십은 어떻게 준비하면 좋을까?'},
    {'label': '영어 공부 루틴 추천 받기', 'prompt': '교환학생·해외 인턴을 위한 영어 공부 루틴 추천해줘'},
]


def build_quick_questions(user) -> list:
    """⑥ 칩 — 고정 2개 + 3번째는 target_job 동선(없으면 일반 진로 칩)."""
    chips = [dict(c) for c in QUICK_QUESTIONS_BASE]
    job = (getattr(user, 'target_job', '') or '').strip()
    if job:  # 디자인 "클라우드 취업 로드맵" = target_job='클라우드 개발자'
        chips.append({'label': f'{job} 취업 로드맵 다시보기', 'prompt': f'{job} 취업 로드맵 다시 알려줘'})
    else:
        chips.append({'label': '내 진로 로드맵 보기', 'prompt': '내 전공·관심사 기준 취업 로드맵 알려줘'})
    return chips


# ─── 최상위 조립 ─────────────────────────────────────────────────────────

def build_exchange_guide(user) -> dict:
    """교환학생·해외 인턴십 가이드 전체 응답 조립 (#180).

    관심사 신호는 한 번만 뽑아 ③④가 공유. 반환 dict는 ExchangeGuideSerializer 스키마와 일치.
    """
    signal = interest_signal(user)
    return {
        'advice': build_advice(user),
        'necessity': build_necessity(user, signal),
        'evaluation': build_evaluation(user, signal),
        'current_recommendation': build_current_recommendation(user),
        'quick_questions': build_quick_questions(user),
    }
