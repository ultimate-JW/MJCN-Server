"""위비티(wevity) 공모전 크롤러 (spec 5.5 / 9.2 / feature #19).

운영팀 회신 정책 (2026-05-12):
- 개인정보 보호: 상세 페이지를 fetch는 하되 본문(description)은 저장하지 않음
  (메타 정보 — title, organizer, start_date, end_date, categories, url 만 저장)
- 보관 1년: 1년 이상 지난 wevity 항목은 prune_information cron이 삭제
- API/RSS 없음 → HTML 크롤링 전용

1차 대상 카테고리 (cidx 파라미터):
- cidx=20: 웹/모바일/IT
- cidx=21: 게임/소프트웨어

페이지네이션: query string `gp=N` (1부터, 빈 페이지 만나면 break)

⚠️ 셀렉터는 위비티 페이지 구조 추정 기반 — 실제 운영 전 fixture 수집해서
   각 select 표현식 검증·보완 필요.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Iterable, Optional

from .base import BaseInformationCrawler, CrawledInformation

logger = logging.getLogger(__name__)


# 위비티 분류 라벨 → 우리 모델 카테고리 매핑.
# 상세 페이지에서 추출되는 분류 텍스트 기준. 매칭 안 되면 '공모전' 기본값.
WEVITY_CATEGORY_MAP = {
    '공모전': '공모전',
    '대외활동': '대외활동',
    '서포터즈': '대외활동',
    '봉사활동': '대외활동',
    '지원사업': '지원사업',
    '창업': '지원사업',
    '장학금': '지원사업',
    '교육': '교육·강의',
    '강의': '교육·강의',
    '아카데미': '교육·강의',
    '부트캠프': '부트캠프',
    '캠프': '부트캠프',
}


class WevityCrawler(BaseInformationCrawler):
    """위비티 공모전 크롤러.

    하나의 인스턴스가 cidx 20/21 두 카테고리를 순회 수집한다.
    카테고리/페이지 이중 루프는 `crawl()`을 오버라이드해서 처리.
    """

    SOURCE = 'wevity'
    LIST_URL = 'https://www.wevity.com/?c=find&s=1&gub=1'  # cidx, gp는 동적으로 부착
    MAX_PAGES = 5  # 안전 상한 (한 카테고리당 최대 페이지)
    fetch_detail = True

    # 수집 대상 cidx 매핑 (로깅·매핑 용도)
    CIDX_TARGETS = {
        20: '웹/모바일/IT',
        21: '게임/소프트웨어',
    }

    # ─────────────────────────────────────────────────────────────────────
    # 오케스트레이션: cidx × 페이지 이중 루프
    # ─────────────────────────────────────────────────────────────────────

    def _build_list_url(self, cidx: int, page: int) -> str:
        # 위비티는 gp=1도 유효함을 가정. 만약 gp 미지정 = 1페이지라면 그대로 동작.
        return f'{self.LIST_URL}&cidx={cidx}&gp={page}'

    def crawl(self) -> Iterable[CrawledInformation]:
        for cidx, label in self.CIDX_TARGETS.items():
            logger.info('[%s] 카테고리 시작: cidx=%d (%s)', self.SOURCE, cidx, label)
            for page in range(1, self.MAX_PAGES + 1):
                list_url = self._build_list_url(cidx, page)
                try:
                    list_html = self.fetch(list_url)
                except Exception:
                    logger.exception(
                        '[%s] 목록 fetch 실패 cidx=%d gp=%d: %s',
                        self.SOURCE, cidx, page, list_url,
                    )
                    break  # 목록 자체가 실패하면 같은 cidx의 다음 페이지도 의미 없음

                items = list(self.parse_list(list_html))
                if not items:
                    logger.info(
                        '[%s] cidx=%d gp=%d 빈 페이지 — 카테고리 종료',
                        self.SOURCE, cidx, page,
                    )
                    break

                for item in items:
                    try:
                        if self.fetch_detail:
                            detail_html = self.fetch(item['url'])
                            yield self.parse_detail(item, detail_html)
                        else:
                            yield self._item_to_information(item)
                    except Exception:
                        logger.exception(
                            '[%s] 상세 파싱 실패: %s',
                            self.SOURCE, item.get('url'),
                        )

    # ─────────────────────────────────────────────────────────────────────
    # 목록 파싱
    # ─────────────────────────────────────────────────────────────────────

    def parse_list(self, html: str) -> Iterable[dict]:
        """목록 페이지에서 각 공모전의 메타만 추출.

        위비티 목록 HTML 구조 추정 — 실제 페이지 검증 후 셀렉터 보완 필요:
          <ul class="list">
            <li>
              <a href="...&i=ID"> 제목 </a>
              <span class="date"> D-7 또는 ~05.20 </span>
              <span class="organ"> 주최자 </span>
            </li>
        """
        soup = self.soup(html)

        # TODO: 실제 위비티 페이지 받으면 셀렉터 확정.
        # 다음 셀렉터들은 흔한 게시판 구조 추정값.
        rows = soup.select('ul.list > li, table.list tbody tr, div.list_item')

        for row in rows:
            link = row.select_one('a[href*="c=find"], a[href*="i="]')
            if not link:
                continue
            href = (link.get('href') or '').strip()
            title = self.normalize_text(link.get_text())
            if not title or not href:
                continue

            yield {
                'title': title,
                'url': self._absolute_url(href),
                'organizer': self._text(row, '.organ, .organizer, .host'),
                'end_date': self._parse_date_loose(
                    self._text(row, '.date, .end, .deadline')
                ),
            }

    # ─────────────────────────────────────────────────────────────────────
    # 상세 파싱 — 본문은 즉시 폐기, 메타만 저장
    # ─────────────────────────────────────────────────────────────────────

    def parse_detail(self, item: dict, html: str) -> CrawledInformation:
        """상세 페이지에서 메타 정보만 추출. description은 강제로 빈 문자열."""
        soup = self.soup(html)

        # 메타 정보 보강 (목록에서 못 얻은 부분만)
        start_date = self._extract_start_date(soup) or item.get('start_date')
        end_date = (
            self._extract_end_date_from_detail(soup) or item.get('end_date')
        )
        organizer = (
            self._extract_organizer_from_detail(soup) or item.get('organizer') or ''
        )
        categories = self._extract_categories(soup) or ['공모전']

        return CrawledInformation(
            title=item['title'],
            url=item['url'],
            organizer=organizer,
            description='',  # 개인정보 보호 정책 — 본문 저장 금지
            start_date=start_date,
            end_date=end_date,
            categories=categories,
            is_active=self._is_active(end_date),
        )

    # ─────────────────────────────────────────────────────────────────────
    # 헬퍼
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _absolute_url(href: str) -> str:
        if href.startswith('http'):
            return href
        if href.startswith('//'):
            return f'https:{href}'
        if href.startswith('?'):
            return f'https://www.wevity.com/{href}'
        if href.startswith('/'):
            return f'https://www.wevity.com{href}'
        return f'https://www.wevity.com/{href}'

    @classmethod
    def _text(cls, element, selector: str) -> str:
        if element is None:
            return ''
        node = element.select_one(selector)
        return cls.normalize_text(node.get_text()) if node else ''

    @staticmethod
    def _is_active(end_date: Optional[date]) -> bool:
        if end_date is None:
            return True
        return end_date >= date.today()

    # 날짜 파싱 — 위비티 표기 다양성 흡수
    _DATE_PATTERNS = [
        (re.compile(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})'), 'ymd'),
        (re.compile(r'(\d{1,2})[.\-/](\d{1,2})(?!\d)'), 'md'),  # 05.20 (연도 없음)
    ]

    @classmethod
    def _parse_date_loose(cls, text: str) -> Optional[date]:
        if not text:
            return None
        text = text.strip()
        # D-X 형식
        m = re.search(r'D-(\d+)', text)
        if m:
            return date.today() + timedelta(days=int(m.group(1)))
        # YYYY.MM.DD 또는 MM.DD
        for pattern, kind in cls._DATE_PATTERNS:
            m = pattern.search(text)
            if not m:
                continue
            try:
                if kind == 'ymd':
                    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if kind == 'md':
                    today = date.today()
                    return date(today.year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                continue
        return None

    # 상세 페이지에서 메타 추출 — 실제 페이지 구조 확인 후 보완 필요
    def _extract_start_date(self, soup) -> Optional[date]:
        # 흔한 패턴: '접수기간 : 2026-05-01 ~ 2026-05-20' 같은 행
        text = soup.get_text(' ', strip=True)
        m = re.search(
            r'접수기간[^0-9]*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})',
            text,
        )
        return self._parse_date_loose(m.group(1)) if m else None

    def _extract_end_date_from_detail(self, soup) -> Optional[date]:
        text = soup.get_text(' ', strip=True)
        m = re.search(
            r'접수기간[^~]*~\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})',
            text,
        )
        return self._parse_date_loose(m.group(1)) if m else None

    def _extract_organizer_from_detail(self, soup) -> str:
        # 흔한 라벨: '주최', '주관'
        for label in ['주최', '주관', '주최기관']:
            node = soup.find(string=re.compile(label))
            if not node:
                continue
            parent = node.parent
            if parent is None:
                continue
            sib = parent.find_next(['td', 'dd', 'span', 'p'])
            if sib:
                txt = self.normalize_text(sib.get_text())
                if txt and txt != label:
                    return txt
        return ''

    def _extract_categories(self, soup) -> list[str]:
        """위비티 분류 라벨을 우리 모델 카테고리로 매핑."""
        text = soup.get_text(' ', strip=True)
        matched: set[str] = set()
        for keyword, mapped in WEVITY_CATEGORY_MAP.items():
            if keyword in text:
                matched.add(mapped)
        return sorted(matched)
