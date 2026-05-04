from datetime import date, datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from notices.crawlers.base import BaseNoticeCrawler, CrawledNotice
from notices.models import Notice


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
