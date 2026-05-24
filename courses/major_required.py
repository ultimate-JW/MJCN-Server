"""학과별 전공필수 과목 + 학과 인정 prefix (graduation_requirements.md §5.1).

import_courses_from_xlsx와 services 양쪽에서 참조.
- import: 전공 파일 행이 매칭되면 category='전공필수'로 정정
- services: 같은 이름 다른 코드(타과·교양 버전)를 학과 학생 추천 후보에서 제외
"""


# 학칙 §5.1 학과별 전공필수 과목명 set
MAJOR_REQUIRED_BY_MAJOR = {
    '컴퓨터공학전공': {
        'C언어', '객체지향프로그래밍1', '자료구조', '컴퓨터하드웨어',
        '운영체제', '소프트웨어공학', '알고리즘', '캡스톤디자인',
    },
}


# 학과별 전공 인정 학과코드 prefix (CLAUDE.md §10).
# 예: 컴공 학생은 학과코드가 컴공/컴정/반아로 시작하는 과목을 전공으로 인정.
MAJOR_DEPT_PREFIXES = {
    '컴퓨터공학전공': ('컴공', '컴정', '반아'),
}
