from datetime import date

from django.test import TestCase

from information.crawlers.base import BaseInformationCrawler, CrawledInformation
from information.models import Information


class InformationUpsertTests(TestCase):
    """Information은 url 단독 unique → upsert 동작 검증."""

    def setUp(self):
        class _DummyCrawler(BaseInformationCrawler):
            SOURCE = 'mju_information'
            LIST_URL = 'https://example.test/list'

            def parse_list(self, html):
                return []

        self.crawler = _DummyCrawler()

    def _make_information(self, **overrides):
        defaults = {
            'title': '공모전',
            'url': 'https://www.mju.ac.kr/information/1',
            'organizer': '학생처',
            'description': '설명',
            'start_date': None,
            'end_date': date(2026, 6, 1),
            'categories': ['공모전'],
            'is_active': True,
        }
        defaults.update(overrides)
        return CrawledInformation(**defaults)

    def test_새_정보는_created(self):
        result = self.crawler.save([self._make_information()])
        self.assertEqual(result.created, 1)
        self.assertEqual(Information.objects.count(), 1)

    def test_같은_url은_updated(self):
        self.crawler.save([self._make_information(title='원본')])
        result = self.crawler.save([self._make_information(title='수정됨')])
        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        self.assertEqual(Information.objects.get().title, '수정됨')


class CrawledInformationDictTests(TestCase):
    def test_to_dict_포맷(self):
        info = CrawledInformation(
            title='공모전',
            url='https://x',
            organizer='주최',
            description='설명',
            start_date=date(2026, 5, 1),
            end_date=date(2026, 6, 1),
            categories=['공모전'],
        )
        result = info.to_dict()
        self.assertEqual(result['start_date'], '2026-05-01')
        self.assertEqual(result['end_date'], '2026-06-01')
        self.assertEqual(result['categories'], ['공모전'])
        self.assertTrue(result['is_active'])
