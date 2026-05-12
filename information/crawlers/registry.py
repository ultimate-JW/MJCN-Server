"""사이트별 크롤러 레지스트리. 새 크롤러는 여기에 등록."""
from typing import Type

from .base import BaseInformationCrawler
from .wevity import WevityCrawler

CRAWLERS: list[Type[BaseInformationCrawler]] = [
    WevityCrawler,
]


def get_crawlers(sources: list[str] | None = None) -> list[Type[BaseInformationCrawler]]:
    if not sources:
        return list(CRAWLERS)
    return [c for c in CRAWLERS if c.SOURCE in sources]
