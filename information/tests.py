from datetime import date

from django.test import TestCase

from information.crawlers.base import BaseContestCrawler, CrawledContest
from information.models import Contest


class ContestUpsertTests(TestCase):
    """Contest는 url 단독 unique → upsert 동작 검증."""

    def setUp(self):
        class _DummyCrawler(BaseContestCrawler):
            SOURCE = 'mju_contest'
            LIST_URL = 'https://example.test/list'

            def parse_list(self, html):
                return []

        self.crawler = _DummyCrawler()

    def _make_contest(self, **overrides):
        defaults = {
            'title': '공모전',
            'url': 'https://www.mju.ac.kr/contest/1',
            'organizer': '학생처',
            'description': '설명',
            'start_date': None,
            'end_date': date(2026, 6, 1),
            'categories': ['공모전'],
            'is_active': True,
        }
        defaults.update(overrides)
        return CrawledContest(**defaults)

    def test_새_정보는_created(self):
        result = self.crawler.save([self._make_contest()])
        self.assertEqual(result.created, 1)
        self.assertEqual(Contest.objects.count(), 1)

    def test_같은_url은_updated(self):
        self.crawler.save([self._make_contest(title='원본')])
        result = self.crawler.save([self._make_contest(title='수정됨')])
        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        self.assertEqual(Contest.objects.get().title, '수정됨')


class CrawledContestDictTests(TestCase):
    def test_to_dict_포맷(self):
        contest = CrawledContest(
            title='공모전',
            url='https://x',
            organizer='주최',
            description='설명',
            start_date=date(2026, 5, 1),
            end_date=date(2026, 6, 1),
            categories=['공모전'],
        )
        result = contest.to_dict()
        self.assertEqual(result['start_date'], '2026-05-01')
        self.assertEqual(result['end_date'], '2026-06-01')
        self.assertEqual(result['categories'], ['공모전'])
        self.assertTrue(result['is_active'])
