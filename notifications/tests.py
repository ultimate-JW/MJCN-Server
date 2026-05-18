"""알림 API + 서비스 헬퍼 테스트 (spec 6.9)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from notifications.models import FCMDevice, Notification
from notifications.services import create_notification

User = get_user_model()


def make_user(email='u@mju.ac.kr', **flags):
    defaults = {
        'notification_enabled': True,
        'notification_chat': True,
        'notification_notice': True,
        'notification_information': True,
    }
    defaults.update(flags)
    user = User.objects.create_user(email=email, password='pw1234abc', **defaults)
    return user


# ─────────────────────────────────────────────────────────────────────
# services.create_notification — 토글 체크 로직
# ─────────────────────────────────────────────────────────────────────

class CreateNotificationServiceTests(TestCase):

    def test_정상_생성(self):
        user = make_user()
        n = create_notification(
            user, title='제목', message='본문', notification_type='notice', related_id=42,
        )
        self.assertIsNotNone(n)
        self.assertEqual(n.title, '제목')
        self.assertEqual(n.related_id, 42)
        self.assertFalse(n.is_read)

    def test_notification_enabled_False는_INSERT_안함(self):
        user = make_user(notification_enabled=False)
        n = create_notification(user, title='t', message='m', notification_type='notice')
        self.assertIsNone(n)
        self.assertEqual(Notification.objects.count(), 0)

    def test_notification_notice_False는_notice만_차단(self):
        user = make_user(notification_notice=False)
        # notice 차단
        self.assertIsNone(create_notification(
            user, title='t', message='m', notification_type='notice',
        ))
        # information은 통과
        self.assertIsNotNone(create_notification(
            user, title='t', message='m', notification_type='information',
        ))

    def test_system_type은_카테고리_토글_무시(self):
        user = make_user(notification_notice=False)  # notice OFF
        n = create_notification(user, title='t', message='m', notification_type='system')
        self.assertIsNotNone(n)

    def test_전체_OFF면_system도_차단(self):
        user = make_user(notification_enabled=False)
        n = create_notification(user, title='t', message='m', notification_type='system')
        self.assertIsNone(n)


# ─────────────────────────────────────────────────────────────────────
# GET /api/v1/notifications/ — 목록
# ─────────────────────────────────────────────────────────────────────

class NotificationListAPITests(TestCase):
    URL = '/api/v1/notifications/'

    def setUp(self):
        self.user_a = make_user(email='a@mju.ac.kr')
        self.user_b = make_user(email='b@mju.ac.kr')
        self.client = APIClient()
        self.client.force_authenticate(self.user_a)

        Notification.objects.create(user=self.user_a, title='A1', message='m', notification_type='notice')
        Notification.objects.create(user=self.user_a, title='A2', message='m', notification_type='information')
        Notification.objects.create(user=self.user_b, title='B1', message='m', notification_type='notice')

    def test_본인_알림만_노출(self):
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 2)
        titles = [r['title'] for r in res.data['results']]
        self.assertIn('A1', titles)
        self.assertIn('A2', titles)
        self.assertNotIn('B1', titles)

    def test_비인증은_401(self):
        anon = APIClient()
        res = anon.get(self.URL)
        self.assertEqual(res.status_code, 401)


# ─────────────────────────────────────────────────────────────────────
# GET /unread-count/
# ─────────────────────────────────────────────────────────────────────

class UnreadCountAPITests(TestCase):
    URL = '/api/v1/notifications/unread-count/'

    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_읽지_않은_알림_수(self):
        Notification.objects.create(user=self.user, title='1', message='m', notification_type='notice', is_read=False)
        Notification.objects.create(user=self.user, title='2', message='m', notification_type='notice', is_read=False)
        Notification.objects.create(user=self.user, title='3', message='m', notification_type='notice', is_read=True)
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['unread_count'], 2)

    def test_타사용자_알림은_제외(self):
        other = make_user(email='other@mju.ac.kr')
        Notification.objects.create(user=other, title='X', message='m', notification_type='notice', is_read=False)
        res = self.client.get(self.URL)
        self.assertEqual(res.data['unread_count'], 0)


# ─────────────────────────────────────────────────────────────────────
# PATCH /<id>/ — 읽음 처리
# ─────────────────────────────────────────────────────────────────────

class NotificationDetailAPITests(TestCase):

    def setUp(self):
        self.user_a = make_user(email='a@mju.ac.kr')
        self.user_b = make_user(email='b@mju.ac.kr')
        self.client = APIClient()
        self.client.force_authenticate(self.user_a)

        self.n_a = Notification.objects.create(user=self.user_a, title='A', message='m', notification_type='notice')
        self.n_b = Notification.objects.create(user=self.user_b, title='B', message='m', notification_type='notice')

    def test_정상_읽음_처리(self):
        res = self.client.patch(f'/api/v1/notifications/{self.n_a.id}/', {'is_read': True}, format='json')
        self.assertEqual(res.status_code, 200)
        self.n_a.refresh_from_db()
        self.assertTrue(self.n_a.is_read)

    def test_타사용자_알림_PATCH는_404(self):
        # enumeration 방어 — 403이 아니라 404
        res = self.client.patch(f'/api/v1/notifications/{self.n_b.id}/', {'is_read': True}, format='json')
        self.assertEqual(res.status_code, 404)
        self.n_b.refresh_from_db()
        self.assertFalse(self.n_b.is_read)

    def test_없는_id는_404(self):
        res = self.client.patch('/api/v1/notifications/999999/', {'is_read': True}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_title_은_읽기전용_무시(self):
        res = self.client.patch(
            f'/api/v1/notifications/{self.n_a.id}/',
            {'title': '바꿈', 'is_read': True},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.n_a.refresh_from_db()
        self.assertEqual(self.n_a.title, 'A')  # 안 바뀜
        self.assertTrue(self.n_a.is_read)


# ─────────────────────────────────────────────────────────────────────
# POST /read-all/
# ─────────────────────────────────────────────────────────────────────

class ReadAllAPITests(TestCase):
    URL = '/api/v1/notifications/read-all/'

    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        Notification.objects.create(user=self.user, title='1', message='m', notification_type='notice', is_read=False)
        Notification.objects.create(user=self.user, title='2', message='m', notification_type='notice', is_read=False)
        Notification.objects.create(user=self.user, title='3', message='m', notification_type='notice', is_read=True)

    def test_전체_읽음_처리(self):
        res = self.client.post(self.URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['updated'], 2)  # 미읽음 2건만
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=False).count(), 0)

    def test_타사용자_알림은_영향_없음(self):
        other = make_user(email='other@mju.ac.kr')
        Notification.objects.create(user=other, title='X', message='m', notification_type='notice', is_read=False)
        self.client.post(self.URL)
        self.assertEqual(Notification.objects.filter(user=other, is_read=False).count(), 1)


# ─────────────────────────────────────────────────────────────────────
# POST /devices/ — FCM 등록
# ─────────────────────────────────────────────────────────────────────

class FCMDeviceRegisterTests(TestCase):
    URL = '/api/v1/notifications/devices/'

    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_정상_등록_201(self):
        res = self.client.post(self.URL, {'registration_token': 'fcm_token_xxx'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(FCMDevice.objects.count(), 1)
        device = FCMDevice.objects.get()
        self.assertEqual(device.user, self.user)
        self.assertTrue(device.is_active)

    def test_같은_토큰_재POST는_200_갱신(self):
        self.client.post(self.URL, {'registration_token': 'token_a'}, format='json')
        res = self.client.post(self.URL, {'registration_token': 'token_a'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(FCMDevice.objects.count(), 1)

    def test_다른_사용자가_같은_토큰_POST하면_양도(self):
        # 사용자 A 등록
        self.client.post(self.URL, {'registration_token': 'shared_token'}, format='json')
        device = FCMDevice.objects.get()
        self.assertEqual(device.user, self.user)

        # 사용자 B로 전환 후 같은 토큰 POST (디바이스 양도 시나리오)
        user_b = make_user(email='b@mju.ac.kr')
        self.client.force_authenticate(user_b)
        res = self.client.post(self.URL, {'registration_token': 'shared_token'}, format='json')
        self.assertEqual(res.status_code, 200)
        device.refresh_from_db()
        self.assertEqual(device.user, user_b)  # 매핑 변경됨
        self.assertEqual(FCMDevice.objects.count(), 1)

    def test_token_누락은_400(self):
        res = self.client.post(self.URL, {}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_비인증은_401(self):
        anon = APIClient()
        res = anon.post(self.URL, {'registration_token': 'x'}, format='json')
        self.assertEqual(res.status_code, 401)


# ─────────────────────────────────────────────────────────────────────
# DELETE /devices/
# ─────────────────────────────────────────────────────────────────────

class FCMDeviceDeleteTests(TestCase):
    URL = '/api/v1/notifications/devices/'

    def setUp(self):
        self.user_a = make_user(email='a@mju.ac.kr')
        self.user_b = make_user(email='b@mju.ac.kr')
        self.client = APIClient()
        self.client.force_authenticate(self.user_a)

        self.device_a = FCMDevice.objects.create(user=self.user_a, registration_token='token_a')
        self.device_b = FCMDevice.objects.create(user=self.user_b, registration_token='token_b')

    def test_정상_삭제_204(self):
        res = self.client.delete(self.URL, {'registration_token': 'token_a'}, format='json')
        self.assertEqual(res.status_code, 204)
        self.assertFalse(FCMDevice.objects.filter(id=self.device_a.id).exists())

    def test_타사용자_토큰_삭제_시도는_404(self):
        # token_b는 user_b 것 — user_a로 삭제 시도 → 404 (enumeration 방어)
        res = self.client.delete(self.URL, {'registration_token': 'token_b'}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertTrue(FCMDevice.objects.filter(id=self.device_b.id).exists())

    def test_token_누락은_400(self):
        res = self.client.delete(self.URL, {}, format='json')
        self.assertEqual(res.status_code, 400)
