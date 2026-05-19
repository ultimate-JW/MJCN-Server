"""정보 fanout 알림 생성 로직 테스트 (spec 6.9)."""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import InterestArea
from information.fanout import fanout_new_information
from information.models import Information
from notifications.models import Notification

User = get_user_model()


def make_user(email, *, major='', interests=None, **flags):
    defaults = {
        'notification_enabled': True,
        'notification_information': True,
        'notification_notice': True,
        'notification_chat': True,
        'is_active': True,
    }
    defaults.update(flags)
    user = User.objects.create_user(
        email=email, password='pw1234abc', major=major, **defaults,
    )
    for cat in interests or []:
        InterestArea.objects.create(user=user, category=cat)
    return user


def make_info(*, categories, end_date=None, title='새 정보', source_id=None):
    return Information.objects.create(
        title=title,
        url=f'https://example.com/{title}',
        source='wevity',
        source_id=source_id or title,
        categories=categories,
        end_date=end_date,
    )


class FanoutNewInformationTests(TestCase):

    def test_매칭_사용자에게_알림_생성(self):
        user = make_user('match@mju.ac.kr', interests=['IT/개발'])
        info = make_info(categories=['IT/개발', '공모전'])

        created = fanout_new_information(info)

        self.assertEqual(created, 1)
        n = Notification.objects.get(user=user)
        self.assertEqual(n.notification_type, Notification.TYPE_INFORMATION)
        self.assertEqual(n.related_id, info.id)

    def test_매칭_점수_0이면_미생성(self):
        make_user('nomatch@mju.ac.kr', interests=['디자인'])
        info = make_info(categories=['IT/개발'])

        created = fanout_new_information(info)

        self.assertEqual(created, 0)

    def test_마감_지난_정보는_미발송(self):
        make_user('any@mju.ac.kr', interests=['IT/개발'])
        yesterday = date.today() - timedelta(days=1)
        info = make_info(categories=['IT/개발'], end_date=yesterday)

        created = fanout_new_information(info)

        self.assertEqual(created, 0)

    def test_마감일_오늘은_발송(self):
        make_user('today@mju.ac.kr', interests=['IT/개발'])
        info = make_info(categories=['IT/개발'], end_date=date.today())

        created = fanout_new_information(info)

        self.assertEqual(created, 1)

    def test_마감일_없으면_발송(self):
        make_user('open@mju.ac.kr', interests=['IT/개발'])
        info = make_info(categories=['IT/개발'], end_date=None)

        created = fanout_new_information(info)

        self.assertEqual(created, 1)

    def test_정보_알림_OFF_사용자는_제외(self):
        make_user(
            'off@mju.ac.kr', interests=['IT/개발'],
            notification_information=False,
        )
        info = make_info(categories=['IT/개발'])

        created = fanout_new_information(info)

        self.assertEqual(created, 0)

    def test_전체_알림_OFF_사용자는_제외(self):
        make_user(
            'off@mju.ac.kr', interests=['IT/개발'],
            notification_enabled=False,
        )
        info = make_info(categories=['IT/개발'])

        created = fanout_new_information(info)

        self.assertEqual(created, 0)

    def test_비활성_사용자는_제외(self):
        make_user('inactive@mju.ac.kr', interests=['IT/개발'], is_active=False)
        info = make_info(categories=['IT/개발'])

        created = fanout_new_information(info)

        self.assertEqual(created, 0)

    def test_categories_없으면_미발송(self):
        make_user('any@mju.ac.kr', interests=['IT/개발'])
        info = make_info(categories=[])

        created = fanout_new_information(info)

        self.assertEqual(created, 0)
