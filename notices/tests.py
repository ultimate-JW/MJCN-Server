from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from notices.crawlers.base import BaseNoticeCrawler, CrawledNotice
from notices.crawlers.mju_board import (
    MjuAcademicNoticeCrawler,
    MJUNoticeBoardCrawler,
)
from notices.models import Notice

FIXTURES_DIR = Path(__file__).parent / 'tests_fixtures'


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding='utf-8')


class NoticeUpsertTests(TestCase):
    """베이스 크롤러의 (source, url) upsert 동작 검증."""

    def setUp(self):
        # 테스트용 더미 크롤러 — fetch/parse는 mock 처리
        class _DummyCrawler(BaseNoticeCrawler):
            SOURCE = 'academic'
            LIST_URL = 'https://example.test/list'

            def parse_list(self, html):
                return []

        self.crawler = _DummyCrawler()
        self.now = timezone.now().replace(microsecond=0)

    def _make_notice(self, **overrides):
        defaults = {
            'source': 'academic',
            'title': '제목',
            'url': 'https://www.mju.ac.kr/notice/1',
            'published_at': self.now,
            'content': '본문',
            'end_date': None,
            'tags': [],
        }
        defaults.update(overrides)
        return CrawledNotice(**defaults)

    def test_새_공지는_created(self):
        result = self.crawler.save([self._make_notice()])
        self.assertEqual(result.created, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(Notice.objects.count(), 1)

    def test_같은_source_url은_updated(self):
        self.crawler.save([self._make_notice(title='원본')])
        result = self.crawler.save([self._make_notice(title='수정됨')])
        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        self.assertEqual(Notice.objects.count(), 1)
        self.assertEqual(Notice.objects.get().title, '수정됨')

    def test_다른_source_같은_url은_별개_공지(self):
        # spec 4.4: unique_together = (source, url) — source가 다르면 다른 row
        self.crawler.save([self._make_notice(source='academic')])

        class _GeneralCrawler(BaseNoticeCrawler):
            SOURCE = 'general'
            LIST_URL = 'https://example.test/g'
            def parse_list(self, html):
                return []

        general = _GeneralCrawler()
        general.save([self._make_notice(source='general')])
        self.assertEqual(Notice.objects.count(), 2)

    def test_저장_실패해도_다음_항목은_계속_진행(self):
        # 한 항목 IntegrityError 발생해도 나머지는 저장돼야 함 (실패 격리)
        good = self._make_notice(url='https://www.mju.ac.kr/notice/1')
        bad = self._make_notice(url='https://www.mju.ac.kr/notice/2')

        from django.db import IntegrityError
        original = Notice.objects.update_or_create
        call_count = {'n': 0}

        def flaky(**kwargs):
            call_count['n'] += 1
            if call_count['n'] == 1:
                raise IntegrityError('forced')
            return original(**kwargs)

        with patch.object(Notice.objects, 'update_or_create', side_effect=flaky):
            result = self.crawler.save([good, bad])

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.created, 1)
        self.assertEqual(Notice.objects.count(), 1)


class CrawledNoticeDictTests(TestCase):
    """CrawledNotice → dict 직렬화 (spec 8.4.1 JSON 단일 포맷)."""

    def test_to_dict_포맷(self):
        notice = CrawledNotice(
            source='academic',
            title='제목',
            url='https://x',
            published_at=datetime(2026, 5, 4, 9, 0, 0),
            content='본문',
            end_date=date(2026, 6, 1),
            tags=['수강신청'],
        )
        result = notice.to_dict()
        self.assertEqual(result['source'], 'academic')
        self.assertEqual(result['published_at'], '2026-05-04T09:00:00')
        self.assertEqual(result['end_date'], '2026-06-01')
        self.assertEqual(result['tags'], ['수강신청'])


class MJUListParserTests(TestCase):
    """K2Web 게시판 목록 파싱."""

    def setUp(self):
        self.crawler = MjuAcademicNoticeCrawler()
        self.list_html = load_fixture('mju_list_sample.html')

    def test_유효한_행만_추출(self):
        items = list(self.crawler.parse_list(self.list_html))
        # 4개 중 href 없는 1개 제외 → 3개
        self.assertEqual(len(items), 3)

    def test_url은_절대경로로_변환(self):
        items = list(self.crawler.parse_list(self.list_html))
        for it in items:
            self.assertTrue(it['url'].startswith('https://www.mju.ac.kr/'))

    def test_제목_추출(self):
        items = list(self.crawler.parse_list(self.list_html))
        self.assertIn(
            '2026학년도 1학기 신·편입학',
            items[0]['title'],
        )

    def test_날짜_파싱_점구분자(self):
        items = list(self.crawler.parse_list(self.list_html))
        first = items[0]
        self.assertEqual(first['published_at'].year, 2026)
        self.assertEqual(first['published_at'].month, 4)
        self.assertEqual(first['published_at'].day, 27)
        # timezone-aware
        self.assertIsNotNone(first['published_at'].tzinfo)

    def test_날짜_파싱_하이픈구분자(self):
        items = list(self.crawler.parse_list(self.list_html))
        # 마지막 항목은 2026-04-09 형식
        last = items[-1]
        self.assertEqual(last['published_at'].month, 4)
        self.assertEqual(last['published_at'].day, 9)


class MJUDetailParserTests(TestCase):
    """K2Web 상세 페이지 파싱."""

    def setUp(self):
        self.crawler = MjuAcademicNoticeCrawler()
        self.detail_html = load_fixture('mju_detail_sample.html')

    def test_상세_본문_추출(self):
        item = {
            'url': 'https://www.mju.ac.kr/bbs/mjukr/143/231465/artclView.do',
            'title': '목록제목',
            'published_at': timezone.now(),
        }
        notice = self.crawler.parse_detail(item, self.detail_html)
        self.assertIn('성적 불인정 신청원', notice.content)
        self.assertIn('교학팀 방문', notice.content)

    def test_상세_제목이_있으면_상세_제목_사용(self):
        item = {
            'url': 'https://www.mju.ac.kr/bbs/mjukr/143/231465/artclView.do',
            'title': '목록제목',
            'published_at': timezone.now(),
        }
        notice = self.crawler.parse_detail(item, self.detail_html)
        # 상세 페이지의 h2.artclTitle 사용
        self.assertIn('2026학년도 1학기', notice.title)

    def test_상세_파싱_결과는_CrawledNotice(self):
        item = {
            'url': 'https://www.mju.ac.kr/bbs/mjukr/143/231465/artclView.do',
            'title': '목록제목',
            'published_at': timezone.now(),
        }
        notice = self.crawler.parse_detail(item, self.detail_html)
        self.assertEqual(notice.source, 'academic')
        self.assertEqual(notice.tags, [])
        self.assertIsNone(notice.end_date)


class MJUPageURLTests(TestCase):
    """페이지네이션 URL 생성."""

    def test_page_1은_원본_URL(self):
        crawler = MjuAcademicNoticeCrawler()
        self.assertEqual(crawler._page_url(1), crawler.LIST_URL)

    def test_page_2는_쿼리_추가(self):
        crawler = MjuAcademicNoticeCrawler()
        self.assertEqual(
            crawler._page_url(2),
            f'{crawler.LIST_URL}?page=2',
        )

    def test_기존_쿼리에는_앰퍼샌드로_연결(self):
        class _C(MJUNoticeBoardCrawler):
            SOURCE = 'academic'
            LIST_URL = 'https://www.mju.ac.kr/test?x=1'

        crawler = _C()
        self.assertEqual(
            crawler._page_url(3),
            'https://www.mju.ac.kr/test?x=1&page=3',
        )


class MJUCrawlIntegrationTests(TestCase):
    """fetch만 mock하고 parse + save까지 통합."""

    def test_crawl_full_flow(self):
        list_html = load_fixture('mju_list_sample.html')
        detail_html = load_fixture('mju_detail_sample.html')

        crawler = MjuAcademicNoticeCrawler(max_pages=1)

        def fake_fetch(url):
            if url == crawler.LIST_URL:
                return list_html
            return detail_html

        with patch.object(crawler, 'fetch', side_effect=fake_fetch):
            result = crawler.run()

        # 유효한 3개 항목 → DB 저장
        self.assertEqual(result.created, 3)
        self.assertEqual(result.failed, 0)
        self.assertEqual(Notice.objects.filter(source='academic').count(), 3)

        # 재실행 시 모두 updated (멱등성)
        with patch.object(crawler, 'fetch', side_effect=fake_fetch):
            result2 = crawler.run()
        self.assertEqual(result2.created, 0)
        self.assertEqual(result2.updated, 3)
        self.assertEqual(Notice.objects.filter(source='academic').count(), 3)
