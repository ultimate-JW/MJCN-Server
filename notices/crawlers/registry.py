"""사이트별 크롤러 레지스트리.

새 크롤러 추가 시 여기에 등록만 하면 management command가 자동 인식한다.
실제 크롤러는 URL 확정 후 동일 디렉토리에 모듈로 추가하고 import 한다.
"""
from typing import Type

from .base import BaseNoticeCrawler

# 사이트별 구현체는 URL 확정 후 추가 예정.
# 예시:
#   from .mju_academic import MjuAcademicNoticeCrawler
#   from .mju_general import MjuGeneralNoticeCrawler

CRAWLERS: list[Type[BaseNoticeCrawler]] = [
    # MjuAcademicNoticeCrawler,
    # MjuGeneralNoticeCrawler,
]


def get_crawlers(sources: list[str] | None = None) -> list[Type[BaseNoticeCrawler]]:
    """전체 크롤러 또는 source 필터링한 크롤러 리스트 반환."""
    if not sources:
        return list(CRAWLERS)
    return [c for c in CRAWLERS if c.SOURCE in sources]
