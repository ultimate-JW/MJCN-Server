"""회원가입·이메일 인증·비밀번호 재설정 테스트 (spec 5.1.1, 5.1.4).

회원가입 정책 변경(#81/#82) 후의 흐름을 검증:
- signup → User 생성 안 함, PendingSignup만 upsert
- verify-email 성공 → User 생성, PendingSignup 삭제, JWT 발급
- resend → PendingSignup의 code/expires 갱신
- 회귀: password_reset 흐름은 EmailVerification 경로 그대로 동작
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import EmailVerification, PendingSignup

User = get_user_model()


SIGNUP_URL = '/api/v1/accounts/signup/'
VERIFY_URL = '/api/v1/accounts/verify-email/'
RESEND_URL = '/api/v1/accounts/verify-email/resend/'
PWD_REQUEST_URL = '/api/v1/accounts/password/reset/'
PWD_VERIFY_URL = '/api/v1/accounts/password/reset/verify/'
PWD_CONFIRM_URL = '/api/v1/accounts/password/reset/confirm/'
LOGIN_URL = '/api/v1/accounts/login/'

VALID_PWD = 'aBcd1234!'


class SignupFlowTests(TestCase):
    """signup이 PendingSignup만 만들고 User는 만들지 않음을 검증."""

    def setUp(self):
        self.client = APIClient()

    def test_signup_creates_pending_not_user(self):
        with patch('accounts.views.send_signup_code_email'):
            res = self.client.post(SIGNUP_URL, {
                'email': 'a@mju.ac.kr',
                'password': VALID_PWD,
                'password_confirm': VALID_PWD,
            }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(PendingSignup.objects.count(), 1)
        pending = PendingSignup.objects.get(email='a@mju.ac.kr')
        # password_hash는 raw 평문이 아니어야 함
        self.assertNotEqual(pending.password_hash, VALID_PWD)
        self.assertTrue(check_password(VALID_PWD, pending.password_hash))
        self.assertEqual(len(pending.code), 6)
        self.assertGreater(pending.code_expires_at, timezone.now())

    def test_signup_normalizes_email(self):
        with patch('accounts.views.send_signup_code_email'):
            self.client.post(SIGNUP_URL, {
                'email': 'Abc@mju.ac.kr',
                'password': VALID_PWD,
                'password_confirm': VALID_PWD,
            }, format='json')
        self.assertTrue(PendingSignup.objects.filter(email='abc@mju.ac.kr').exists())

    def test_signup_duplicate_email_upserts_pending(self):
        # 1차 signup
        with patch('accounts.views.send_signup_code_email'):
            self.client.post(SIGNUP_URL, {
                'email': 'a@mju.ac.kr',
                'password': VALID_PWD,
                'password_confirm': VALID_PWD,
            }, format='json')
        first = PendingSignup.objects.get(email='a@mju.ac.kr')
        first_code = first.code
        first_hash = first.password_hash

        # 2차 signup (같은 이메일, 다른 비번)
        new_pwd = 'XyzW9876@'
        with patch('accounts.views.send_signup_code_email'):
            res = self.client.post(SIGNUP_URL, {
                'email': 'a@mju.ac.kr',
                'password': new_pwd,
                'password_confirm': new_pwd,
            }, format='json')

        self.assertEqual(res.status_code, 201)
        # row 1개 유지 (upsert), code·password_hash 갱신, 이전 코드 무효화
        self.assertEqual(PendingSignup.objects.count(), 1)
        second = PendingSignup.objects.get(email='a@mju.ac.kr')
        self.assertNotEqual(second.code, first_code)
        self.assertNotEqual(second.password_hash, first_hash)
        self.assertTrue(check_password(new_pwd, second.password_hash))

    def test_signup_blocked_if_already_verified_user_exists(self):
        # 이미 인증 완료된 User가 있으면 400
        User.objects.create(email='a@mju.ac.kr', password='x', is_email_verified=True)
        res = self.client.post(SIGNUP_URL, {
            'email': 'a@mju.ac.kr',
            'password': VALID_PWD,
            'password_confirm': VALID_PWD,
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('email', res.data)


class VerifyEmailFlowTests(TestCase):
    """verify-email이 PendingSignup → User 변환을 트랜잭션으로 수행함을 검증."""

    def setUp(self):
        self.client = APIClient()
        with patch('accounts.views.send_signup_code_email'):
            self.client.post(SIGNUP_URL, {
                'email': 'a@mju.ac.kr',
                'password': VALID_PWD,
                'password_confirm': VALID_PWD,
            }, format='json')
        self.pending = PendingSignup.objects.get(email='a@mju.ac.kr')

    def test_verify_success_creates_user_and_deletes_pending(self):
        res = self.client.post(VERIFY_URL, {
            'email': 'a@mju.ac.kr',
            'code': self.pending.code,
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)

        # PendingSignup 삭제, User 생성
        self.assertEqual(PendingSignup.objects.count(), 0)
        user = User.objects.get(email='a@mju.ac.kr')
        self.assertTrue(user.is_email_verified)
        # 비밀번호 체크 통과 (signup 시점의 비번으로 로그인 가능)
        self.assertTrue(user.check_password(VALID_PWD))

    def test_verify_wrong_code_returns_400(self):
        res = self.client.post(VERIFY_URL, {
            'email': 'a@mju.ac.kr',
            'code': '000000',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(User.objects.count(), 0)
        # attempts 증가
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.attempts, 1)

    def test_verify_expired_code_returns_400(self):
        self.pending.code_expires_at = timezone.now() - timedelta(seconds=1)
        self.pending.save(update_fields=['code_expires_at'])
        res = self.client.post(VERIFY_URL, {
            'email': 'a@mju.ac.kr',
            'code': self.pending.code,
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('만료', res.data['detail'])
        self.assertEqual(User.objects.count(), 0)

    def test_verify_with_old_code_after_resignup_fails(self):
        # 사용자가 다시 signup하여 code 갱신됨 → 옛 코드는 무효
        old_code = self.pending.code
        new_pwd = 'XyzW9876@'
        with patch('accounts.views.send_signup_code_email'):
            self.client.post(SIGNUP_URL, {
                'email': 'a@mju.ac.kr',
                'password': new_pwd,
                'password_confirm': new_pwd,
            }, format='json')
        res = self.client.post(VERIFY_URL, {
            'email': 'a@mju.ac.kr',
            'code': old_code,
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(User.objects.count(), 0)

    def test_verify_already_verified_user_returns_400(self):
        # 이미 인증 완료된 User가 있는 상태 (enumeration 방지)
        User.objects.create(email='a@mju.ac.kr', password='x', is_email_verified=True)
        res = self.client.post(VERIFY_URL, {
            'email': 'a@mju.ac.kr',
            'code': self.pending.code,
        }, format='json')
        self.assertEqual(res.status_code, 400)


class ResendVerificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        with patch('accounts.views.send_signup_code_email'):
            self.client.post(SIGNUP_URL, {
                'email': 'a@mju.ac.kr',
                'password': VALID_PWD,
                'password_confirm': VALID_PWD,
            }, format='json')

    def test_resend_updates_pending_code(self):
        pending_before = PendingSignup.objects.get(email='a@mju.ac.kr')
        old_code = pending_before.code

        with patch('accounts.views.send_signup_code_email') as sender:
            res = self.client.post(RESEND_URL, {'email': 'a@mju.ac.kr'}, format='json')

        self.assertEqual(res.status_code, 200)
        sender.assert_called_once()
        pending_after = PendingSignup.objects.get(email='a@mju.ac.kr')
        self.assertNotEqual(pending_after.code, old_code)
        self.assertEqual(pending_after.attempts, 0)

    def test_resend_for_nonexistent_email_silent_200(self):
        res = self.client.post(RESEND_URL, {'email': 'nobody@mju.ac.kr'}, format='json')
        # enumeration 방지 — 200 응답 유지
        self.assertEqual(res.status_code, 200)


class PasswordResetRegressionTests(TestCase):
    """비밀번호 재설정 흐름은 EmailVerification 경로 그대로 동작해야 함."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='a@mju.ac.kr',
            password='Old1234@',
        )
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])

    def test_password_reset_end_to_end(self):
        # request → verify → confirm
        with patch('accounts.services.send_mail'):
            res = self.client.post(PWD_REQUEST_URL, {'email': 'a@mju.ac.kr'}, format='json')
        self.assertEqual(res.status_code, 200)

        verification = EmailVerification.objects.filter(user=self.user, purpose='password_reset').first()
        self.assertIsNotNone(verification)

        res = self.client.post(PWD_VERIFY_URL, {
            'email': 'a@mju.ac.kr',
            'code': verification.code,
        }, format='json')
        self.assertEqual(res.status_code, 200)
        # consume=False라 verify 후에도 코드는 사용 가능 상태
        verification.refresh_from_db()
        self.assertFalse(verification.is_used)

        new_pwd = 'New5678#'
        res = self.client.post(PWD_CONFIRM_URL, {
            'email': 'a@mju.ac.kr',
            'code': verification.code,
            'new_password': new_pwd,
        }, format='json')
        self.assertEqual(res.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_pwd))


class NonASCIIEmailRejectionTests(TestCase):
    """이슈 #79 — 한글/유니코드 섞인 이메일은 spec 5.1.1·5.1.5에 따라 모두 거부.

    7개 이메일 입력 endpoint가 동일하게 400을 반환해야 한다
    (이전에는 signup만 통과, password/reset은 reject되어 endpoint별 불일치였음 — 결함 D).
    """

    NON_ASCII_EMAIL = 'abc한글@example.com'

    def setUp(self):
        self.client = APIClient()

    def _assert_email_rejected(self, response):
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.data)

    def test_signup_rejects_non_ascii_email(self):
        res = self.client.post(SIGNUP_URL, {
            'email': self.NON_ASCII_EMAIL,
            'password': VALID_PWD,
            'password_confirm': VALID_PWD,
        }, format='json')
        self._assert_email_rejected(res)

    def test_verify_email_rejects_non_ascii_email(self):
        res = self.client.post(VERIFY_URL, {
            'email': self.NON_ASCII_EMAIL,
            'code': '12345678',
        }, format='json')
        self._assert_email_rejected(res)

    def test_resend_verification_rejects_non_ascii_email(self):
        res = self.client.post(RESEND_URL, {
            'email': self.NON_ASCII_EMAIL,
        }, format='json')
        self._assert_email_rejected(res)

    def test_login_rejects_non_ascii_email(self):
        res = self.client.post(LOGIN_URL, {
            'email': self.NON_ASCII_EMAIL,
            'password': VALID_PWD,
        }, format='json')
        self._assert_email_rejected(res)

    def test_password_reset_request_rejects_non_ascii_email(self):
        res = self.client.post(PWD_REQUEST_URL, {
            'email': self.NON_ASCII_EMAIL,
        }, format='json')
        self._assert_email_rejected(res)

    def test_password_reset_verify_rejects_non_ascii_email(self):
        res = self.client.post(PWD_VERIFY_URL, {
            'email': self.NON_ASCII_EMAIL,
            'code': '12345678',
        }, format='json')
        self._assert_email_rejected(res)

    def test_password_reset_confirm_rejects_non_ascii_email(self):
        res = self.client.post(PWD_CONFIRM_URL, {
            'email': self.NON_ASCII_EMAIL,
            'code': '12345678',
            'new_password': VALID_PWD,
        }, format='json')
        self._assert_email_rejected(res)

    def test_signup_and_reset_consistent_on_same_non_ascii_input(self):
        # 이슈 #79 결함 D — 같은 입력에 두 endpoint가 같은 결정 내려야 함
        signup_res = self.client.post(SIGNUP_URL, {
            'email': self.NON_ASCII_EMAIL,
            'password': VALID_PWD,
            'password_confirm': VALID_PWD,
        }, format='json')
        reset_res = self.client.post(PWD_REQUEST_URL, {
            'email': self.NON_ASCII_EMAIL,
        }, format='json')
        self.assertEqual(signup_res.status_code, 400)
        self.assertEqual(reset_res.status_code, 400)


class CourseHistoryAutoFillResponseTests(TestCase):
    """#114 — CourseHistory.save() override가 course_code로 Course에서 자동 복사한
    `liberal_subtype` / `core_area`가 API 응답에 노출되는지 검증.

    DB 자동 채움 동작 자체는 정상이었으나 시리얼라이저 fields에서 두 필드가 빠져
    응답에 안 보이던 결함 L."""

    url = '/api/v1/accounts/course-history/'

    def setUp(self):
        from courses.models import Course
        self.user = User.objects.create_user(
            email='hist@mju.ac.kr',
            password=VALID_PWD,
        )
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # 핵심교양 — liberal_subtype + core_area 둘 다 채워짐
        Course.objects.create(
            course_code='COR1001', name='서양사', category='핵심교양',
            college='교양', liberal_subtype='핵심교양', core_area='역사와 철학',
            credits=3, year_open=1, semester_open=1,
        )
        # 학문기초교양 — liberal_subtype만, core_area None
        Course.objects.create(
            course_code='FOU1001', name='미적분학1', category='학문기초교양',
            college='교양', liberal_subtype='학문기초교양',
            credits=3, year_open=1, semester_open=1,
        )
        # 전공필수 — 두 필드 다 None
        Course.objects.create(
            course_code='MAJ1001', name='자료구조', category='전공필수',
            college='ICT융합대학', major='컴퓨터공학전공',
            credits=3, year_open=2, semester_open=1,
        )

    def _post(self, course_code, course_name, category, credits=3, year=2024, semester=1):
        return self.client.post(self.url, {
            'course_name': course_name,
            'course_code': course_code,
            'year': year,
            'semester': semester,
            'grade_received': 'A',
            'category': category,
            'credits': credits,
        }, format='json')

    def test_POST_핵심교양_응답에_liberal_subtype과_core_area_노출_114(self):
        res = self._post('COR1001', '서양사', '핵심교양')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['liberal_subtype'], '핵심교양')
        self.assertEqual(res.data['core_area'], '역사와 철학')

    def test_POST_학문기초_응답에_liberal_subtype_노출_core_area_null_114(self):
        res = self._post('FOU1001', '미적분학1', '학문기초교양')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['liberal_subtype'], '학문기초교양')
        self.assertIsNone(res.data['core_area'])

    def test_POST_전공필수_응답에_두_키_모두_null_114(self):
        res = self._post('MAJ1001', '자료구조', '전공필수')
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(res.data['liberal_subtype'])
        self.assertIsNone(res.data['core_area'])

    def test_GET_list_응답_모든_item에_두_키_존재_114(self):
        self._post('COR1001', '서양사', '핵심교양', year=2024, semester=1)
        self._post('MAJ1001', '자료구조', '전공필수', year=2024, semester=2)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        # StandardPagination 적용 — res.data = {count, next, previous, results}
        results = res.data['results']
        self.assertEqual(len(results), 2)
        for item in results:
            self.assertIn('liberal_subtype', item)
            self.assertIn('core_area', item)


class CourseHistoryCategoryChoicesTests(TestCase):
    """#115 — CourseHistory.category가 학칙 7분류 외 임의 문자열 거부."""

    url = '/api/v1/accounts/course-history/'
    VALID_CATEGORIES = (
        '전공필수', '전공선택', '공통교양', '핵심교양',
        '학문기초교양', '일반교양', '자유선택',
    )

    def setUp(self):
        self.user = User.objects.create_user(
            email='cat@mju.ac.kr', password=VALID_PWD,
        )
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _payload(self, category, course_code='TST001'):
        return {
            'course_name': '테스트',
            'course_code': course_code,
            'year': 2024,
            'semester': 1,
            'grade_received': 'A',
            'category': category,
            'credits': 3,
        }

    def test_임의_문자열_category_거부_115(self):
        # 결함 M 재현 케이스 — 이전엔 201 통과 + DB에 garbage 박힘
        res = self.client.post(self.url, self._payload('테스트교양'), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('category', res.data)

    def test_빈_문자열_category_거부_115(self):
        res = self.client.post(self.url, self._payload(''), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('category', res.data)

    def test_학칙_7분류_모두_통과_115(self):
        for i, cat in enumerate(self.VALID_CATEGORIES):
            res = self.client.post(self.url, self._payload(cat, course_code=f'TST{i:03d}'), format='json')
            self.assertEqual(res.status_code, 201, msg=f'{cat} 거부됨')
            self.assertEqual(res.data['category'], cat)

    def test_옛_라벨_교양필수는_거부_115(self):
        # #47 Phase 3에서 폐기된 라벨 — silent 통과 방지
        res = self.client.post(self.url, self._payload('교양필수'), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('category', res.data)
