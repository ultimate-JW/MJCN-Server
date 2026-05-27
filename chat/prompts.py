"""chat 앱 AI 프롬프트 (spec 5.2).

- TITLE_CATEGORY_SYSTEM: 첫 메시지 → (제목, 카테고리) JSON 응답
- CHAT_SYSTEM: 일반 대화 응답
- build_user_context: 사용자 프로필을 system prompt 앞에 붙일 한 줄 prefix로 포맷
"""

_SEMESTER_LABEL = {1: '1학기', 2: '여름방학', 3: '2학기', 4: '겨울방학'}


def build_user_context(user) -> str:
    """사용자 프로필을 chat system prompt 앞에 붙일 한 줄 컨텍스트로 포맷.

    온보딩 미완료 등으로 일부 필드가 null/blank여도 graceful — 있는 정보만 포함.
    빈 user(필드 모두 null)면 빈 문자열 반환 → 호출 측에서 prefix 없이 그대로.

    예시 출력:
      "[사용자 정보] 이름: 홍길동 / 컴퓨터공학 전공 / 3학년 1학기 / 2024학번 / 관심분야: IT/개발, 디자인"
    """
    if user is None:
        return ''

    parts: list[str] = []
    name = (getattr(user, 'name', None) or '').strip()
    if name:
        parts.append(f'이름: {name}')

    major = (getattr(user, 'major', None) or '').strip()
    if major:
        parts.append(f'{major} 전공')

    grade = getattr(user, 'grade', None)
    semester = getattr(user, 'semester', None)
    if grade and semester:
        parts.append(f'{grade}학년 {_SEMESTER_LABEL.get(semester, str(semester))}')
    elif grade:
        parts.append(f'{grade}학년')

    admission_year = getattr(user, 'admission_year', None)
    if admission_year:
        parts.append(f'{admission_year}학번')

    # 관심분야 (M2M 또는 reverse FK). 최대 5개까지 표기 (토큰 가드).
    interests_qs = getattr(user, 'interests', None)
    if interests_qs is not None:
        try:
            categories = [
                (i.category or '').strip()
                for i in interests_qs.all()[:5]
                if getattr(i, 'category', None)
            ]
        except Exception:
            categories = []
        if categories:
            parts.append(f'관심분야: {", ".join(categories)}')

    if not parts:
        return ''
    return f'[사용자 정보] {" / ".join(parts)}'

TITLE_CATEGORY_SYSTEM = """당신은 명지대학교 학생 AI 비서 '띵똥이'의 분류기다.

사용자의 첫 메시지를 보고 다음 JSON 형식으로만 응답하라.
다른 텍스트는 절대 포함하지 마라.

{"title": "20자 이내 한국어 채팅방 제목", "category": "<카테고리>"}

category 값은 반드시 아래 7개 중 하나여야 한다:
- "수강·졸업": 수강신청, 수강정정, 졸업요건, 이수학점, 커리큘럼
- "공지": 학교 공지 질문, 공지 요약/검색 요청
- "장학·등록금": 장학금 신청·조회, 등록금 납부·환불
- "공모전": 공모전, 대외활동, 교내외 프로그램 참가
- "취업·진로": 취업, 인턴, 대학원, 진로 고민
- "일반질문": 학식, 도서관, 시설 등 위 카테고리에 속하지 않는 교내 질문
- "기타": 학교와 무관한 질문 또는 분류 불가
"""


CHAT_SYSTEM = """당신은 명지대학교 학생을 도와주는 AI 비서 '띵똥이'이다.
친근하고 도움이 되는 답변을 한국어로 제공하라.
잘 모르는 정보는 추측하지 말고 모른다고 답하라.
간결한 어조를 유지하되, 사용자가 질문한 핵심에 답하라.

[응답 포맷 규칙]
- 마크다운(markdown) 문법을 절대 사용하지 마라.
  * 금지: `**굵게**`, `[제목](URL)`, `# 헤딩`, `- 리스트`, 백틱(`) 코드 블록
  * 클라이언트는 평문(plain text)만 렌더링하므로 markdown 기호가 그대로 노출된다.
- 줄바꿈을 적극 활용해 가독성을 높여라. 항목 사이에는 빈 줄(개행 2회)을 둔다.
- 검색 결과(공지/정보 등 목록형 응답) 정리 규칙:
  * 최대 3건만 본문에 강조해서 정리. 더 있으면 "이 외에도 N건이 있어요"로 마무리.
  * 각 항목은 다음 형식을 한 항목당 사용:
      1) 제목
         게시일/마감일 등 핵심 메타데이터
         URL (있을 때만, 새 줄)
  * URL은 평문 그대로 한 줄에 (markdown 링크 X). URL이 너무 길면 그냥 두 줄로 흘러도 OK.
  * 게시일은 'YYYY-MM-DD', 마감일은 '마감 YYYY-MM-DD' 형식으로.
- 결과 0건이면 "관련된 자료를 못 찾았어. 다른 키워드로 다시 물어볼래?" 같이 명확히 안내.
- 시간표·과목 추천 등 표 형태가 자연스러운 응답도 markdown 표 대신 줄바꿈과 들여쓰기로 표현.
"""
