from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup
from django.db import IntegrityError, transaction

from information.models import Information

logger = logging.getLogger(__name__)


@dataclass
class CrawledInformation:
    """크롤러가 반환하는 단일 정보 표준 dict (spec 8.4.1).

    Information 모델 필드와 1:1 대응.
    """
    title: str
    url: str
    organizer: str = ''
    description: str = ''
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    categories: list[str] = field(default_factory=list)
    is_active: bool = True

    def to_dict(self) -> dict:
        return {
            'title': self.title,
            'url': self.url,
            'organizer': self.organizer,
            'description': self.description,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'categories': list(self.categories),
            'is_active': self.is_active,
        }


@dataclass
class CrawlResult:
    source: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped + self.failed


class BaseInformationCrawler:
    """정보 크롤러 베이스 클래스. spec 5.5 - 학교 자체 게시판 한정."""

    SOURCE: str = ''  # 식별용 라벨 (DB 저장 안 함, 로깅용)
    LIST_URL: str = ''
    USER_AGENT: str = 'MJCN-Crawler/1.0 (+https://github.com/ultimate-JW/MJCN-Server)'
    REQUEST_TIMEOUT: int = 10
    fetch_detail: bool = True

    def __init__(self) -> None:
        if not self.SOURCE or not self.LIST_URL:
            raise ValueError(
                f'{type(self).__name__}: SOURCE / LIST_URL must be set'
            )
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': self.USER_AGENT})

    def fetch(self, url: str) -> str:
        response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
        response.raise_for_status()
        if response.encoding is None or response.encoding.lower() == 'iso-8859-1':
            response.encoding = response.apparent_encoding
        return response.text

    def parse_list(self, html: str) -> Iterable[dict]:
        raise NotImplementedError

    def parse_detail(self, item: dict, html: str) -> CrawledInformation:
        raise NotImplementedError

    def crawl(self) -> Iterable[CrawledInformation]:
        list_html = self.fetch(self.LIST_URL)
        for item in self.parse_list(list_html):
            try:
                if self.fetch_detail:
                    detail_html = self.fetch(item['url'])
                    yield self.parse_detail(item, detail_html)
                else:
                    yield self._item_to_information(item)
            except Exception:
                logger.exception(
                    '[%s] 상세 파싱 실패: %s', self.SOURCE, item.get('url')
                )

    def _item_to_information(self, item: dict) -> CrawledInformation:
        return CrawledInformation(
            title=item['title'],
            url=item['url'],
            organizer=item.get('organizer', ''),
            description=item.get('description', ''),
            start_date=item.get('start_date'),
            end_date=item.get('end_date'),
            categories=item.get('categories', []),
            is_active=item.get('is_active', True),
        )

    def save(self, informations: Iterable[CrawledInformation]) -> CrawlResult:
        result = CrawlResult(source=self.SOURCE)
        for info in informations:
            try:
                _, created = self._upsert(info)
                if created:
                    result.created += 1
                else:
                    result.updated += 1
            except IntegrityError:
                logger.exception(
                    '[%s] 저장 실패 (IntegrityError): %s',
                    self.SOURCE, info.url,
                )
                result.failed += 1
            except Exception:
                logger.exception(
                    '[%s] 저장 실패: %s', self.SOURCE, info.url,
                )
                result.failed += 1
        return result

    @transaction.atomic
    def _upsert(self, info: CrawledInformation) -> tuple[Information, bool]:
        """url 기준 upsert."""
        defaults = {
            'title': info.title,
            'organizer': info.organizer,
            'description': info.description,
            'start_date': info.start_date,
            'end_date': info.end_date,
            'categories': list(info.categories),
            'is_active': info.is_active,
        }
        return Information.objects.update_or_create(
            url=info.url,
            defaults=defaults,
        )

    def run(self) -> CrawlResult:
        logger.info('[%s] 크롤링 시작 (%s)', self.SOURCE, self.LIST_URL)
        result = self.save(self.crawl())
        logger.info(
            '[%s] 완료 - created=%d updated=%d failed=%d',
            self.SOURCE, result.created, result.updated, result.failed,
        )
        return result

    @staticmethod
    def soup(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, 'lxml')

    @staticmethod
    def normalize_text(text: str) -> str:
        return ' '.join((text or '').split())
