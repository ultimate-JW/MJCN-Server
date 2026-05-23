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
    '물리학1',
    '물리학실험1',
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
