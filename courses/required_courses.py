"""필수 이수 과목 강제 — 이수현황 응답의 required_courses 분해용
(graduation_requirements.md §4.4, §5.1).

CompletionStatusView가 학생의 CourseHistory와 cross-check해
각 과목의 이수 여부를 응답에 노출한다.

표 순서를 유지하기 위해 list로 박는다 (set은 major_required.py 별도).
"""


# 학칙 §5.1 — 학과별 전공필수 과목 (graduation_requirements.md §5.1 표 순서 유지)
MAJOR_REQUIRED_COURSES_BY_MAJOR = {
    '컴퓨터공학전공': [
        'C언어', '객체지향프로그래밍1', '자료구조', '컴퓨터하드웨어',
        '운영체제', '소프트웨어공학', '알고리즘', '캡스톤디자인',
    ],
}


# 학칙 §4.4 — 학과별 학문기초교양 강제 과목 (graduation_requirements.md §4.4)
LIBERAL_FOUNDATION_COURSES_BY_MAJOR = {
    '컴퓨터공학전공': [
        '미적분학1', '통계학개론', '공학수학1', '이산수학개론', '선형대수학개론',
    ],
}


def get_required_courses(category, major):
    """카테고리 + 학과로 필수 과목명 list 반환. 매핑 없으면 None."""
    if category == '전공필수':
        return MAJOR_REQUIRED_COURSES_BY_MAJOR.get(major)
    if category == '학문기초교양':
        return LIBERAL_FOUNDATION_COURSES_BY_MAJOR.get(major)
    return None
