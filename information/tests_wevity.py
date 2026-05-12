"""위비티 크롤러 테스트 (개인정보 보호 정책 검증 포함)."""
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase

from information.crawlers.wevity import WevityCrawler
from information.models import Information


# 위비티 페이지 구조 추정 fixture (실제 페이지 확인 후 갱신 필요)
SAMPLE_LIST_HTML = """
<html>
<body>
<ul class="list">
  <li>
    <a href="?c=find&s=2&cidx=20&i=12345">2026 AI 챌린지 공모전</a>
    <span class="organ">한국정보과학회</span>
    <span class="date">~05.31</span>
  </li>
  <li>
    <a href="?c=find&s=2&cidx=20&i=12346">웹 개발 해커톤 모집</a>
    <span class="organ">테크스타트업</span>
    <span class="date">D-7</span>
  </li>
  <li>
    <!-- 링크 없는 행 — 무시되어야 함 -->
    <span>제목만 있는 행</span>
  </li>
</ul>
</body>
</html>
"""

SAMPLE_DETAIL_HTML = """
<html>
<body>
<div class="content">
  <h1>2026 AI 챌린지 공모전</h1>
  <table>
    <tr><th>주최</th><td>한국정보과학회</td></tr>
    <tr><th>접수기간</th><td>2026-05-01 ~ 2026-05-31</td></tr>
  </table>
  <p>본 공모전은 공모전 분야에서 진행됩니다. 학생 누구나 참여 가능.</p>
  <p>개인정보가 들어갈 수 있는 본문 내용입니다.
     이름: 홍길동, 전화: 010-1234-5678 같은 게 들어있을 수 있음.</p>
</div>
</body>
</html>
"""

EMPTY_LIST_HTML = '<html><body><ul class="list"></ul></body></html>'


class WevityURLBuildingTests(TestCase):
    """URL 생성 — cidx, gp 파라미터 부착."""

    def test_list_url_cidx_gp(self):
        c = WevityCrawler()
        self.assertEqual(
            c._build_list_url(20, 1),
            'https://www.wevity.com/?c=find&s=1&gub=1&cidx=20&gp=1',
        )
        self.assertEqual(
            c._build_list_url(21, 3),
            'https://www.wevity.com/?c=find&s=1&gub=1&cidx=21&gp=3',
        )

    def test_absolute_url_relative_query(self):
        self.assertEqual(
            WevityCrawler._absolute_url('?c=find&s=2&i=123'),
            'https://www.wevity.com/?c=find&s=2&i=123',
        )

    def test_absolute_url_path(self):
        self.assertEqual(
            WevityCrawler._absolute_url('/contest/view/123'),
            'https://www.wevity.com/contest/view/123',
        )

    def test_absolute_url_already_absolute(self):
        self.assertEqual(
            WevityCrawler._absolute_url('https://www.wevity.com/x'),
            'https://www.wevity.com/x',
        )


class WevityDateParsingTests(TestCase):
    """위비티 표기 다양성 흡수 — D-7, 05.31, 2026-05-31 등."""

    def test_dday_format(self):
        result = WevityCrawler._parse_date_loose('D-7')
        self.assertEqual(result, date.today() + timedelta(days=7))

    def test_md_only(self):
        # 연도 없는 표기는 올해로 가정
        result = WevityCrawler._parse_date_loose('~05.31')
        self.assertEqual(result.month, 5)
        self.assertEqual(result.day, 31)
        self.assertEqual(result.year, date.today().year)

    def test_ymd_dot(self):
        self.assertEqual(
            WevityCrawler._parse_date_loose('2026.05.31'),
            date(2026, 5, 31),
        )

    def test_ymd_hyphen(self):
        self.assertEqual(
            WevityCrawler._parse_date_loose('2026-05-31'),
            date(2026, 5, 31),
        )

    def test_empty(self):
        self.assertIsNone(WevityCrawler._parse_date_loose(''))
        self.assertIsNone(WevityCrawler._parse_date_loose('마감'))


class WevityParseListTests(TestCase):
    """목록 파싱 — 유효한 링크만 추출."""

    def setUp(self):
        self.crawler = WevityCrawler()

    def test_링크_있는_행만_추출(self):
        items = list(self.crawler.parse_list(SAMPLE_LIST_HTML))
        # 링크 있는 2개만, 링크 없는 행은 무시
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['title'], '2026 AI 챌린지 공모전')
        self.assertEqual(items[1]['title'], '웹 개발 해커톤 모집')

    def test_url은_절대경로(self):
        items = list(self.crawler.parse_list(SAMPLE_LIST_HTML))
        for item in items:
            self.assertTrue(item['url'].startswith('https://www.wevity.com/'))

    def test_주최자_추출(self):
        items = list(self.crawler.parse_list(SAMPLE_LIST_HTML))
        self.assertEqual(items[0]['organizer'], '한국정보과학회')

    def test_빈_목록은_빈_iterable(self):
        items = list(self.crawler.parse_list(EMPTY_LIST_HTML))
        self.assertEqual(items, [])


class WevityParseDetailTests(TestCase):
    """상세 파싱 — 개인정보 보호 정책 검증."""

    def setUp(self):
        self.crawler = WevityCrawler()
        self.item = {
            'title': '2026 AI 챌린지 공모전',
            'url': 'https://www.wevity.com/?c=find&s=2&i=12345',
            'organizer': '한국정보과학회',
            'end_date': None,
        }

    def test_description은_항상_빈_문자열(self):
        """⭐ 핵심 정책 — 본문에 무엇이 있든 description은 빈 채로 저장."""
        info = self.crawler.parse_detail(self.item, SAMPLE_DETAIL_HTML)
        self.assertEqual(info.description, '')

    def test_본문에_개인정보가_있어도_저장되지_않음(self):
        """개인정보 보호 — 본문의 '홍길동', '010-1234-5678'가 저장되어선 안 됨."""
        info = self.crawler.parse_detail(self.item, SAMPLE_DETAIL_HTML)
        # 메타 어디에도 본문 텍스트가 누출되지 않아야 함
        for field in (info.title, info.organizer, info.description):
            self.assertNotIn('홍길동', field)
            self.assertNotIn('010-1234-5678', field)

    def test_접수기간_파싱(self):
        info = self.crawler.parse_detail(self.item, SAMPLE_DETAIL_HTML)
        self.assertEqual(info.start_date, date(2026, 5, 1))
        self.assertEqual(info.end_date, date(2026, 5, 31))

    def test_주최자_상세에서_보강(self):
        # 목록에서 organizer 비어있어도 상세에서 채워야 함
        item = {**self.item, 'organizer': ''}
        info = self.crawler.parse_detail(item, SAMPLE_DETAIL_HTML)
        self.assertEqual(info.organizer, '한국정보과학회')

    def test_categories_매핑(self):
        # 본문에 '공모전' 키워드 → categories=['공모전']
        info = self.crawler.parse_detail(self.item, SAMPLE_DETAIL_HTML)
        self.assertIn('공모전', info.categories)

    def test_categories_없으면_기본값_공모전(self):
        empty_html = '<html><body>아무 분류 키워드 없음</body></html>'
        info = self.crawler.parse_detail(self.item, empty_html)
        self.assertEqual(info.categories, ['공모전'])


class WevityIsActiveTests(TestCase):
    """is_active 마감일 기준 판정."""

    def test_마감일_없으면_활성(self):
        self.assertTrue(WevityCrawler._is_active(None))

    def test_미래_마감이면_활성(self):
        future = date.today() + timedelta(days=10)
        self.assertTrue(WevityCrawler._is_active(future))

    def test_오늘_마감이면_활성(self):
        self.assertTrue(WevityCrawler._is_active(date.today()))

    def test_과거_마감이면_비활성(self):
        past = date.today() - timedelta(days=1)
        self.assertFalse(WevityCrawler._is_active(past))


class WevityCrawlIterationTests(TestCase):
    """cidx × 페이지 이중 루프 동작."""

    def test_빈_페이지_만나면_break(self):
        crawler = WevityCrawler()
        crawler.MAX_PAGES = 3

        # cidx=20: gp=1 데이터 있음, gp=2 빈 페이지
        # cidx=21: gp=1 빈 페이지부터
        page_responses = {
            'cidx=20&gp=1': SAMPLE_LIST_HTML,
            'cidx=20&gp=2': EMPTY_LIST_HTML,
            'cidx=21&gp=1': EMPTY_LIST_HTML,
        }

        def fake_fetch(url):
            for key, html in page_responses.items():
                if key in url:
                    return html
            return SAMPLE_DETAIL_HTML  # 상세 페이지 fetch

        with patch.object(crawler, 'fetch', side_effect=fake_fetch):
            items = list(crawler.crawl())

        # cidx=20 gp=1에서 2개 추출, cidx=21은 즉시 종료 → 총 2개
        self.assertEqual(len(items), 2)


class WevityIntegrationSaveTests(TestCase):
    """fetch만 mock하고 save까지 통합 — DB 멱등성 검증."""

    def test_run_full_flow(self):
        crawler = WevityCrawler()
        crawler.MAX_PAGES = 1
        # cidx=20만 데이터, cidx=21은 빈 결과
        page_responses = {
            'cidx=20&gp=1': SAMPLE_LIST_HTML,
            'cidx=21&gp=1': EMPTY_LIST_HTML,
        }

        def fake_fetch(url):
            for key, html in page_responses.items():
                if key in url:
                    return html
            return SAMPLE_DETAIL_HTML

        with patch.object(crawler, 'fetch', side_effect=fake_fetch):
            result = crawler.run()

        self.assertEqual(result.created, 2)
        self.assertEqual(result.failed, 0)
        # 저장된 description이 모두 빈 문자열
        descriptions = list(Information.objects.values_list('description', flat=True))
        self.assertEqual(descriptions, ['', ''])

        # 재실행 시 url 기준 updated (멱등)
        with patch.object(crawler, 'fetch', side_effect=fake_fetch):
            result2 = crawler.run()
        self.assertEqual(result2.created, 0)
        self.assertEqual(result2.updated, 2)
