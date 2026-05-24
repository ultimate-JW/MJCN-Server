"""교양 4종 분류 매핑 — 학칙 별표4·5 + graduation_requirements.md §3 기준.

학교 강의시간표 엑셀에는 4종(공통/핵심/학문기초/일반) 분류 라벨이 없다.
학과코드 prefix와 교과목명을 보고 우리가 분류한다.

매핑 우선순위 (높은 → 낮은):
  1. by_name — graduation_requirements.md §3.3 명시 학문기초교양 필수 7과목 등 정확 일치
  2. by_prefix — 학과코드 prefix (학칙 §6 표시기호)
  3. None — 매핑 불가 (전공/군사/등 4종 분류 대상 외)

추가/변경 시 graduation_requirements.md 원문 근거를 같이 갱신할 것.
"""


# 학문기초교양 — 컴공 졸업이수가이드 §3.3 명시 필수과목 (2024학번부터)
# 과목명 정확 일치. by_prefix(기자→학문기초)와 결과 동일하지만,
# 학칙 §6 "기○/균○ → 학문기초·일반 둘 다 가능" 모호성 회피용 안전장치.
LIBERAL_FOUNDATION_NAMES = {
    '미적분학1',
    '이산수학개론',
    '선형대수학개론',
    '공학수학1',
    '통계학개론',
}


# 학과코드 prefix → 4종 분류 (학칙 별표4·5, graduation_requirements.md §6 표시기호)
PREFIX_TO_SUBTYPE = {
    '교필': '공통교양',      # 학칙 §6: "교필" = 공통교양 (교양필수 아님)
    '교선': '핵심교양',      # 학칙 §6: "교선" = 핵심교양 (교양선택 아님)
    '기자': '학문기초교양',  # 자연·이공 기초 — 미적분/물리/공학수학/통계 등
    '기컴': '학문기초교양',  # 컴퓨터 기초 — C언어/파이썬/엑셀 등
    '기종': '학문기초교양',  # 종교/철학 기초 — 학칙 표 모호 추정 (보강 필요)
    '균': '일반교양',         # 균형교양 — graduation_requirements.md 정의 부재, 일반교양 추정
}


# 4종 분류 대상 외 — 명시적으로 null 처리 (분류 시도 안 함)
PREFIX_EXCLUDED = {
    '컴공', '컴정', '반아',       # 전공 (전공필수/전공선택은 Course.category로 별도 관리)
    '군과', '군문', '군사', '군업', '군여', '군인',  # 군사학 — 컴공 졸업요건 외
    '예술',                       # 학칙 §6에 명시 없음 — 수동 보강 전까지 분류 보류
}


# 핵심교양 4영역 매핑 — graduation_requirements.md §4.3.1~4.3.4 과목명 → 영역.
# 단일 영역 규칙: 한 과목 = 한 영역. md §4.3 ※ 주석 잔존(창업과 공동체 중복)은 오타 확정,
# `창업과 공동체`는 §4.3.2 사회와 공동체로만 매핑한다.
# 외국인학생전용 과목도 동일 dict에 포함 — 필터링 불필요.
CORE_AREA_BY_NAME = {
    # §4.3.1 역사와 철학
    '철학과 인간': '역사와 철학',
    '한국근현대사의 이해': '역사와 철학',
    '역사와 문명': '역사와 철학',
    '비판적 사고와 논증': '역사와 철학',
    '디지털콘텐츠로 만나는 한국의 문화유산': '역사와 철학',
    '현대인의 삶과 가치': '역사와 철학',
    '외국인학생을 위한 한국현대사': '역사와 철학',
    # §4.3.2 사회와 공동체
    '세계화와 사회변화': '사회와 공동체',
    '민주주의와 현대사회': '사회와 공동체',
    '창업과 공동체': '사회와 공동체',  # md §4.3 ※ 중복 표기는 오타 — 사회 단일
    '사회적 다양성과 공동체': '사회와 공동체',
    '현대사회와 심리학': '사회와 공동체',
    '통합적 커뮤니케이션': '사회와 공동체',
    '디지털사회와 미래 이슈': '사회와 공동체',
    '외국인학생을 위한 한국사회의 이해': '사회와 공동체',
    # §4.3.3 문화와 예술
    '글로벌문화': '문화와 예술',
    '고전으로읽는 인문학': '문화와 예술',
    '예술과 창조성': '문화와 예술',
    'AI와 예술의 만남': '문화와 예술',
    '문화리터러시와 창의적 스토리텔링': '문화와 예술',
    '디지털문화의 이해': '문화와 예술',
    '외국인학생을 위한 한국문화': '문화와 예술',
    # §4.3.4 과학기술과 정보 (구. 과학과 기술)
    '환경과 인간': '과학기술과 정보',
    '우주,생명,마음': '과학기술과 정보',
    '컴퓨팅사고와 코딩입문': '과학기술과 정보',
    '인공지능입문': '과학기술과 정보',
    'AI 사회와 인간': '과학기술과 정보',
    '파이썬을 활용한 데이터분석과 인공지능': '과학기술과 정보',
    '외국인학생을위한컴퓨터활용': '과학기술과 정보',
}


def classify_liberal_subtype(subject_code, course_name):
    """학과코드 + 교과목명 → 4종 분류 또는 None.

    None을 반환하면 Course.liberal_subtype은 비어 있어야 한다
    (전공/군사 등 4종 분류 대상 외, 또는 매핑 룰 미정).
    """
    name = (course_name or '').strip()
    if name in LIBERAL_FOUNDATION_NAMES:
        return '학문기초교양'  # by_name 매칭 — 학칙 §3.3 필수 7과목

    prefix = (subject_code or '').strip()
    if prefix in PREFIX_EXCLUDED:
        return None  # 전공·군사 등 명시 제외 — by_prefix 평가 skip

    if prefix in PREFIX_TO_SUBTYPE:
        return PREFIX_TO_SUBTYPE[prefix]

    # 균형교양은 '균자' / '균인' / '균공' 등 1글자 더 붙는 코드도 있을 수 있어 prefix startswith로 보강
    if prefix.startswith('균'):
        return PREFIX_TO_SUBTYPE['균']

    return None


def _normalize_name(name):
    """공백 제거 — xlsx는 공백 없이('철학과인간'), md는 공백 있게('철학과 인간') 표기되는 차이 흡수."""
    return (name or '').replace(' ', '').strip()


# 정규화된 매핑 dict — _normalize_name 결과 기준으로 lookup
_CORE_AREA_BY_NORMALIZED = {_normalize_name(k): v for k, v in CORE_AREA_BY_NAME.items()}


def classify_core_area(course_name):
    """과목명 → 핵심교양 4영역 또는 None.

    호출자는 liberal_subtype=='핵심교양'인 행에 한해 이 함수를 부르고
    반환값을 Course.core_area에 채운다. 그 외 행은 호출 자체를 안 한다
    (전공/공통교양 과목명이 우연히 매핑 dict에 들어 있어도 영역 부여 안 됨).

    표기 정규화: 공백 차이만 흡수한다 (xlsx vs md). 글자 자체가 다른 표기
    ('디지털문명의이해' vs '디지털문화의 이해' 등)는 그대로 None 반환 — dict 보강 시그널.
    """
    return _CORE_AREA_BY_NORMALIZED.get(_normalize_name(course_name))
