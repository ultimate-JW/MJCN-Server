"""FCM 푸시 송신 테스트 (spec 6.9 / 9.3).

firebase-admin은 mock — 실제 자격증명 없이 동작 (notices/tests_ai.py 패턴).
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from notifications import fcm, push
from notifications.fcm import MulticastResult, PushClientError, TokenSendResult
from notifications.models import FCMDevice, Notification

User = get_user_model()

FAKE_CRED = '/fake/firebase-credentials.json'


# ───────────────────────── 헬퍼 ─────────────────────────

def make_user(email='u@mju.ac.kr', **flags):
    return User.objects.create_user(email=email, password='pw1234abc', **flags)


def make_notification(user, *, title='제목', message='본문',
                      ntype='notice', is_pushed=False, related_id=None):
    return Notification.objects.create(
        user=user, title=title, message=message,
        notification_type=ntype, is_pushed=is_pushed, related_id=related_id,
    )


def make_device(user, token, *, is_active=True):
    return FCMDevice.objects.create(
        user=user, registration_token=token, is_active=is_active,
    )


def age_notification(notif, hours):
    """created_at(auto_now_add)을 과거로 강제 — recency guard 테스트용."""
    Notification.objects.filter(pk=notif.pk).update(
        created_at=timezone.now() - timedelta(hours=hours),
    )
    notif.refresh_from_db()


def ok(tokens):
    return MulticastResult(results=[
        TokenSendResult(token=t, success=True) for t in tokens
    ])


def dead(tokens):
    return MulticastResult(results=[
        TokenSendResult(token=t, success=False, is_dead=True, error='Unregistered')
        for t in tokens
    ])


def transient(tokens):
    return MulticastResult(results=[
        TokenSendResult(token=t, success=False, is_dead=False, error='UNAVAILABLE')
        for t in tokens
    ])


# ───────────────────── send_pending_pushes ─────────────────────

class SendPendingPushesTests(TestCase):

    def test_미설정이면_no_op(self):
        # FIREBASE_CREDENTIALS_PATH 미설정 → 송신 없이 알림 미변경
        user = make_user()
        make_device(user, 't1')
        notif = make_notification(user)
        with patch('notifications.fcm.send_to_tokens') as mock_send:
            result = push.send_pending_pushes()
        mock_send.assert_not_called()
        self.assertEqual(result.total, 1)
        self.assertEqual(result.pushed, 0)
        notif.refresh_from_db()
        self.assertFalse(notif.is_pushed)

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_정상_송신_단일_토큰(self):
        user = make_user()
        make_device(user, 't1')
        notif = make_notification(user, related_id=42)
        with patch('notifications.fcm.send_to_tokens',
                   side_effect=lambda toks, **kw: ok(toks)) as mock_send:
            result = push.send_pending_pushes()
        mock_send.assert_called_once()
        self.assertEqual(result.pushed, 1)
        notif.refresh_from_db()
        self.assertTrue(notif.is_pushed)

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_복수_토큰은_한_번에_멀티캐스트(self):
        user = make_user()
        make_device(user, 't1')
        make_device(user, 't2')
        make_notification(user)
        with patch('notifications.fcm.send_to_tokens',
                   side_effect=lambda toks, **kw: ok(toks)) as mock_send:
            result = push.send_pending_pushes()
        mock_send.assert_called_once()
        self.assertEqual(sorted(mock_send.call_args.args[0]), ['t1', 't2'])
        self.assertEqual(result.pushed, 1)

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_멀티캐스트_상한_초과시_청크_분할(self):
        user = make_user()
        for i in range(3):
            make_device(user, f't{i}')
        make_notification(user)
        with patch.object(fcm, 'FCM_MULTICAST_LIMIT', 2), \
             patch('notifications.fcm.send_to_tokens',
                   side_effect=lambda toks, **kw: ok(toks)) as mock_send:
            result = push.send_pending_pushes()
        self.assertEqual(mock_send.call_count, 2)  # [2] + [1]
        self.assertEqual(result.pushed, 1)

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_활성_디바이스_없으면_푸시없이_종결(self):
        user = make_user()
        notif = make_notification(user)
        with patch('notifications.fcm.send_to_tokens') as mock_send:
            result = push.send_pending_pushes()
        mock_send.assert_not_called()
        self.assertEqual(result.skipped_no_device, 1)
        notif.refresh_from_db()
        self.assertTrue(notif.is_pushed)  # 영구 종결 — 재시도 안 함

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_비활성_디바이스만_있으면_종결(self):
        user = make_user()
        make_device(user, 't1', is_active=False)
        notif = make_notification(user)
        with patch('notifications.fcm.send_to_tokens') as mock_send:
            result = push.send_pending_pushes()
        mock_send.assert_not_called()
        self.assertEqual(result.skipped_no_device, 1)
        notif.refresh_from_db()
        self.assertTrue(notif.is_pushed)

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_죽은_토큰은_디바이스_비활성화(self):
        user = make_user()
        device = make_device(user, 'dead-token')
        notif = make_notification(user)
        with patch('notifications.fcm.send_to_tokens',
                   side_effect=lambda toks, **kw: dead(toks)):
            result = push.send_pending_pushes()
        self.assertEqual(result.dead_tokens, 1)
        device.refresh_from_db()
        self.assertFalse(device.is_active)
        notif.refresh_from_db()
        self.assertTrue(notif.is_pushed)  # transient 없음 → 확정

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_transient_실패는_재시도_위해_미마킹(self):
        user = make_user()
        make_device(user, 't1')
        notif = make_notification(user)
        with patch('notifications.fcm.send_to_tokens',
                   side_effect=lambda toks, **kw: transient(toks)):
            result = push.send_pending_pushes()
        self.assertEqual(result.deferred, 1)
        self.assertEqual(result.pushed, 0)
        notif.refresh_from_db()
        self.assertFalse(notif.is_pushed)
        # 재실행 시 같은 알림이 다시 대상이 됨
        with patch('notifications.fcm.send_to_tokens',
                   side_effect=lambda toks, **kw: ok(toks)):
            result2 = push.send_pending_pushes()
        self.assertEqual(result2.total, 1)
        self.assertEqual(result2.pushed, 1)

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_PushClientError는_미마킹_후_다음_알림_계속(self):
        user_a = make_user(email='a@mju.ac.kr')
        user_b = make_user(email='b@mju.ac.kr')
        make_device(user_a, 'ta')
        make_device(user_b, 'tb')
        notif_a = make_notification(user_a)
        notif_b = make_notification(user_b)
        age_notification(notif_a, 2)  # a가 더 오래됨 → 먼저 처리
        with patch('notifications.fcm.send_to_tokens',
                   side_effect=[PushClientError('boom'), ok(['tb'])]):
            result = push.send_pending_pushes()
        self.assertEqual(result.deferred, 1)
        self.assertEqual(result.pushed, 1)
        notif_a.refresh_from_db()
        notif_b.refresh_from_db()
        self.assertFalse(notif_a.is_pushed)  # 실패 → 재시도 대상
        self.assertTrue(notif_b.is_pushed)   # 격리 — 영향 없이 송신됨

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_recency_guard_노후알림은_푸시없이_종결(self):
        user = make_user()
        make_device(user, 't1')
        stale = make_notification(user)
        age_notification(stale, 25)  # 24h 초과
        with patch('notifications.fcm.send_to_tokens') as mock_send:
            result = push.send_pending_pushes()
        mock_send.assert_not_called()
        self.assertEqual(result.skipped_stale, 1)
        self.assertEqual(result.total, 0)
        stale.refresh_from_db()
        self.assertTrue(stale.is_pushed)  # 푸시 없이 종결

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_recency_guard_경계_23h는_정상_송신(self):
        user = make_user()
        make_device(user, 't1')
        fresh = make_notification(user)
        age_notification(fresh, 23)  # 24h 이내
        with patch('notifications.fcm.send_to_tokens',
                   side_effect=lambda toks, **kw: ok(toks)):
            result = push.send_pending_pushes()
        self.assertEqual(result.pushed, 1)
        fresh.refresh_from_db()
        self.assertTrue(fresh.is_pushed)

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_이미_전송된_알림은_대상_아님(self):
        user = make_user()
        make_device(user, 't1')
        make_notification(user, is_pushed=True)
        with patch('notifications.fcm.send_to_tokens') as mock_send:
            result = push.send_pending_pushes()
        mock_send.assert_not_called()
        self.assertEqual(result.total, 0)


# ─────────────────── send_pending_pushes 명령 ───────────────────

class SendPendingPushesCommandTests(TestCase):

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_명령_실행(self):
        user = make_user()
        make_device(user, 't1')
        out = StringIO()
        with patch('notifications.fcm.send_to_tokens',
                   side_effect=lambda toks, **kw: ok(toks)):
            make_notification(user)
            call_command('send_pending_pushes', stdout=out)
        self.assertIn('pushed=1', out.getvalue())

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_dry_run은_송신도_마킹도_안함(self):
        user = make_user()
        make_device(user, 't1')
        notif = make_notification(user)
        out = StringIO()
        with patch('notifications.fcm.send_to_tokens') as mock_send:
            call_command('send_pending_pushes', '--dry-run', stdout=out)
        mock_send.assert_not_called()
        notif.refresh_from_db()
        self.assertFalse(notif.is_pushed)


# ─────────────────────── fcm.py 단위 ───────────────────────

class FcmClientTests(TestCase):

    def tearDown(self):
        fcm.reset_app()

    def test_is_configured(self):
        with override_settings(FIREBASE_CREDENTIALS_PATH=''):
            self.assertFalse(fcm.is_configured())
        with override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED):
            self.assertTrue(fcm.is_configured())

    @override_settings(FIREBASE_CREDENTIALS_PATH='')
    def test_get_app_미설정시_PushClientError(self):
        fcm.reset_app()
        with self.assertRaises(PushClientError):
            fcm.get_app()

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_get_app_초기화_후_캐싱(self):
        fcm.reset_app()
        fake_app = object()
        with patch('firebase_admin.credentials.Certificate'), \
             patch('firebase_admin.initialize_app',
                   return_value=fake_app) as mock_init:
            self.assertIs(fcm.get_app(), fake_app)
            self.assertIs(fcm.get_app(), fake_app)  # 2회차는 캐시
        mock_init.assert_called_once()

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_send_to_tokens_결과_매핑(self):
        from firebase_admin import messaging

        responses = [
            Mock(success=True, exception=None),
            Mock(success=False, exception=messaging.UnregisteredError('gone')),
            Mock(success=False, exception=Exception('UNAVAILABLE')),
        ]
        batch = Mock(responses=responses)
        with patch('notifications.fcm.get_app', return_value=object()), \
             patch('firebase_admin.messaging.send_each_for_multicast',
                   return_value=batch):
            result = fcm.send_to_tokens(
                ['ok', 'dead', 'flaky'], title='t', body='b',
            )
        by_token = {r.token: r for r in result.results}
        self.assertTrue(by_token['ok'].success)
        self.assertTrue(by_token['dead'].is_dead)
        self.assertFalse(by_token['flaky'].is_dead)       # transient
        self.assertTrue(result.has_transient_failure)
        self.assertEqual(result.dead_tokens, ['dead'])

    @override_settings(FIREBASE_CREDENTIALS_PATH=FAKE_CRED)
    def test_send_to_tokens_호출_전체_실패는_PushClientError(self):
        with patch('notifications.fcm.get_app', return_value=object()), \
             patch('firebase_admin.messaging.send_each_for_multicast',
                   side_effect=RuntimeError('network down')):
            with self.assertRaises(PushClientError):
                fcm.send_to_tokens(['t1'], title='t', body='b')
