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

HTML 구조 (2026-05-12 실제 페이지 확인):
- 행: <ul class="list"> > <li> (첫 li는 헤더 .top 클래스)
- 제목/링크: <div class="tit"> > <a href="...&ix=NNNN">제목 <span class="stat">뱃지</span></a>
- 분야: <div class="tit"> > <div class="sub-tit">분야 : A, B, C</div>
- 주최: <div class="organ">주최사</div>
- 마감: <div class="day">D-N <span class="dday ing|soon|future|end">상태</span></div>
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Iterable, Optional

from .base import BaseInformationCrawler, CrawledInformation

logger = logging.getLogger(__name__)


# 위비티 분야 키워드 → 우리 모델 카테고리(공모전/대외활동/지원사업/교육·강의/부트캠프).
# 위비티 sub-tit 텍스트(예: "분야 : 기획/아이디어, 대외활동/서포터즈, 취업/창업")에서
# 키워드가 등장하면 해당 카테고리로 매핑.
WEVITY_FIELD_KEYWORDS = {
    '대외활동': '대외활동',
    '서포터즈': '대외활동',
    '봉사': '대외활동',
    '취업': '지원사업',
    '창업': '지원사업',
    '장학': '지원사업',
    '지원사업': '지원사업',
    '교육': '교육·강의',
    '강의': '교육·강의',
    '아카데미': '교육·강의',
    '부트캠프': '부트캠프',
    '캠프': '부트캠프',
}

# 활성/비활성 상태 라벨 (span.dday 클래스)
INACTIVE_DDAY_CLASSES = {'end'}  # 그 외(ing, soon, future)는 활성


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
                    break

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
    # 목록 파싱 — 실제 위비티 HTML 구조 기반
    # ─────────────────────────────────────────────────────────────────────

    # 위비티 URL의 공모전 고유 ID(ix) 추출 — (source, source_id) upsert 키
    _IX_RE = re.compile(r'[?&]ix=(\d+)')

    @classmethod
    def _extract_ix(cls, href: str) -> Optional[str]:
        m = cls._IX_RE.search(href)
        return m.group(1) if m else None

    def parse_list(self, html: str) -> Iterable[dict]:
        soup = self.soup(html)

        # 헤더 li.top 제외하고 데이터 행만
        for row in soup.select('ul.list > li'):
            if 'top' in row.get('class', []):
                continue

            link = row.select_one('div.tit > a')
            if not link:
                continue

            href = (link.get('href') or '').strip()
            if not href:
                continue

            ix = self._extract_ix(href)
            if not ix:
                # ix가 없는 행은 unique 키 만들 수 없음 → skip (이론상 발생 안 함)
                continue

            title = self._extract_title(link)
            if not title:
                continue

            field_text = self._text(row, 'div.tit > div.sub-tit')
            dday_span = row.select_one('div.day span.dday')
            dday_classes = dday_span.get('class', []) if dday_span else []
            day_text = self._text(row, 'div.day')

            yield {
                'title': title,
                'url': self._absolute_url(href),
                'source': self.SOURCE,        # 'wevity'
                'source_id': ix,              # 위비티 공모전 고유 ID
                'organizer': self._text(row, 'div.organ'),
                'end_date': self._parse_dday(day_text),
                'is_active': not bool(set(dday_classes) & INACTIVE_DDAY_CLASSES),
                'categories': self._map_field_to_categories(field_text),
            }

    @staticmethod
    def _extract_title(link) -> str:
        """링크 텍스트에서 SPECIAL 같은 뱃지(span.stat) 텍스트 제외."""
        # span.stat 뱃지를 복제본에서 제거 후 텍스트 추출
        clone = BeautifulSoupClone.copy(link)
        for stat in clone.select('span.stat'):
            stat.decompose()
        return ' '.join(clone.get_text().split())

    # ─────────────────────────────────────────────────────────────────────
    # 상세 파싱 — 본문은 즉시 폐기, 메타만 저장
    # ─────────────────────────────────────────────────────────────────────

    def parse_detail(self, item: dict, html: str) -> CrawledInformation:
        """상세 페이지에서 메타 정보만 추출. description은 강제로 빈 문자열.

        상세 페이지에서 보강 가능한 정보:
          - 정확한 start_date / end_date (목록은 D-N 근사값만)
          - organizer (목록에 이미 있지만 상세에 더 정확한 표기 가능)

        ⚠️ 상세 페이지 HTML fixture 미확보 — 셀렉터는 일반적 패턴 추정.
           실제 fixture 확보 후 검증·보완 필요.
        """
        soup = self.soup(html)

        # 메타 보강 (상세에서 더 정확한 정보가 있으면 우선 사용)
        start_date = self._extract_detail_start_date(soup) or item.get('start_date')
        end_date = (
            self._extract_detail_end_date(soup) or item.get('end_date')
        )
        organizer = (
            self._extract_detail_organizer(soup) or item.get('organizer') or ''
        )

        # categories는 목록에서 이미 정확함 (sub-tit 기반)
        categories = item.get('categories') or ['공모전']

        # is_active는 end_date 기반 재판정 (상세에서 더 정확한 end_date 가능)
        if end_date is not None:
            is_active = end_date >= date.today()
        else:
            is_active = item.get('is_active', True)

        return CrawledInformation(
            title=item['title'],
            url=item['url'],
            source=item['source'],         # parse_list가 채운 'wevity'
            source_id=item['source_id'],   # parse_list가 채운 ix
            organizer=organizer,
            description='',  # 개인정보 보호 정책 — 본문 저장 금지
            start_date=start_date,
            end_date=end_date,
            categories=categories,
            is_active=is_active,
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

    @classmethod
    def _map_field_to_categories(cls, sub_tit_text: str) -> list[str]:
        """위비티 sub-tit("분야 : A, B, C")의 분야 키워드를 모델 카테고리로 매핑.

        위비티 사이트 자체가 공모전 위주라 기본 '공모전'은 항상 포함.
        대외활동/서포터즈, 취업/창업 등 추가 키워드가 있으면 더 붙임.
        """
        result: set[str] = {'공모전'}  # 위비티는 기본적으로 공모전 사이트
        if sub_tit_text:
            for keyword, mapped in WEVITY_FIELD_KEYWORDS.items():
                if keyword in sub_tit_text:
                    result.add(mapped)
        return sorted(result)

    # 날짜 파싱 — 위비티 표기 다양성 흡수
    _DDAY_PATTERN = re.compile(r'D-(\d+)')
    _DATE_PATTERNS = [
        (re.compile(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})'), 'ymd'),
        (re.compile(r'(\d{1,2})[.\-/](\d{1,2})(?!\d)'), 'md'),
    ]

    @classmethod
    def _parse_dday(cls, text: str) -> Optional[date]:
        """D-N 형식 → 오늘 + N일."""
        if not text:
            return None
        m = cls._DDAY_PATTERN.search(text)
        if m:
            return date.today() + timedelta(days=int(m.group(1)))
        return cls._parse_date_loose(text)

    @classmethod
    def _parse_date_loose(cls, text: str) -> Optional[date]:
        """절대 날짜(YYYY.MM.DD 또는 MM.DD) 파싱."""
        if not text:
            return None
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

    # ─────────────────────────────────────────────────────────────────────
    # 상세 페이지 메타 추출 — div.info > ul.cd-info-list > li 구조
    # ─────────────────────────────────────────────────────────────────────

    @classmethod
    def _detail_info_map(cls, soup) -> dict[str, str]:
        """div.info > ul.cd-info-list > li를 {라벨: 값} 딕셔너리로 변환.

        각 li 구조:
          <li[class]>
            <span class="tit">라벨</span>
            값 텍스트 (혹은 <a>, <span class="cil-dday">D-N</span> 등)
          </li>
        """
        result: dict[str, str] = {}
        for li in soup.select('div.info ul.cd-info-list > li'):
            label_el = li.select_one('span.tit')
            if not label_el:
                continue
            label = label_el.get_text(strip=True)
            if not label:
                continue
            # li 전체 텍스트에서 라벨 + 부가 라벨(D-N 등) 제거 → 값만 남김
            full = li.get_text(' ', strip=True)
            value = full
            for el in li.select('span.tit, span.cil-dday'):
                value = value.replace(el.get_text(strip=True), '', 1)
            result[label] = cls.normalize_text(value)
        return result

    # 접수기간 텍스트에서 두 날짜 추출: "2026-04-27 ~ 2026-05-25"
    _PERIOD_RE = re.compile(
        r'(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})\s*[~\-]\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})'
    )

    def _extract_detail_start_date(self, soup) -> Optional[date]:
        period = self._detail_info_map(soup).get('접수기간', '')
        m = self._PERIOD_RE.search(period)
        return self._parse_date_loose(m.group(1)) if m else None

    def _extract_detail_end_date(self, soup) -> Optional[date]:
        period = self._detail_info_map(soup).get('접수기간', '')
        m = self._PERIOD_RE.search(period)
        return self._parse_date_loose(m.group(2)) if m else None

    def _extract_detail_organizer(self, soup) -> str:
        info_map = self._detail_info_map(soup)
        # '주최/주관' 라벨 우선, 없으면 '주최'
        return (
            info_map.get('주최/주관')
            or info_map.get('주최')
            or info_map.get('주관')
            or ''
        )


class BeautifulSoupClone:
    """selector로 추출한 노드를 안전하게 복제해 일부만 제거할 때 사용.

    BeautifulSoup의 element를 직접 변경하면 원본 soup이 손상되므로,
    문자열로 직렬화 → 다시 파싱해 격리된 복제본을 만든다.
    """

    @staticmethod
    def copy(element):
        from bs4 import BeautifulSoup
        return BeautifulSoup(str(element), 'lxml')
