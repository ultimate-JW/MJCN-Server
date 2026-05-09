"""사이트별 크롤러 레지스트리. 새 크롤러는 여기에 등록."""
from typing import Type

from .base import BaseInformationCrawler

# 학교 자체 정보(공모전·대외활동·지원사업 등) 게시판 크롤러는 URL 확정 후 추가 예정.
# 예시:
#   from .mju_information import MjuInformationCrawler

CRAWLERS: list[Type[BaseInformationCrawler]] = [
    # MjuInformationCrawler,
]


def get_crawlers(sources: list[str] | None = None) -> list[Type[BaseInformationCrawler]]:
    if not sources:
        return list(CRAWLERS)
    return [c for c in CRAWLERS if c.SOURCE in sources]
