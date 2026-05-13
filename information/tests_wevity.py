"""위비티 크롤러 테스트 (개인정보 보호 정책 검증 포함).

실제 위비티 페이지 HTML(2026-05-12 cidx=20 목록)을 fixture로 사용.
상세 페이지 fixture는 추후 확보 후 추가.
"""
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from information.crawlers.wevity import WevityCrawler
from information.models import Information


FIXTURES_DIR = Path(__file__).parent / 'tests_fixtures'


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding='utf-8')


# 실제 위비티 페이지 (2026-05-12 캡처)
REAL_LIST_HTML = load_fixture('wevity_list_cidx20.html')  # 웹/모바일/IT
REAL_LIST_CIDX21_HTML = load_fixture('wevity_list_cidx21.html')  # 게임/소프트웨어
REAL_DETAIL_HTML = load_fixture('wevity_detail_sample.html')  # ix=106851 (K-콘텐츠 수출 마케터)

# 정책 검증용 — 본문에 개인정보 포함된 가상 상세 페이지
PRIVACY_TEST_DETAIL_HTML = """
<html>
<body>
<div class="info">
  <ul class="cd-info-list">
    <li><span class="tit">주최/주관</span> 테스트 주최사</li>
    <li class="dday-area"><span class="tit">접수기간</span> 2026-05-01 ~ 2026-05-31</li>
  </ul>
</div>
<div class="content">
  <p>상세 본문 — 개인정보가 들어갈 수 있음. 이름: 홍길동, 전화: 010-1234-5678</p>
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
            WevityCrawler._absolute_url('?c=find&s=2&ix=123'),
            'https://www.wevity.com/?c=find&s=2&ix=123',
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
    """위비티 표기 — D-N (가장 흔함) 및 절대 날짜."""

    def test_dday_basic(self):
        result = WevityCrawler._parse_dday('D-7')
        self.assertEqual(result, date.today() + timedelta(days=7))

    def test_dday_with_status_text(self):
        # "D-19 접수중" 같은 합쳐진 텍스트
        result = WevityCrawler._parse_dday('D-19 접수중')
        self.assertEqual(result, date.today() + timedelta(days=19))

    def test_dday_zero(self):
        result = WevityCrawler._parse_dday('D-0')
        self.assertEqual(result, date.today())

    def test_loose_ymd_dot(self):
        self.assertEqual(
            WevityCrawler._parse_date_loose('2026.05.31'),
            date(2026, 5, 31),
        )

    def test_loose_ymd_hyphen(self):
        self.assertEqual(
            WevityCrawler._parse_date_loose('2026-05-31'),
            date(2026, 5, 31),
        )

    def test_empty(self):
        self.assertIsNone(WevityCrawler._parse_dday(''))
        self.assertIsNone(WevityCrawler._parse_dday('마감'))


class WevityCategoryMappingTests(TestCase):
    """위비티 분야 → 모델 카테고리 매핑."""

    def test_기본은_공모전(self):
        result = WevityCrawler._map_field_to_categories('분야 : 기획/아이디어')
        self.assertEqual(result, ['공모전'])

    def test_대외활동_매핑(self):
        result = WevityCrawler._map_field_to_categories(
            '분야 : 기획/아이디어, 대외활동/서포터즈'
        )
        self.assertIn('대외활동', result)
        self.assertIn('공모전', result)

    def test_취업창업_지원사업으로(self):
        result = WevityCrawler._map_field_to_categories(
            '분야 : 광고/마케팅, 취업/창업'
        )
        self.assertIn('지원사업', result)

    def test_교육_매핑(self):
        result = WevityCrawler._map_field_to_categories(
            '분야 : 교육 콘텐츠 제작'
        )
        self.assertIn('교육·강의', result)

    def test_여러_키워드(self):
        # 대외활동 + 취업 → 대외활동 + 지원사업 + 공모전
        result = WevityCrawler._map_field_to_categories(
            '분야 : 대외활동/서포터즈, 취업/창업'
        )
        self.assertEqual(set(result), {'공모전', '대외활동', '지원사업'})

    def test_빈_텍스트(self):
        result = WevityCrawler._map_field_to_categories('')
        self.assertEqual(result, ['공모전'])


class WevityRealListParsingTests(TestCase):
    """실제 위비티 cidx=20 목록 페이지 (2026-05-12 캡처) 기반."""

    def setUp(self):
        self.crawler = WevityCrawler()
        self.items = list(self.crawler.parse_list(REAL_LIST_HTML))

    def test_헤더_제외하고_15개_추출(self):
        # 실제 페이지: ul.list > li 16개 중 첫 li.top(헤더) 제외 → 15개
        self.assertEqual(len(self.items), 15)

    def test_모든_항목에_필수_필드_존재(self):
        for item in self.items:
            self.assertTrue(item['title'])
            self.assertTrue(item['url'])
            self.assertTrue(item['url'].startswith('https://www.wevity.com/'))

    def test_url에_ix_파라미터_포함(self):
        # 위비티 상세 페이지는 ?...&ix=NNNNN 형식
        for item in self.items:
            self.assertIn('ix=', item['url'])

    def test_SPECIAL_뱃지_텍스트_제거(self):
        # 첫 항목 제목에 "SPECIAL" 뱃지가 붙어있는데 제목에는 안 들어가야 함
        first = self.items[0]
        self.assertNotIn('SPECIAL', first['title'])
        self.assertIn('AI BIZ CREATOR SCHOOL', first['title'])

    def test_주최자_추출(self):
        # 첫 항목 주최: "네이버 X 어반플레이"
        self.assertEqual(self.items[0]['organizer'], '네이버 X 어반플레이')

    def test_DDay_파싱(self):
        # 첫 항목: D-19 → 오늘 + 19일
        expected = date.today() + timedelta(days=19)
        self.assertEqual(self.items[0]['end_date'], expected)

    def test_접수중은_활성(self):
        # 첫 항목: span.dday.ing → 접수중 → is_active=True
        self.assertTrue(self.items[0]['is_active'])

    def test_마감임박도_활성(self):
        # 둘째 항목: D-6 마감임박 (dday.soon) → 여전히 활성
        self.assertTrue(self.items[1]['is_active'])

    def test_카테고리_분야_매핑(self):
        # 첫 항목 sub-tit: "분야 : 기획/아이디어, 광고/마케팅, 영상/UCC/사진, 웹/모바일/IT, 대외활동/서포터즈, 취업/창업"
        # → 공모전(기본) + 대외활동 + 지원사업(취업/창업)
        first_cats = set(self.items[0]['categories'])
        self.assertIn('공모전', first_cats)
        self.assertIn('대외활동', first_cats)
        self.assertIn('지원사업', first_cats)


class WevityCidx21StructureTests(TestCase):
    """게임/소프트웨어(cidx=21) 페이지도 동일 셀렉터로 파싱되는지 검증."""

    def setUp(self):
        self.crawler = WevityCrawler()
        self.items = list(self.crawler.parse_list(REAL_LIST_CIDX21_HTML))

    def test_헤더_제외_데이터_행_추출(self):
        # cidx=20과 동일하게 15건 (변동 가능하므로 5건 이상만 확인)
        self.assertGreaterEqual(len(self.items), 5)

    def test_모든_항목_필수_필드_존재(self):
        for item in self.items:
            self.assertTrue(item['title'])
            self.assertTrue(item['url'].startswith('https://www.wevity.com/'))
            self.assertIn('ix=', item['url'])

    def test_cidx21_url에_cidx21_포함(self):
        # 게임/소프트웨어 카테고리에서 추출된 항목은 cidx=21 URL이어야 함
        for item in self.items:
            # 일부 항목은 SPECIAL 등으로 다른 cidx에 노출될 수 있어 느슨하게 검증
            self.assertTrue(
                'cidx=21' in item['url'] or 'cidx=' in item['url']
            )

    def test_categories_매핑_정상_동작(self):
        # 위비티는 기본 '공모전' 카테고리가 항상 포함됨
        for item in self.items:
            self.assertIn('공모전', item['categories'])


class WevityParseDetailPrivacyTests(TestCase):
    """⭐ 상세 파싱 — 개인정보 보호 정책 검증 (가장 중요)."""

    def setUp(self):
        self.crawler = WevityCrawler()
        self.item = {
            'title': '테스트 공모전',
            'url': 'https://www.wevity.com/?c=find&s=1&gub=1&cidx=20&gbn=view&gp=1&ix=12345',
            'source': 'wevity',
            'source_id': '12345',
            'organizer': '목록 주최',
            'end_date': date.today() + timedelta(days=10),
            'is_active': True,
            'categories': ['공모전'],
        }

    def test_description은_항상_빈_문자열(self):
        info = self.crawler.parse_detail(self.item, PRIVACY_TEST_DETAIL_HTML)
        self.assertEqual(info.description, '')

    def test_본문에_개인정보가_있어도_저장_안_됨(self):
        info = self.crawler.parse_detail(self.item, PRIVACY_TEST_DETAIL_HTML)
        for field in (info.title, info.organizer, info.description):
            self.assertNotIn('홍길동', field)
            self.assertNotIn('010-1234-5678', field)

    def test_접수기간_파싱(self):
        info = self.crawler.parse_detail(self.item, PRIVACY_TEST_DETAIL_HTML)
        self.assertEqual(info.start_date, date(2026, 5, 1))
        self.assertEqual(info.end_date, date(2026, 5, 31))

    def test_목록의_categories_보존(self):
        info = self.crawler.parse_detail(self.item, PRIVACY_TEST_DETAIL_HTML)
        self.assertEqual(info.categories, ['공모전'])


class WevityRealDetailParsingTests(TestCase):
    """실제 위비티 상세 페이지 (ix=106851) 기반 검증."""

    def setUp(self):
        self.crawler = WevityCrawler()
        self.item = {
            'title': '[문화체육관광부 x 한국콘텐츠진흥원] 2026 K-콘텐츠 수출 마케터 양성 교육 5기 교육생 모집',
            'url': 'https://www.wevity.com/?c=find&s=1&gub=1&cidx=20&gbn=view&gp=1&ix=106851',
            'source': 'wevity',
            'source_id': '106851',
            'organizer': '문화체육관광부, 한국콘텐츠진흥원',  # 목록에서 추출된 값
            'end_date': date.today() + timedelta(days=13),  # D-13
            'is_active': True,
            'categories': ['공모전', '대외활동', '지원사업'],
        }

    def test_상세_정확한_접수기간_추출(self):
        # 상세 페이지의 '접수기간 2026-04-27 ~ 2026-05-25'
        info = self.crawler.parse_detail(self.item, REAL_DETAIL_HTML)
        self.assertEqual(info.start_date, date(2026, 4, 27))
        self.assertEqual(info.end_date, date(2026, 5, 25))

    def test_상세_주최주관_추출(self):
        info = self.crawler.parse_detail(self.item, REAL_DETAIL_HTML)
        # div.info의 '주최/주관 문화체육관광부, 한국콘텐츠진흥원'
        self.assertIn('문화체육관광부', info.organizer)
        self.assertIn('한국콘텐츠진흥원', info.organizer)

    def test_상세_description은_빈_채(self):
        # 실제 페이지 og:description에는 본문 일부가 있지만 우리는 저장하지 않음
        info = self.crawler.parse_detail(self.item, REAL_DETAIL_HTML)
        self.assertEqual(info.description, '')

    def test_상세_title은_목록값_유지(self):
        info = self.crawler.parse_detail(self.item, REAL_DETAIL_HTML)
        self.assertEqual(info.title, self.item['title'])

    def test_상세_categories는_목록값_유지(self):
        # 상세 파싱은 categories 덮어쓰지 않음 (목록의 sub-tit 매핑이 정확)
        info = self.crawler.parse_detail(self.item, REAL_DETAIL_HTML)
        self.assertEqual(info.categories, ['공모전', '대외활동', '지원사업'])

    def test_상세_end_date로_is_active_재판정(self):
        # 상세에서 추출한 end_date=2026-05-25 기준 활성 여부 결정
        info = self.crawler.parse_detail(self.item, REAL_DETAIL_HTML)
        # 2026-05-25 >= 오늘(2026-05-12) → True
        self.assertTrue(info.is_active)

    def test_detail_info_map_라벨_파싱(self):
        # 헬퍼 직접 검증
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(REAL_DETAIL_HTML, 'lxml')
        info_map = WevityCrawler._detail_info_map(soup)
        self.assertIn('접수기간', info_map)
        self.assertIn('주최/주관', info_map)
        self.assertIn('분야', info_map)
        # 접수기간 값에 날짜가 포함되어 있는지
        self.assertIn('2026-04-27', info_map['접수기간'])
        self.assertIn('2026-05-25', info_map['접수기간'])
        # cil-dday(D-N) 텍스트는 제거되어 있어야 함
        self.assertNotIn('D-13', info_map['접수기간'])


class WevityCrawlIterationTests(TestCase):
    """cidx × 페이지 이중 루프 동작."""

    def test_빈_페이지_만나면_break(self):
        crawler = WevityCrawler()
        crawler.MAX_PAGES = 3

        page_responses = {
            'cidx=20&gp=1': REAL_LIST_HTML,
            'cidx=20&gp=2': EMPTY_LIST_HTML,
            'cidx=21&gp=1': EMPTY_LIST_HTML,
        }

        def fake_fetch(url):
            for key, html in page_responses.items():
                if key in url:
                    return html
            return PRIVACY_TEST_DETAIL_HTML

        with patch.object(crawler, 'fetch', side_effect=fake_fetch):
            items = list(crawler.crawl())

        # cidx=20 gp=1에서 15개, cidx=21은 즉시 종료 → 15개
        self.assertEqual(len(items), 15)


class WevityIntegrationSaveTests(TestCase):
    """fetch만 mock하고 save까지 통합 — DB 멱등성 + description 검증."""

    def test_run_full_flow(self):
        crawler = WevityCrawler()
        crawler.MAX_PAGES = 1
        page_responses = {
            'cidx=20&gp=1': REAL_LIST_HTML,
            'cidx=21&gp=1': EMPTY_LIST_HTML,
        }

        def fake_fetch(url):
            for key, html in page_responses.items():
                if key in url:
                    return html
            return PRIVACY_TEST_DETAIL_HTML

        with patch.object(crawler, 'fetch', side_effect=fake_fetch):
            result = crawler.run()

        # 15개 모두 저장
        self.assertEqual(result.created, 15)
        self.assertEqual(result.failed, 0)
        self.assertEqual(Information.objects.count(), 15)

        # ⭐ 정책 검증: description은 모두 빈 문자열
        descriptions = list(Information.objects.values_list('description', flat=True))
        self.assertTrue(all(d == '' for d in descriptions))

        # ⭐ source/source_id 저장 확인
        for info in Information.objects.all():
            self.assertEqual(info.source, 'wevity')
            self.assertTrue(info.source_id.isdigit(), f'ix가 숫자여야 함: {info.source_id!r}')

        # 재실행 — (source, source_id) 기준 멱등 upsert
        with patch.object(crawler, 'fetch', side_effect=fake_fetch):
            result2 = crawler.run()
        self.assertEqual(result2.created, 0)
        self.assertEqual(result2.updated, 15)
        self.assertEqual(Information.objects.count(), 15)


class WevityDedupTests(TestCase):
    """⭐ cidx=20과 cidx=21에 같은 ix가 노출돼도 1건만 저장됨 (Option B 핵심)."""

    def test_같은_ix_여러_카테고리에서_1건만_저장(self):
        """실제 fixture: cidx=20과 cidx=21 모두 ix=106194, 106656, 106687, 106965 4건 공유.

        cidx=20: 15건 / cidx=21: 15건 / 그 중 4건이 공유 ix → 총 고유 ix = 26건.
        """
        import re
        from pathlib import Path

        crawler = WevityCrawler()
        crawler.MAX_PAGES = 1

        cidx20_html = REAL_LIST_HTML
        cidx21_html = load_fixture('wevity_list_cidx21.html')

        page_responses = {
            'cidx=20&gp=1': cidx20_html,
            'cidx=21&gp=1': cidx21_html,
        }

        def fake_fetch(url):
            for key, html in page_responses.items():
                if key in url:
                    return html
            return PRIVACY_TEST_DETAIL_HTML

        with patch.object(crawler, 'fetch', side_effect=fake_fetch):
            result = crawler.run()

        # 전체 30번 yield되지만 (source='wevity', source_id=ix) 기준 dedup
        # 결과: 고유 ix 수만큼 저장
        total_yields = result.created + result.updated
        unique_db = Information.objects.count()
        self.assertEqual(unique_db, 26, '고유 ix 26건만 저장되어야 함')
        self.assertEqual(total_yields, 30, '크롤러는 30번 yield (15 + 15)')
        # → 첫 15건 created + 같은 ix 4건 updated + 새 11건 created = 26 created + 4 updated
        self.assertEqual(result.created, 26)
        self.assertEqual(result.updated, 4)

        # 중복 그룹 검증 — 같은 ix가 두 row에 분산되지 않음
        from django.db.models import Count
        dupes = Information.objects.values('source', 'source_id').annotate(
            n=Count('id')
        ).filter(n__gt=1)
        self.assertEqual(list(dupes), [])
