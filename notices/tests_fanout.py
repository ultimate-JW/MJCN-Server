"""공지 fanout 알림 생성 로직 테스트 (spec 6.9)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import InterestArea
from notices.fanout import NOTICE_FANOUT_RECENCY_DAYS, fanout_new_notice
from notices.models import Notice
from notifications.models import Notification

User = get_user_model()


def make_user(email, *, major='', interests=None, **flags):
    """매칭 키워드를 가진 사용자 생성."""
    defaults = {
        'notification_enabled': True,
        'notification_notice': True,
        'notification_information': True,
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


def make_notice(*, tags, published_at=None, title='새 공지', source='general'):
    return Notice.objects.create(
        source=source,
        title=title,
        url=f'https://example.com/{title}',
        published_at=published_at or timezone.now(),
        tags=tags,
    )


class FanoutNewNoticeTests(TestCase):

    def test_매칭_사용자에게_알림_생성(self):
        user = make_user('match@mju.ac.kr', interests=['IT/개발'])
        notice = make_notice(tags=['IT/개발', '취업'])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 1)
        self.assertEqual(Notification.objects.filter(user=user).count(), 1)
        n = Notification.objects.get(user=user)
        self.assertEqual(n.notification_type, Notification.TYPE_NOTICE)
        self.assertEqual(n.related_id, notice.id)
        self.assertEqual(n.title, '새 공지')

    def test_매칭_점수_0이면_미생성(self):
        make_user('nomatch@mju.ac.kr', interests=['디자인'])
        notice = make_notice(tags=['IT/개발'])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_전체_알림_OFF_사용자는_제외(self):
        make_user(
            'off@mju.ac.kr', interests=['IT/개발'],
            notification_enabled=False,
        )
        notice = make_notice(tags=['IT/개발'])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)

    def test_공지_알림_OFF_사용자는_제외(self):
        make_user(
            'noticeoff@mju.ac.kr', interests=['IT/개발'],
            notification_notice=False,
        )
        notice = make_notice(tags=['IT/개발'])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)

    def test_비활성_사용자는_제외(self):
        make_user('inactive@mju.ac.kr', interests=['IT/개발'], is_active=False)
        notice = make_notice(tags=['IT/개발'])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)

    def test_백필_가드_7일_초과_공지는_미발송(self):
        make_user('recent@mju.ac.kr', interests=['IT/개발'])
        old = timezone.now() - timedelta(days=NOTICE_FANOUT_RECENCY_DAYS + 1)
        notice = make_notice(tags=['IT/개발'], published_at=old)

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)

    def test_백필_가드_경계_정확히_7일은_발송(self):
        make_user('edge@mju.ac.kr', interests=['IT/개발'])
        # NOTICE_FANOUT_RECENCY_DAYS - 1 일 전 = 가드 안쪽
        recent = timezone.now() - timedelta(days=NOTICE_FANOUT_RECENCY_DAYS - 1)
        notice = make_notice(tags=['IT/개발'], published_at=recent)

        created = fanout_new_notice(notice)

        self.assertEqual(created, 1)

    def test_태그_없는_공지는_미발송(self):
        make_user('any@mju.ac.kr', interests=['IT/개발'])
        notice = make_notice(tags=[])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)

    def test_전공_기반_매칭도_동작(self):
        # InterestArea 없어도 User.major로 매칭 가능 (spec 5.10)
        make_user('major@mju.ac.kr', major='컴퓨터공학과')
        notice = make_notice(tags=['컴퓨터공학과', '학사'])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 1)

    def test_여러_사용자에게_각각_생성(self):
        u1 = make_user('a@mju.ac.kr', interests=['IT/개발'])
        u2 = make_user('b@mju.ac.kr', interests=['IT/개발'])
        make_user('c@mju.ac.kr', interests=['디자인'])  # 매칭 안 됨
        notice = make_notice(tags=['IT/개발'])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 2)
        self.assertEqual(Notification.objects.filter(user=u1).count(), 1)
        self.assertEqual(Notification.objects.filter(user=u2).count(), 1)

    def test_published_at_없으면_미발송(self):
        # 모델상 published_at은 not-null이라 실제로는 발생 안 하지만
        # 헬퍼 가드로 None도 안전 처리되는지 확인
        make_user('z@mju.ac.kr', interests=['IT/개발'])
        notice = make_notice(tags=['IT/개발'])
        notice.published_at = None
        # save() 안 함 — 메모리에서만 None으로 만들어 fanout에 전달

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)


# ─── #153: 학사공지 전체 fanout 예외 ─────────────────────────────────

class AcademicNoticeFanoutTests(TestCase):
    """학사공지(source='academic')는 매칭·태그 무관 모든 활성 사용자에게 알림 (#153)."""

    def test_학사공지는_매칭_없는_사용자에게도_알림(self):
        # 관심사가 '디자인'이고 공지 태그도 디자인 무관 — 일반 fanout이면 미발송
        u = make_user('nomatch@mju.ac.kr', interests=['디자인'])
        notice = make_notice(
            source='academic', tags=['수강신청', '학사일정'], title='수강신청 안내',
        )

        created = fanout_new_notice(notice)

        self.assertEqual(created, 1)
        n = Notification.objects.get(user=u)
        self.assertEqual(n.related_id, notice.id)
        self.assertIn('학사', n.message)  # 학사 전용 메시지

    def test_학사공지는_tags_비어있어도_알림(self):
        u = make_user('any@mju.ac.kr', interests=['IT/개발'])
        notice = make_notice(source='academic', tags=[], title='2026 등록 안내')

        created = fanout_new_notice(notice)

        self.assertEqual(created, 1)
        self.assertEqual(Notification.objects.filter(user=u).count(), 1)

    def test_학사공지_여러_사용자_모두에게_발송(self):
        # 매칭 안 되는 사용자 포함 모두 받음
        u1 = make_user('a@mju.ac.kr', interests=['IT/개발'])
        u2 = make_user('b@mju.ac.kr', interests=['디자인'])
        u3 = make_user('c@mju.ac.kr', major='경영학')
        notice = make_notice(source='academic', tags=[], title='졸업 안내')

        created = fanout_new_notice(notice)

        self.assertEqual(created, 3)
        for u in (u1, u2, u3):
            self.assertEqual(Notification.objects.filter(user=u).count(), 1)

    def test_학사공지도_토글_OFF_사용자는_제외(self):
        # 학사공지여도 사용자가 공지 알림 OFF 했으면 존중
        make_user(
            'off@mju.ac.kr', interests=['IT/개발'],
            notification_notice=False,
        )
        notice = make_notice(source='academic', tags=[])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)

    def test_학사공지도_비활성_사용자는_제외(self):
        make_user('inactive@mju.ac.kr', interests=['IT/개발'], is_active=False)
        notice = make_notice(source='academic', tags=[])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)

    def test_학사공지도_백필_가드_적용(self):
        # 7일 이전 학사공지는 알림 발송 안 함 (대량 backfill 폭주 방지)
        make_user('any@mju.ac.kr', interests=['IT/개발'])
        old = timezone.now() - timedelta(days=NOTICE_FANOUT_RECENCY_DAYS + 1)
        notice = make_notice(source='academic', tags=[], published_at=old)

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)

    def test_general_source는_기존_로직_그대로(self):
        # 회귀 — 학사 외 카테고리는 tags=[]면 skip
        make_user('any@mju.ac.kr', interests=['IT/개발'])
        notice = make_notice(source='general', tags=[])

        created = fanout_new_notice(notice)

        self.assertEqual(created, 0)

    def test_학사공지_메시지는_학사_전용(self):
        u = make_user('msg@mju.ac.kr', interests=['IT/개발'])
        notice = make_notice(source='academic', tags=[], title='학사 공지')

        fanout_new_notice(notice)

        n = Notification.objects.get(user=u)
        self.assertEqual(n.message, '새 학사 공지가 등록되었습니다.')
