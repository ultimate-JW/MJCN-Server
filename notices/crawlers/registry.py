"""사이트별 크롤러 레지스트리.

새 크롤러 추가 시 여기에 등록만 하면 management command가 자동 인식한다.
"""
from typing import Type

from .base import BaseNoticeCrawler
from .mju_board import (
    MjuAcademicNoticeCrawler,
    MjuCareerNoticeCrawler,
    MjuEventNoticeCrawler,
    MjuGeneralNoticeCrawler,
    MjuScholarshipNoticeCrawler,
    MjuStudentActivityNoticeCrawler,
)

CRAWLERS: list[Type[BaseNoticeCrawler]] = [
    MjuAcademicNoticeCrawler,
    MjuGeneralNoticeCrawler,
    MjuEventNoticeCrawler,
    MjuScholarshipNoticeCrawler,
    MjuCareerNoticeCrawler,
    MjuStudentActivityNoticeCrawler,
]


def get_crawlers(sources: list[str] | None = None) -> list[Type[BaseNoticeCrawler]]:
    """전체 크롤러 또는 source 필터링한 크롤러 리스트 반환."""
    if not sources:
        return list(CRAWLERS)
    return [c for c in CRAWLERS if c.SOURCE in sources]
