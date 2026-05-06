"""명지대학교 K2Web Wizard 기반 공지 게시판 크롤러.

학사공지/일반공지/행사공지/장학공지/진로공지/학생활동공지 모두
동일한 K2Web 게시판 HTML 구조를 사용하므로
SOURCE + LIST_URL만 다른 서브클래스로 6개 공지 게시판을 처리한다.

대상 URL 형식: https://www.mju.ac.kr/mjukr/{board_id}/subview.do
페이지네이션: ?page=N
상세 페이지: /bbs/mjukr/.../artclView.do (목록의 a.artclLinkView href)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterable, Optional
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from django.utils import timezone

from .base import BaseNoticeCrawler, CrawledNotice

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.mju.ac.kr'

# 목록의 작성일 컬럼은 'YYYY.MM.DD' 형식
DATE_PATTERN = re.compile(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})')


class MJUNoticeBoardCrawler(BaseNoticeCrawler):
    """K2Web Wizard 게시판 공통 파서.

    서브클래스는 SOURCE 와 LIST_URL 만 지정하면 된다.
    """

    # 페이지 1만 크롤링 (매일 03:00 정기 실행 → 새 글은 항상 1페이지 상단에 노출).
    # 대량 백필이 필요하면 management command 옵션으로 max_pages 늘림.
    DEFAULT_MAX_PAGES = 1
    fetch_detail = True

    def __init__(self, max_pages: Optional[int] = None) -> None:
        super().__init__()
        self.max_pages = max_pages or self.DEFAULT_MAX_PAGES

    # ---- crawl 흐름 ----

    def crawl(self) -> Iterable[CrawledNotice]:
        seen_urls: set[str] = set()  # pinned 행이 페이지마다 중복되므로 dedupe
        for page in range(1, self.max_pages + 1):
            try:
                list_html = self.fetch(self._page_url(page))
            except Exception:
                logger.exception('[%s] 목록 페이지 fetch 실패: page=%d',
                                 self.SOURCE, page)
                continue

            for item in self.parse_list(list_html):
                if item['url'] in seen_urls:
                    continue
                seen_urls.add(item['url'])
                try:
                    detail_html = self.fetch(item['url'])
                    yield self.parse_detail(item, detail_html)
                except Exception:
                    logger.exception('[%s] 상세 파싱 실패: %s',
                                     self.SOURCE, item['url'])

    def _page_url(self, page: int) -> str:
        if page <= 1:
            return self.LIST_URL
        sep = '&' if '?' in self.LIST_URL else '?'
        return f'{self.LIST_URL}{sep}page={page}'

    # ---- 목록 파싱 ----

    def parse_list(self, html: str) -> Iterable[dict]:
        soup = self.soup(html)
        rows = soup.select('table.artclTable tbody tr')
        for tr in rows:
            link = tr.select_one('td._artclTdTitle a.artclLinkView')
            date_td = tr.select_one('td._artclTdRdate')
            if not link or not date_td:
                continue
            href = link.get('href') or ''
            if not href:
                continue
            url = urljoin(BASE_URL, href)
            title_el = link.select_one('strong') or link
            title = self.normalize_text(title_el.get_text(' ', strip=True))
            published_at = self._parse_date(date_td.get_text(strip=True))
            if not title or not published_at:
                continue
            yield {
                'url': url,
                'title': title,
                'published_at': published_at,
            }

    # ---- 상세 파싱 ----

    def parse_detail(self, item: dict, html: str) -> CrawledNotice:
        soup = self.soup(html)

        # 본문: div.artclView (없으면 빈 문자열)
        body_el = soup.select_one('div.artclView')
        content = ''
        image_urls: list[str] = []
        if body_el:
            content = self.normalize_text(body_el.get_text(' ', strip=True))
            # 본문 영역의 <img> URL 절대경로로 수집 (spec 9.1.6 VLM 입력용).
            # 한글 등 비-ASCII 문자는 percent-encoding으로 정규화해야
            # OpenAI VLM이 fetch 가능. 같은 이미지 dedupe (순서 보존).
            seen: set[str] = set()
            for img in body_el.select('img'):
                src = (img.get('src') or '').strip()
                if not src:
                    continue
                abs_url = self._encode_url(urljoin(BASE_URL, src))
                if abs_url in seen:
                    continue
                seen.add(abs_url)
                image_urls.append(abs_url)

        # 상세 페이지에 더 정확한 제목이 있으면 그것을 사용
        detail_title = None
        h2 = soup.select_one('h2.artclTitle, header.artclHead h2, h2')
        if h2:
            detail_title = self.normalize_text(h2.get_text(' ', strip=True))

        title = detail_title or item['title']

        return CrawledNotice(
            source=self.SOURCE,
            title=title[:300],
            url=item['url'],
            published_at=item['published_at'],
            content=content,
            end_date=None,  # 본문에서 마감일 추출은 후속 AI 파이프라인에서 처리
            tags=[],
            image_urls=image_urls,
        )

    # ---- 유틸 ----

    @staticmethod
    def _encode_url(url: str) -> str:
        """URL의 path/query에 한글이 포함된 경우 percent-encoding으로 정규화.

        OpenAI 등 외부 서비스에서 image_url을 fetch할 때 RFC 3986 이외 문자가
        들어있으면 실패하므로 안전하게 인코딩해서 저장.
        """
        parts = urlsplit(url)
        path = quote(parts.path, safe='/%')
        query = quote(parts.query, safe='=&%')
        return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))

    @staticmethod
    def _parse_date(text: str) -> Optional[datetime]:
        """'2026.04.27' 형식 → timezone-aware datetime (00:00 KST)."""
        m = DATE_PATTERN.search(text or '')
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
        try:
            naive = datetime(y, mo, d, 0, 0, 0)
        except ValueError:
            return None
        tz = timezone.get_current_timezone()
        return timezone.make_aware(naive, tz)


# ---- 게시판별 서브클래스 ----


class MjuAcademicNoticeCrawler(MJUNoticeBoardCrawler):
    """학사공지 게시판."""
    SOURCE = 'academic'
    LIST_URL = 'https://www.mju.ac.kr/mjukr/257/subview.do'


class MjuGeneralNoticeCrawler(MJUNoticeBoardCrawler):
    """일반공지 게시판."""
    SOURCE = 'general'
    LIST_URL = 'https://www.mju.ac.kr/mjukr/255/subview.do'


class MjuEventNoticeCrawler(MJUNoticeBoardCrawler):
    """행사공지 게시판."""
    SOURCE = 'event'
    LIST_URL = 'https://www.mju.ac.kr/mjukr/256/subview.do'


class MjuScholarshipNoticeCrawler(MJUNoticeBoardCrawler):
    """장학/학자금공지 게시판."""
    SOURCE = 'scholarship'
    LIST_URL = 'https://www.mju.ac.kr/mjukr/259/subview.do'


class MjuCareerNoticeCrawler(MJUNoticeBoardCrawler):
    """진로/취업/창업공지 게시판."""
    SOURCE = 'career'
    LIST_URL = 'https://www.mju.ac.kr/mjukr/260/subview.do'


class MjuStudentActivityNoticeCrawler(MJUNoticeBoardCrawler):
    """학생활동공지 게시판."""
    SOURCE = 'student_activity'
    LIST_URL = 'https://www.mju.ac.kr/mjukr/5364/subview.do'
