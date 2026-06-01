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
    return sum(1 for kw in user_keywords if _tokenize(kw) & tag_tokens)


def sort_by_match(
    items: list,
    user_keywords: set[str],
    tags_attr: str = 'tags',
    *,
    secondary_key: Callable | None = None,
) -> list:
    """match_score 부여 후 점수 내림차순 정렬.

    Args:
        items: list of model instances (각 instance에 tags_attr 필드 존재)
        user_keywords: extract_user_keywords() 결과
        tags_attr: 콘텐츠 태그 필드명 ('tags' 또는 'categories' 등)
        secondary_key: 동점일 때 사용할 정렬 키 (예: lambda x: -x.published_at.timestamp())
                       반환값이 작을수록 위로 (Python sorted 기본 동작과 일치)

    Returns:
        새 list. 각 item에 `match_score` 속성 추가됨 (모델 인스턴스 attribute).
        DB 저장은 안 함 — 응답 직렬화 단계에서만 사용.
    """
    scored = []
    for item in items:
        tags = getattr(item, tags_attr, None) or []
        item.match_score = score_match(user_keywords, tags)
        scored.append(item)

    # 정렬 키: (-점수, secondary_key)
    # -점수로 내림차순, 동점이면 secondary_key 적용
    if secondary_key:
        scored.sort(key=lambda x: (-x.match_score, secondary_key(x)))
    else:
        scored.sort(key=lambda x: -x.match_score)

    return scored
