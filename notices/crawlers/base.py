from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup
from django.db import IntegrityError, transaction

from notices.models import Notice

logger = logging.getLogger(__name__)


@dataclass
class CrawledNotice:
    """크롤러가 반환하는 단일 공지 표준 dict (spec 8.4.1).

    DB 저장 직전 단계의 표준 포맷. Notice 모델 필드와 1:1 대응.
    """
    source: str
    title: str
    url: str
    published_at: datetime
    content: str = ''
    end_date: Optional[date] = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'source': self.source,
            'title': self.title,
            'url': self.url,
            'published_at': self.published_at.isoformat(),
            'content': self.content,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'tags': list(self.tags),
        }


@dataclass
class CrawlResult:
    """단일 크롤러 실행 결과 집계."""
    source: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped + self.failed


class BaseNoticeCrawler:
    """공지 크롤러 베이스 클래스.

    서브클래스가 구현할 메서드:
      - SOURCE: SOURCE_CHOICES 중 하나
      - LIST_URL: 게시판 목록 페이지 URL
      - parse_list(html): 목록 페이지에서 항목 dict iterable 반환
      - parse_detail(item, html): 상세 페이지에서 CrawledNotice 반환
        (목록만으로 충분하면 fetch_detail=False로 두고 parse_list에서 직접 반환)
    """

    SOURCE: str = ''
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

    # --- HTTP ---

    def fetch(self, url: str) -> str:
        response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
        response.raise_for_status()
        # 학교 사이트는 EUC-KR/UTF-8 혼재 가능 → apparent_encoding 보정
        if response.encoding is None or response.encoding.lower() == 'iso-8859-1':
            response.encoding = response.apparent_encoding
        return response.text

    # --- 파싱 (서브클래스 구현) ---

    def parse_list(self, html: str) -> Iterable[dict]:
        """목록 페이지 HTML → 각 항목의 부분 정보 dict iterable.

        반환되는 dict는 최소한 'url' 또는 fetch_detail=False면 CrawledNotice 전체를
        반환할 수 있도록 한다. 서브클래스가 구체 형태를 결정.
        """
        raise NotImplementedError

    def parse_detail(self, item: dict, html: str) -> CrawledNotice:
        """상세 페이지 HTML + 목록 항목 dict → CrawledNotice."""
        raise NotImplementedError

    # --- 실행 ---

    def crawl(self) -> Iterable[CrawledNotice]:
        """목록 → (선택) 상세 → CrawledNotice 스트림."""
        list_html = self.fetch(self.LIST_URL)
        for item in self.parse_list(list_html):
            try:
                if self.fetch_detail:
                    detail_html = self.fetch(item['url'])
                    yield self.parse_detail(item, detail_html)
                else:
                    yield self._item_to_notice(item)
            except Exception:
                logger.exception(
                    '[%s] 상세 파싱 실패: %s', self.SOURCE, item.get('url')
                )

    def _item_to_notice(self, item: dict) -> CrawledNotice:
        """fetch_detail=False일 때 목록 dict를 CrawledNotice로 변환."""
        return CrawledNotice(
            source=self.SOURCE,
            title=item['title'],
            url=item['url'],
            published_at=item['published_at'],
            content=item.get('content', ''),
            end_date=item.get('end_date'),
            tags=item.get('tags', []),
        )

    # --- 저장 ---

    def save(self, notices: Iterable[CrawledNotice]) -> CrawlResult:
        result = CrawlResult(source=self.SOURCE)
        for notice in notices:
            try:
                _, created = self._upsert(notice)
                if created:
                    result.created += 1
                else:
                    result.updated += 1
            except IntegrityError:
                logger.exception(
                    '[%s] 저장 실패 (IntegrityError): %s',
                    self.SOURCE, notice.url,
                )
                result.failed += 1
            except Exception:
                logger.exception(
                    '[%s] 저장 실패: %s', self.SOURCE, notice.url,
                )
                result.failed += 1
        return result

    @transaction.atomic
    def _upsert(self, notice: CrawledNotice) -> tuple[Notice, bool]:
        """(source, url) 기준 upsert."""
        defaults = {
            'title': notice.title,
            'content': notice.content,
            'published_at': notice.published_at,
            'end_date': notice.end_date,
            'tags': list(notice.tags),
        }
        return Notice.objects.update_or_create(
            source=notice.source,
            url=notice.url,
            defaults=defaults,
        )

    # --- 엔트리포인트 ---

    def run(self) -> CrawlResult:
        """크롤링 + 저장 한번에. management command에서 사용."""
        logger.info('[%s] 크롤링 시작 (%s)', self.SOURCE, self.LIST_URL)
        result = self.save(self.crawl())
        logger.info(
            '[%s] 완료 - created=%d updated=%d failed=%d',
            self.SOURCE, result.created, result.updated, result.failed,
        )
        return result

    # --- 유틸 ---

    @staticmethod
    def soup(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, 'lxml')

    @staticmethod
    def normalize_text(text: str) -> str:
        return ' '.join((text or '').split())
