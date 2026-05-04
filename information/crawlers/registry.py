"""사이트별 크롤러 레지스트리. 새 크롤러는 여기에 등록."""
from typing import Type

from .base import BaseContestCrawler

# 학교 자체 공모전 게시판 크롤러는 URL 확정 후 추가 예정.
# 예시:
#   from .mju_contest import MjuContestCrawler

CRAWLERS: list[Type[BaseContestCrawler]] = [
    # MjuContestCrawler,
]


def get_crawlers(sources: list[str] | None = None) -> list[Type[BaseContestCrawler]]:
    if not sources:
        return list(CRAWLERS)
    return [c for c in CRAWLERS if c.SOURCE in sources]
