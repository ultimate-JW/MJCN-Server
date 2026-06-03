"""챗 카테고리 단일 출처 (#220 #10).

분류기 프롬프트(TITLE_CATEGORY_SYSTEM)와 ChatRoom.category choices가 따로 관리되어
카테고리 추가/변경 시 한쪽만 바뀌는 동기화 누락 위험이 있었음. 여기서
(value, 분류 가이드)를 한 번 정의하고 모델 choices·분류기 프롬프트가 모두 파생한다.
"""

# (카테고리 값, 분류 가이드 설명) — 분류기 프롬프트 설명 + 모델 choices의 공통 출처.
CHAT_CATEGORY_GUIDE = [
    ('수강·졸업', '수강신청, 수강정정, 졸업요건, 이수학점, 커리큘럼'),
    ('공지', '학교 공지 질문, 공지 요약/검색 요청'),
    ('장학·등록금', '장학금 신청·조회, 등록금 납부·환불'),
    ('공모전', '공모전, 대외활동, 교내외 프로그램 참가'),
    ('취업·진로', '취업, 인턴, 대학원, 진로 고민'),
    ('일반질문', '학식, 도서관, 시설 등 위 카테고리에 속하지 않는 교내 질문'),
    ('기타', '학교와 무관한 질문 또는 분류 불가'),
]

# 모델 choices용 (value, label) — label은 value와 동일.
CHAT_CATEGORIES = [(value, value) for value, _ in CHAT_CATEGORY_GUIDE]

# 분류기 프롬프트에 끼울 카테고리 설명 블록 (한 줄 = 한 카테고리).
CATEGORY_GUIDE_LINES = '\n'.join(
    f'- "{value}": {desc}' for value, desc in CHAT_CATEGORY_GUIDE
)
