"""prune_information cron 테스트 (spec 5.5 — 1년 보관 정책)."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from information.models import Information


def make_info(**overrides):
    defaults = {
        'title': '테스트',
        'organizer': '주최',
        'description': '',
        'url': 'https://www.wevity.com/?c=find&s=2&i=1',
        'start_date': None,
        'end_date': None,
        'categories': ['공모전'],
        'is_active': False,
    }
    defaults.update(overrides)
    return Information.objects.create(**defaults)


class PruneInformationTests(TestCase):
    """기본 365일 보관 + wevity 출처 한정 동작."""

    def setUp(self):
        today = timezone.localdate()
        # 1년 1일 지난 위비티 항목 — 삭제 대상
        self.expired_wevity = make_info(
            title='오래된 위비티',
            url='https://www.wevity.com/?c=find&s=2&i=100',
            end_date=today - timedelta(days=366),
        )
        # 1년 안에 끝난 위비티 항목 — 보존
        self.recent_wevity = make_info(
            title='최근 위비티',
            url='https://www.wevity.com/?c=find&s=2&i=200',
            end_date=today - timedelta(days=100),
        )
        # end_date NULL 위비티 — 보존
        self.nodate_wevity = make_info(
            title='상시모집 위비티',
            url='https://www.wevity.com/?c=find&s=2&i=300',
            end_date=None,
        )
        # 다른 도메인(학교 자체) — 보존 (source=wevity 필터)
        self.expired_school = make_info(
            title='오래된 학교',
            url='https://www.mju.ac.kr/contest/1',
            end_date=today - timedelta(days=400),
        )

    def test_기본_365일_위비티_만_삭제(self):
        out = StringIO()
        call_command('prune_information', stdout=out)
        self.assertEqual(Information.objects.count(), 3)
        self.assertFalse(
            Information.objects.filter(id=self.expired_wevity.id).exists()
        )
        # 나머지 3건 모두 보존
        self.assertTrue(
            Information.objects.filter(id=self.recent_wevity.id).exists()
        )
        self.assertTrue(
            Information.objects.filter(id=self.nodate_wevity.id).exists()
        )
        self.assertTrue(
            Information.objects.filter(id=self.expired_school.id).exists()
        )

    def test_dry_run은_삭제_안_함(self):
        out = StringIO()
        call_command('prune_information', '--dry-run', stdout=out)
        self.assertEqual(Information.objects.count(), 4)
        self.assertIn('dry-run', out.getvalue())
        self.assertIn('1건', out.getvalue())

    def test_days_옵션으로_보관_기간_조정(self):
        # --days 30 → 30일보다 오래된 wevity 항목 모두 삭제
        # → expired_wevity (366일) + recent_wevity (100일) 둘 다 삭제
        out = StringIO()
        call_command('prune_information', '--days', '30', stdout=out)
        self.assertFalse(
            Information.objects.filter(id=self.expired_wevity.id).exists()
        )
        self.assertFalse(
            Information.objects.filter(id=self.recent_wevity.id).exists()
        )
        # NULL인 nodate_wevity는 여전히 보존
        self.assertTrue(
            Information.objects.filter(id=self.nodate_wevity.id).exists()
        )

    def test_end_date_null은_삭제_안_함(self):
        # --days 0 으로 강제 — 그래도 NULL은 보존
        out = StringIO()
        call_command('prune_information', '--days', '0', stdout=out)
        self.assertTrue(
            Information.objects.filter(id=self.nodate_wevity.id).exists()
        )

    def test_학교_자체_데이터는_영향_없음(self):
        # default source=wevity 라서 mju.ac.kr 도메인은 매칭 안 됨
        out = StringIO()
        call_command('prune_information', '--days', '0', stdout=out)
        self.assertTrue(
            Information.objects.filter(id=self.expired_school.id).exists()
        )

    def test_days_음수는_에러(self):
        err = StringIO()
        call_command('prune_information', '--days', '-1', stderr=err)
        self.assertIn('0 이상', err.getvalue())
