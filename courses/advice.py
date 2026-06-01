"""② 띵똥이의 조언 + ④ 띵똥이에게 물어보기 문구 조립 (이슈 #164, Figma 160-1144).

수강신청 테마 상세의 띵똥이 카피 2종을 규칙 기반(LLM 없음)으로 만든다.

② 조언 (build_advice):
  stage_message : (학년, 학기) 고정 행동 멘트 — 모든 사용자 동일 (8칸 테이블)
  user_insight  : 추천 결과 신호에서 도출한 근거 멘트 — 사용자마다 다름

④ 물어보기 칩 (build_quick_questions):
  탭하면 prompt가 챗으로 전송되는 숏컷 (label + prompt). 관심분야 칩은 custom_text 치환.

신호 추출은 services에서, phrasing만 이 모듈에서 담당 — 나중에 LLM phrasing으로
갈아끼우려면 build_user_insight / build_quick_questions만 교체하면 된다.
"""
from __future__ import annotations

import re

# 관심분야 칩 치환용 — custom_text 첫 키워드 추출 (구분자: 콤마/공백/슬래시/가운뎃점).
# 표시용이라 services와 달리 lower 안 함 (예: 'AI' 원형 유지).
_KEYWORD_SPLIT_RE = re.compile(r'[,\s/·]+')

# 전공 계열 / 교양 계열 — dominant_category 분기용 (학칙 7분류)
_MAJOR_CATEGORIES = {'전공필수', '전공선택'}
_LIBERAL_CATEGORIES = {'공통교양', '핵심교양', '학문기초교양', '일반교양'}


# (학년, 정규학기) → 행동 멘트. 8칸. 계절학기(3/4)는 직전 정규학기 멘트로 fallback.
STAGE_MESSAGES = {
    (1, 1): "대학 생활을 시작하는 시기예요. 학습 리듬을 잡고 기초 과목을 충실히 들어두세요.",
    (1, 2): "다양한 분야를 경험하며 관심사를 찾기 좋은 시기예요. 교양을 폭넓게 들어보세요.",
    (2, 1): "전공 기초를 다지는 시기예요. 기반이 되는 전공 과목을 탄탄히 쌓아두세요.",
    (2, 2): "전공이 본격화되는 시기예요. 선수과목 흐름을 놓치지 않는 게 중요해요.",
    (3, 1): "취업 준비를 본격적으로 시작할 시점이에요. 전공 심화와 대외활동의 균형이 핵심이에요.",
    (3, 2): "진로 방향을 구체화할 시기예요. 관심 분야 과목과 실무 경험을 함께 챙겨보세요.",
    (4, 1): "졸업을 준비하는 시기예요. 미이수 졸업요건을 점검하고 우선순위를 정하세요.",
    (4, 2): "졸업이 가까운 만큼 남은 필수 요건을 마무리하는 데 집중하세요.",
}
_DEFAULT_STAGE = "이번 학기 목표를 정하고 필요한 과목을 차근차근 채워보세요."


def stage_message(grade, semester):
    """(학년, 학기) 고정 행동 멘트. 학년/학기 미입력 시 기본 멘트."""
    if grade is None or semester is None:
        return _DEFAULT_STAGE
    # 계절학기(3=하계/4=동계)는 직전 정규학기 멘트 재사용
    normal_sem = 1 if semester in (1, 3) else 2
    return STAGE_MESSAGES.get((grade, normal_sem), _DEFAULT_STAGE)


def build_user_insight(signals):
    """추천 신호 → 근거 멘트 1문장 (사용자별).

    signals: {backlog, successor, interest, dominant} (services._recommendation_signals 결과).
    우선순위(시급한 순)로 가장 강한 신호 1개를 골라 '학기' 수식 조각으로 phrasing.
    """
    core = _top_fragment(signals)
    return f"이번 학기는 {core} 학기예요."


def _top_fragment(signals):
    """신호 우선순위로 '학기'를 수식하는 관형구 1개 선택 (시급한 순)."""
    dominant = signals.get('dominant')
    # 1. 밀린 필수 — 가장 시급
    if signals.get('backlog', 0) > 0:
        return "아직 안 들은 전공필수를 채워야 하는"
    # 2. 지난학기 연계 후수과목 — 흐름 유지
    if signals.get('successor', 0) > 0:
        return "지난학기에서 이어지는 전공 과목이 있는"
    # 3. 전공 심화 집중
    if dominant in _MAJOR_CATEGORIES:
        return "전공 심화 수업이 집중된"
    # 4. 관심분야 과목
    if signals.get('interest', 0) > 0:
        return "관심분야 과목을 담을 수 있는"
    # 5. 교양 위주
    if dominant in _LIBERAL_CATEGORIES:
        return "교양을 폭넓게 채우기 좋은"
    # fallback — 신호 없음
    return "여러 선택지가 열려 있는"


def build_advice(user, signals):
    """② 조언 dict — user_insight(파트1) + stage_message(파트2) + 합친 text.

    text는 'OOO님, {파트1} {파트2}' 형식 — 프론트가 text 그대로 쓰거나 파트별로 렌더.
    """
    insight = build_user_insight(signals)
    stage = stage_message(getattr(user, 'grade', None), getattr(user, 'semester', None))
    name = (getattr(user, 'name', '') or '').strip()
    greeting = f"{name}님, " if name else ""
    return {
        'user_insight': insight,
        'stage_message': stage,
        'text': f"{greeting}{insight} {stage}",
    }


def _first_interest_keyword(user):
    """사용자 관심분야 custom_text의 첫 키워드 (표시용 원형). 없으면 None."""
    interests = getattr(user, 'interests', None)
    if interests is None:
        return None
    for area in interests.all():
        text = (getattr(area, 'custom_text', '') or '').strip()
        for tok in _KEYWORD_SPLIT_RE.split(text):
            tok = tok.strip()
            if tok:
                return tok
    return None


def build_quick_questions(user):
    """④ 띵똥이에게 물어보기 칩 3개 — label(버튼 문구) + prompt(챗 전송 문장) (#164).

    탭하면 prompt가 기존 챗 엔드포인트로 전송되는 숏컷. 관심분야 칩은 custom_text
    첫 키워드를 치환 — 없으면 일반 문구로 fallback.
    """
    keyword = _first_interest_keyword(user)
    if keyword:
        interest_chip = {
            'label': f'{keyword} 과목 더 추천',
            'prompt': f'내 관심분야 {keyword} 관련 과목 더 추천해줘',
        }
    else:
        interest_chip = {
            'label': '관심분야 과목 더 추천',
            'prompt': '내 관심분야 관련 과목 더 추천해줘',
        }
    return [
        {
            'label': '추천 과목으로 시간표 짜줘',
            'prompt': '방금 추천받은 과목들로 시간표 안 겹치게 짜줘',
        },
        interest_chip,
        {
            'label': '이번 학기 몇 학점이 적당해?',
            'prompt': '이번 학기 몇 학점 듣는 게 좋을까?',
        },
    ]
