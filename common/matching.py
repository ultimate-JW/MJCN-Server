"""사용자 관심사 ↔ 콘텐츠 태그 매칭 로직 (spec 5.10).

공지·정보 맞춤형 보기 + 대시보드 관심사 기반 노출에 공통 사용.

핵심 함수:
  - extract_user_keywords(user) → set[str]
  - score_match(user_keywords, content_tags) → int
  - sort_by_match(items, user_keywords, tags_attr) → list  (점수 부여 + 정렬)
"""
from __future__ import annotations

import re
from typing import Callable, Iterable


# 키워드 토큰 분리 구분자 — 콤마, 공백, 슬래시, 가운뎃점.
# custom_text 자유 입력 분리 + 복합 라벨/전공 토큰화에 공통 사용 (spec 5.10.3).
_TOKEN_SPLIT_RE = re.compile(r'[,\s/·]+')


def _tokenize(value: str) -> set[str]:
    """단일 문자열을 구분자로 쪼갠 정규화(lower/strip) 토큰 set.

    '공기업/공공기관' → {'공기업', '공공기관'}
    '반도체·ICT대학 · 컴퓨터공학전공' → {'반도체', 'ict대학', '컴퓨터공학전공'}
    """
    return {
        tok.strip().lower()
        for tok in _TOKEN_SPLIT_RE.split(value or '')
        if tok.strip()
    }


def extract_user_keywords(user) -> set[str]:
    """사용자 관심사 키워드 집합 추출 (spec 5.10.1).

    출처:
      - User.major (전공명 그대로)
      - InterestArea.category (FK 1:N — 모든 카테고리)
      - InterestArea.custom_text (FK 1:N — 콤마/공백 분리)

    Returns:
        set of lowercase-stripped keyword strings.
        매칭 시 대소문자 무관 처리하려고 lower로 정규화.
    """
    keywords: set[str] = set()

    # User.major
    major = getattr(user, 'major', None)
    if major:
        keywords.add(major.strip())

    # InterestArea 일괄 조회 (related_name='interests', accounts/models.py)
    # N+1 회피 가능하면 호출자가 prefetch
    interests = getattr(user, 'interests', None)
    if interests is None:
        return _normalize(keywords)

    for area in interests.all():
        # category (선택형 직업군)
        cat = (getattr(area, 'category', '') or '').strip()
        if cat:
            keywords.add(cat)

        # custom_text (자유 텍스트)
        custom = (getattr(area, 'custom_text', '') or '').strip()
        if custom:
            for token in _TOKEN_SPLIT_RE.split(custom):
                token = token.strip()
                if token:
                    keywords.add(token)

    return _normalize(keywords)


def _normalize(keywords: set[str]) -> set[str]:
    """매칭 정확도 + 대소문자 무관 처리. 빈 문자열 제거."""
    return {k.lower() for k in keywords if k}


# InterestArea 12개 고정 카테고리(소문자 라벨) → 도메인 동의어 토큰 집합 (spec 5.10.3).
# 굵은 카테고리 라벨('IT/개발')과 LLM이 뽑는 세밀 도메인 태그('AI') 사이 어휘 격차를
# 메운다. 카테고리 라벨에만 적용 — 전공명·custom_text 자유 텍스트는 확장하지 않음.
# 값은 _tokenize 결과 토큰과 비교되므로 모두 소문자 단일 토큰. 항목은 이 파일에서 관리.
CATEGORY_SYNONYMS: dict[str, set[str]] = {
    'it/개발': {'ai', '인공지능', '머신러닝', '딥러닝', '데이터', '빅데이터', '소프트웨어',
               'sw', '개발', '개발자', '프로그래밍', '코딩', '백엔드', '프론트엔드',
               '웹', '앱', '클라우드', '보안', 'it'},
    '디자인': {'ux', 'ui', '그래픽', 'bx', '브랜딩', '시각디자인', '영상', '디자인'},
    '마케팅/광고': {'마케팅', '광고', '브랜드', 'sns', '퍼포먼스', '홍보'},
    '금융/회계': {'금융', '회계', '재무', '투자', '핀테크', '세무'},
    '교육': {'교육', '강의', '멘토링', '진로체험', '교직'},
    '공기업/공공기관': {'공기업', '공공기관', '공무원', '행정', '정부', '공공'},
    '의료/바이오': {'의료', '바이오', '제약', '헬스케어', '간호', '생명', '의학'},
    '미디어/콘텐츠': {'미디어', '콘텐츠', '영상', '방송', '크리에이터', '유튜브'},
    '건축/공간': {'건축', '공간', '인테리어', '도시', '설계', '조경'},
    '스포츠/예술': {'스포츠', '예술', '체육', '음악', '미술', '공연'},
    '연구/r&d': {'연구', 'r&d', '논문', '실험', '연구원', '학술'},
    '기타': set(),
}


def score_match(user_keywords: set[str], content_tags: Iterable[str]) -> int:
    """토큰 단위로 매칭된 관심사 수 = 점수 (spec 5.10.3).

    사용자 키워드·콘텐츠 태그를 구분자로 토큰화한 뒤, 각 관심사가 태그 토큰과
    하나라도 겹치면 1점. 복합 라벨('공기업/공공기관')·전공 통짜 문자열도 토큰으로
    쪼개져 LLM의 단일어 태그('공기업')와 매칭된다.

    토큰 단위 '완전 일치'만 인정하므로 부분 문자열 오탐은 없다 ('공기업' ↔
    '공기업체관리'는 매칭 안 됨). 한 관심사는 여러 토큰으로 쪼개져도 최대 1점
    (점수 인플레 방지).

    Args:
        user_keywords: extract_user_keywords() 결과
        content_tags: Notice.tags 또는 Information.categories 같은 list[str]

    Returns:
        매칭된 관심사 수 (0 이상의 정수)
    """
    if not user_keywords or not content_tags:
        return 0
    tag_tokens: set[str] = set()
    for tag in content_tags:
        tag_tokens |= _tokenize(tag)
    if not tag_tokens:
        return 0
    score = 0
    for kw in user_keywords:
        eff = _tokenize(kw)
        # kw가 12개 고정 카테고리 라벨이면 도메인 동의어로 확장 (spec 5.10.3).
        # 전공명·custom_text 등 비카테고리 키워드는 확장 안 됨(정확 토큰 유지).
        eff |= CATEGORY_SYNONYMS.get(kw, set())
        if eff & tag_tokens:
            score += 1
    return score


# 관심사 토큰 → 콘텐츠 제목에 실제 등장하는 표현으로 확장하는 동의어 맵 (spec 5.10.4).
# 배경: 공모전(Information)은 크롤러가 본문을 저장하지 않아 매칭 재료가 'title'뿐인데,
# title은 "OO 경진대회"처럼 포괄적이라 '데이터분석'·'클라우드' 같은 관심사가 거의 안 나온다.
# LLM 태깅(공지가 쓰는 정규화)이 불가능하므로 이 맵이 그 역할을 대신한다.
# 키·값 모두 소문자(_tokenize 결과 토큰과 매칭). 항목 변경은 이 파일에서만 — spec엔 추상만 기록.
SYNONYM_MAP: dict[str, set[str]] = {
    'ai': {'ai', '인공지능', '머신러닝', '딥러닝'},
    '개발': {'개발', '코딩', '프로그래밍', '소프트웨어', 'sw'},
    '게임': {'게임', 'game'},
    '보안': {'보안', '정보보호', 'security', '사이버'},
    '데이터분석': {'데이터', '분석', '빅데이터'},
    '클라우드': {'클라우드', 'aws', 'azure', 'gcp'},
}


def _contains_term(text_lower: str, term: str) -> bool:
    """term이 text_lower 안에 '독립적으로' 등장하면 True (부분일치 + 오매칭 가드).

    앞뒤 문자가 라틴 알파벳이면 매칭 불인정 — 짧은 영문 토큰('it')이 더 긴
    영단어('digital')에 박히는 오매칭을 막는다. 한글 토큰은 앞뒤가 한글이어도
    통과해 부분일치가 유지되고('개발'→'개발자' OK), 영문이 한글에 붙어도 통과한다
    ('ai'↔'제조ai' OK — 앞 '조'가 라틴 알파벳이 아님).
    """
    term = term.strip().lower()
    if not term:
        return False
    # (?<![a-z]) 앞 / (?![a-z]) 뒤 — 인접 문자가 영문 알파벳이 아닐 때만 매칭 인정
    return re.search(rf'(?<![a-z]){re.escape(term)}(?![a-z])', text_lower) is not None


def matched_keywords(
    user_keywords: set[str],
    content_tags: Iterable[str],
    text: str,
    *,
    synonyms: dict[str, set[str]] = SYNONYM_MAP,
) -> list[str]:
    """매칭된 관심사(user_keywords 원소) 리스트 — categories OR title 동의어 (spec 5.10.4).

    각 관심사는 다음 둘 중 하나라도 충족하면 매칭으로 본다:
      (1) 콘텐츠 태그(categories) 토큰과 완전일치 — score_match와 동일 기준
      (2) 제목 등 자유 텍스트에 관심사의 동의어가 부분일치(_contains_term)
    한 관심사는 두 경로에 모두 걸려도 한 번만 포함된다 → "관심사당 최대 1점" 캡.

    공모전 배지(어떤 관심사로 매칭됐는지) 표기에 쓰려고 점수(개수)가 아니라
    매칭된 관심사 자체를 반환한다.
    """
    if not user_keywords:
        return []
    tag_tokens: set[str] = set()
    for tag in (content_tags or []):
        tag_tokens |= _tokenize(tag)
    text_lower = (text or '').lower()

    matched: list[str] = []
    for kw in user_keywords:
        kw_tokens = _tokenize(kw)
        # (1) categories 토큰 완전일치
        if kw_tokens & tag_tokens:
            matched.append(kw)
            continue
        # (2) title 동의어 부분일치 — 토큰을 동의어로 확장 후 하나라도 등장하면 매칭
        candidates: set[str] = set()
        for tok in kw_tokens:
            candidates |= synonyms.get(tok, {tok})
        if any(_contains_term(text_lower, c) for c in candidates):
            matched.append(kw)
    return matched


def score_combined(
    user_keywords: set[str],
    content_tags: Iterable[str],
    text: str,
    *,
    synonyms: dict[str, set[str]] = SYNONYM_MAP,
) -> int:
    """matched_keywords 개수 = 점수 (관심사당 최대 1점, categories+title 합산 캡 유지)."""
    return len(matched_keywords(user_keywords, content_tags, text, synonyms=synonyms))


def sort_by_match(
    items: list,
    user_keywords: set[str],
    tags_attr: str = 'tags',
    *,
    text_attr: str | None = None,
    secondary_key: Callable | None = None,
) -> list:
    """match_score 부여 후 점수 내림차순 정렬.

    Args:
        items: list of model instances (각 instance에 tags_attr 필드 존재)
        user_keywords: extract_user_keywords() 결과
        tags_attr: 콘텐츠 태그 필드명 ('tags' 또는 'categories' 등)
        text_attr: 지정 시 해당 자유 텍스트 필드(예: 공모전 title)도 동의어 부분일치로
                   매칭에 합산 (score_combined, 관심사당 1점 캡). 미지정(None)이면
                   기존 태그 완전일치만 — 공지(notices) 경로는 영향 없음.
        secondary_key: 동점일 때 사용할 정렬 키 (예: lambda x: -x.published_at.timestamp())
                       반환값이 작을수록 위로 (Python sorted 기본 동작과 일치)

    Returns:
        새 list. 각 item에 `match_score` 속성 추가됨 (모델 인스턴스 attribute).
        DB 저장은 안 함 — 응답 직렬화 단계에서만 사용.
    """
    scored = []
    for item in items:
        tags = getattr(item, tags_attr, None) or []
        if text_attr:
            # categories + 제목 동의어를 관심사당 1점으로 결합 (공모전용)
            item.match_score = score_combined(
                user_keywords, tags, getattr(item, text_attr, '') or ''
            )
        else:
            item.match_score = score_match(user_keywords, tags)
        scored.append(item)

    # 정렬 키: (-점수, secondary_key)
    # -점수로 내림차순, 동점이면 secondary_key 적용
    if secondary_key:
        scored.sort(key=lambda x: (-x.match_score, secondary_key(x)))
    else:
        scored.sort(key=lambda x: -x.match_score)

    return scored
