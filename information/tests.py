from datetime import date

from django.test import TestCase

from information.crawlers.base import BaseInformationCrawler, CrawledInformation
from information.models import Information


class InformationUpsertTests(TestCase):
    """Information은 (source, source_id) unique → upsert 동작 검증."""

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
            'source': 'mju',
            'source_id': 'mju-1',
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

    def test_같은_source_source_id는_updated(self):
        self.crawler.save([self._make_information(title='원본')])
        result = self.crawler.save([
            self._make_information(title='수정됨', url='https://changed/url'),
        ])
        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        # url이 다르더라도 (source, source_id) 같으면 같은 row
        self.assertEqual(Information.objects.count(), 1)
        self.assertEqual(Information.objects.get().title, '수정됨')
        self.assertEqual(Information.objects.get().url, 'https://changed/url')

    def test_같은_source_다른_source_id는_별개_row(self):
        self.crawler.save([self._make_information(source_id='1', url='https://x/1')])
        self.crawler.save([self._make_information(source_id='2', url='https://x/2')])
        self.assertEqual(Information.objects.count(), 2)

    def test_source_또는_source_id_누락은_ValueError(self):
        with self.assertRaises(ValueError):
            self._make_information(source='')
        with self.assertRaises(ValueError):
            self._make_information(source_id='')


class CrawledInformationDictTests(TestCase):
    def test_to_dict_포맷(self):
        info = CrawledInformation(
            title='공모전',
            url='https://x',
            source='wevity',
            source_id='12345',
            organizer='주최',
            description='설명',
            start_date=date(2026, 5, 1),
            end_date=date(2026, 6, 1),
            categories=['공모전'],
        )
        result = info.to_dict()
        self.assertEqual(result['source'], 'wevity')
        self.assertEqual(result['source_id'], '12345')
        self.assertEqual(result['start_date'], '2026-05-01')
        self.assertEqual(result['end_date'], '2026-06-01')
        self.assertEqual(result['categories'], ['공모전'])
        self.assertTrue(result['is_active'])

    def test_to_dict_source_누락은_ValueError(self):
        with self.assertRaises(ValueError):
            CrawledInformation(title='x', url='y', source='', source_id='1')
