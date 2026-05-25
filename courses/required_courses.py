"""필수 이수 과목 및 학과 인정 prefix
(graduation_requirements.md §4.4 / §5.1).

여러 모듈이 공유:
- import_courses_from_xlsx: 전공필수 라벨 정정 + 학과 인정 prefix
- services: 같은 이름 다른 코드(타과·교양 버전) 추천 후보에서 제외
- CompletionStatusView: 응답 required_courses 분해 (#68)

list가 원천 (graduation_requirements.md 표 순서 유지),
set은 set 연산용 derived.
"""


# 학칙 §5.1 — 학과별 전공필수 과목 (graduation_requirements.md §5.1 표 순서)
MAJOR_REQUIRED_COURSES_BY_MAJOR = {
    '컴퓨터공학전공': [
        'C언어', '객체지향프로그래밍1', '자료구조', '컴퓨터하드웨어',
        '운영체제', '소프트웨어공학', '알고리즘', '캡스톤디자인',
    ],
}

# set 연산용 derived (in / intersection 등)
MAJOR_REQUIRED_BY_MAJOR = {
    major: set(courses) for major, courses in MAJOR_REQUIRED_COURSES_BY_MAJOR.items()
}


# 학칙 §4.4 — 학과별 학문기초교양 강제 과목
LIBERAL_FOUNDATION_COURSES_BY_MAJOR = {
    '컴퓨터공학전공': [
        '미적분학1', '통계학개론', '공학수학1', '이산수학개론', '선형대수학개론',
    ],
}


# 학과별 전공 인정 학과코드 prefix (CLAUDE.md §10).
# 예: 컴공 학생은 학과코드가 컴공/컴정/반아로 시작하는 과목을 전공으로 인정.
MAJOR_DEPT_PREFIXES = {
    '컴퓨터공학전공': ('컴공', '컴정', '반아'),
}


def get_required_courses(category, major):
    """카테고리 + 학과로 필수 과목명 list 반환. 매핑 없으면 None."""
    if category == '전공필수':
        return MAJOR_REQUIRED_COURSES_BY_MAJOR.get(major)
    if category == '학문기초교양':
        return LIBERAL_FOUNDATION_COURSES_BY_MAJOR.get(major)
    return None
