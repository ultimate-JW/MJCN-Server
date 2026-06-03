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


# #115 CourseHistoryCategoryChoicesTests는 #151에서 흡수됨 — 새 시리얼라이저가
# category를 read_only로 두어 평문 임의 문자열 입력 자체가 무시되고, Course
# 카탈로그의 category가 응답으로 노출됨. 회귀 검증은 CourseHistoryHydrateTests의
# test_평문_category_보내도_무시되고_카탈로그가_정답 / test_학칙_7분류_각_과목별_자동_매칭에서 수행.


class CourseHistoryHydrateTests(TestCase):
    """#151 — POST /course-history/는 course_code 1개로 5개 필드 자동 hydrate.

    course_name·category·credits·liberal_subtype·core_area가 Course에서 복사되고,
    사용자 입력은 course_code + year + semester + grade_received(선택) 4개로 축소.
    Course 미존재 시 400 (#149와 동일 정책). PUT/PATCH는 grade_received만 partial.
    """

    url = '/api/v1/accounts/course-history/'
    VALID_CATEGORIES = (
        '전공필수', '전공선택', '공통교양', '핵심교양',
        '학문기초교양', '일반교양', '자유선택',
    )

    def setUp(self):
        from courses.models import Course
        self.user = User.objects.create_user(
            email='hist151@mju.ac.kr', password=VALID_PWD,
        )
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # 핵심교양 — liberal_subtype + core_area 둘 다
        self.c_core = Course.objects.create(
            course_code='COR1001', name='서양사', category='핵심교양',
            college='교양', liberal_subtype='핵심교양', core_area='역사와 철학',
            credits=3, year_open=1, semester_open=1,
        )
        # 학문기초교양 — liberal_subtype만
        self.c_found = Course.objects.create(
            course_code='FOU1001', name='미적분학1', category='학문기초교양',
            college='교양', liberal_subtype='학문기초교양',
            credits=3, year_open=1, semester_open=1,
        )
        # 전공필수 — 두 키 모두 None
        self.c_major = Course.objects.create(
            course_code='MAJ1001', name='자료구조', category='전공필수',
            college='ICT융합대학', major='컴퓨터공학전공',
            credits=3, year_open=2, semester_open=1,
        )

    def test_POST_course_code로_5개_필드_자동_hydrate(self):
        res = self.client.post(self.url, {
            'course_code': 'COR1001',
            'year': 2024,
            'semester': 1,
            'grade_received': 'A',
        }, format='json')
        self.assertEqual(res.status_code, 201, msg=res.data)
        self.assertEqual(res.data['course_name'], '서양사')
        self.assertEqual(res.data['category'], '핵심교양')
        self.assertEqual(res.data['credits'], 3)
        self.assertEqual(res.data['liberal_subtype'], '핵심교양')
        self.assertEqual(res.data['core_area'], '역사와 철학')
        # 사용자 입력 4개 그대로
        self.assertEqual(res.data['course_code'], 'COR1001')
        self.assertEqual(res.data['year'], 2024)
        self.assertEqual(res.data['semester'], 1)
        self.assertEqual(res.data['grade_received'], 'A')

    def test_grade_received_생략하면_빈_문자열(self):
        res = self.client.post(self.url, {
            'course_code': 'COR1001', 'year': 2024, 'semester': 1,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['grade_received'], '')

    def test_평문_category_보내도_무시되고_카탈로그가_정답(self):
        # 사용자가 평문 category='임의값' 보내도 read_only라 무시
        res = self.client.post(self.url, {
            'course_code': 'COR1001', 'year': 2024, 'semester': 1,
            'category': '테스트교양', 'course_name': '다른이름',
            'credits': 99,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['category'], '핵심교양')
        self.assertEqual(res.data['course_name'], '서양사')
        self.assertEqual(res.data['credits'], 3)

    def test_학문기초교양_과목은_core_area_null(self):
        res = self.client.post(self.url, {
            'course_code': 'FOU1001', 'year': 2024, 'semester': 1,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['liberal_subtype'], '학문기초교양')
        self.assertIsNone(res.data['core_area'])

    def test_전공_과목은_두_키_모두_null(self):
        res = self.client.post(self.url, {
            'course_code': 'MAJ1001', 'year': 2024, 'semester': 1,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(res.data['liberal_subtype'])
        self.assertIsNone(res.data['core_area'])

    def test_존재하지_않는_course_code_400(self):
        res = self.client.post(self.url, {
            'course_code': '존재하지않음', 'year': 2024, 'semester': 1,
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('course_code', res.data)

    def test_unique_constraint_400(self):
        # 같은 (user, course_code, year, semester) 중복 등록 → 400
        self.client.post(self.url, {
            'course_code': 'COR1001', 'year': 2024, 'semester': 1,
        }, format='json')
        res = self.client.post(self.url, {
            'course_code': 'COR1001', 'year': 2024, 'semester': 1,
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('course_code', res.data)

    def test_학칙_7분류_각_과목별_자동_매칭(self):
        """#115 흡수 — Course의 category가 응답에 그대로 노출되는지 7분류 모두 검증."""
        from courses.models import Course
        for i, cat in enumerate(self.VALID_CATEGORIES):
            code = f'CAT{i:03d}'
            Course.objects.create(
                course_code=code, name=f'테스트{cat}', category=cat,
                college='교양' if cat.endswith('교양') else 'ICT융합대학',
                credits=3, year_open=1, semester_open=1,
            )
            res = self.client.post(self.url, {
                'course_code': code, 'year': 2024, 'semester': 1,
            }, format='json')
            self.assertEqual(res.status_code, 201, msg=f'{cat}: {res.data}')
            self.assertEqual(res.data['category'], cat)

    def test_PATCH_grade_received만_갱신(self):
        post = self.client.post(self.url, {
            'course_code': 'COR1001', 'year': 2024, 'semester': 1,
            'grade_received': 'B',
        }, format='json')
        hist_id = post.data['id']

        res = self.client.patch(
            f'{self.url}{hist_id}/', {'grade_received': 'A+'}, format='json',
        )
        self.assertEqual(res.status_code, 200, msg=res.data)
        self.assertEqual(res.data['grade_received'], 'A+')
        # 자동 채움 결과 + course_code 무변경
        self.assertEqual(res.data['course_name'], '서양사')
        self.assertEqual(res.data['course_code'], 'COR1001')

    def test_PATCH로_course_code_바꿔도_무시(self):
        """course_code는 read_only가 아니지만 update는 grade_received만 보고 처리."""
        post = self.client.post(self.url, {
            'course_code': 'COR1001', 'year': 2024, 'semester': 1,
        }, format='json')
        hist_id = post.data['id']

        # course_code도 같이 보내도 update는 grade_received만 변경
        res = self.client.patch(f'{self.url}{hist_id}/', {
            'course_code': 'MAJ1001',
            'grade_received': 'C',
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['grade_received'], 'C')
        # course_code·course_name 무변경
        self.assertEqual(res.data['course_code'], 'COR1001')
        self.assertEqual(res.data['course_name'], '서양사')

    def test_GET_응답에_9개_필드_모두_노출(self):
        self.client.post(self.url, {
            'course_code': 'COR1001', 'year': 2024, 'semester': 1,
            'grade_received': 'A',
        }, format='json')
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        results = res.data['results']
        self.assertEqual(len(results), 1)
        item = results[0]
        for k in ('id', 'course_code', 'year', 'semester', 'grade_received',
                  'course_name', 'category', 'credits',
                  'liberal_subtype', 'core_area'):
            self.assertIn(k, item, msg=f'필드 누락: {k}')


# ─── #135: prune_pending_signups cron ────────────────────────────────

class PrunePendingSignupsTests(TestCase):
    """24시간 이상 활동 없는 PendingSignup row 삭제 회귀 (#135).

    spec 5.1.1 후속 cron — 인증 미완료 dead row 청소.
    updated_at(auto_now) 기준 — resend 활동 시 자동 갱신되므로 활동 없는 row만 청소.
    """

    def _make_pending(self, email, updated_hours_ago=0):
        # PendingSignup.updated_at은 auto_now라 직접 박을 수 없어 update() 우회
        from accounts.models import PendingSignup
        from django.utils import timezone
        from datetime import timedelta

        pending = PendingSignup.objects.create(
            email=email,
            password_hash='hash',
            code='12345678',
            code_expires_at=timezone.now() + timedelta(minutes=10),
        )
        if updated_hours_ago > 0:
            PendingSignup.objects.filter(pk=pending.pk).update(
                updated_at=timezone.now() - timedelta(hours=updated_hours_ago),
            )
        return pending

    def test_24h_지난_row_삭제됨(self):
        from accounts.models import PendingSignup
        from django.core.management import call_command
        from io import StringIO

        self._make_pending('old@mju.ac.kr', updated_hours_ago=25)
        self._make_pending('recent@mju.ac.kr', updated_hours_ago=1)

        out = StringIO()
        call_command('prune_pending_signups', stdout=out)
        output = out.getvalue()

        self.assertIn('deleted=1', output)
        self.assertIn('threshold_hours=24', output)
        self.assertFalse(PendingSignup.objects.filter(email='old@mju.ac.kr').exists())
        self.assertTrue(PendingSignup.objects.filter(email='recent@mju.ac.kr').exists())

    def test_dry_run은_삭제하지_않음(self):
        from accounts.models import PendingSignup
        from django.core.management import call_command
        from io import StringIO

        self._make_pending('old@mju.ac.kr', updated_hours_ago=48)

        out = StringIO()
        call_command('prune_pending_signups', '--dry-run', stdout=out)
        output = out.getvalue()

        self.assertIn('dry-run', output)
        self.assertIn('target=1', output)
        # 실제 삭제는 안 됨
        self.assertTrue(PendingSignup.objects.filter(email='old@mju.ac.kr').exists())

    def test_hours_옵션으로_임계값_조정(self):
        from accounts.models import PendingSignup
        from django.core.management import call_command
        from io import StringIO

        self._make_pending('h6@mju.ac.kr', updated_hours_ago=8)
        self._make_pending('h30@mju.ac.kr', updated_hours_ago=30)

        # --hours 12 — 12h 이상 지난 것만 삭제
        out = StringIO()
        call_command('prune_pending_signups', '--hours', '12', stdout=out)
        output = out.getvalue()

        self.assertIn('deleted=1', output)
        self.assertIn('threshold_hours=12', output)
        self.assertTrue(PendingSignup.objects.filter(email='h6@mju.ac.kr').exists())
        self.assertFalse(PendingSignup.objects.filter(email='h30@mju.ac.kr').exists())

    def test_대상_0건이면_정상_종료(self):
        from django.core.management import call_command
        from io import StringIO

        # 모든 row가 활동 시점 직후 — 삭제 대상 없음
        self._make_pending('a@mju.ac.kr', updated_hours_ago=1)
        self._make_pending('b@mju.ac.kr', updated_hours_ago=0)

        out = StringIO()
        call_command('prune_pending_signups', stdout=out)
        output = out.getvalue()

        self.assertIn('deleted=0', output)


class LoginEndpointTests(TestCase):
    """spec 5.1.3 / 6.1 login 양수·음수 케이스 (#123 §2).

    기존 NonASCIIEmailRejectionTests는 비ASCII 거부만 검증. 본 클래스는
    정상 로그인 + 비밀번호/이메일 오류 + 미인증 사용자 4 케이스 커버.
    """

    def setUp(self):
        self.client = APIClient()
        # 정상 인증된 사용자 — 정상 로그인 케이스 베이스
        self.verified = User.objects.create_user(
            email='login@mju.ac.kr',
            password=VALID_PWD,
        )
        self.verified.is_email_verified = True
        self.verified.save(update_fields=['is_email_verified'])

    def test_정상_자격증명_200_JWT_발급(self):
        res = self.client.post(LOGIN_URL, {
            'email': 'login@mju.ac.kr',
            'password': VALID_PWD,
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)
        # JWT 토큰은 빈 문자열이면 안 됨
        self.assertTrue(res.data['access'])
        self.assertTrue(res.data['refresh'])

    def test_잘못된_비밀번호_401(self):
        res = self.client.post(LOGIN_URL, {
            'email': 'login@mju.ac.kr',
            'password': 'WrongPass123!',
        }, format='json')
        self.assertEqual(res.status_code, 401)
        # 타이밍 공격 방어로 미존재 이메일과 동일 메시지
        self.assertIn('이메일 또는 비밀번호', res.data['detail'])

    def test_미존재_이메일_401(self):
        res = self.client.post(LOGIN_URL, {
            'email': 'nobody@mju.ac.kr',
            'password': VALID_PWD,
        }, format='json')
        self.assertEqual(res.status_code, 401)
        # 미존재 이메일이라는 정보를 응답으로 흘리면 enumeration 공격 가능 → 잘못된 비번과 동일 메시지여야 함
        self.assertIn('이메일 또는 비밀번호', res.data['detail'])

    def test_미인증_사용자_403(self):
        # 카카오 first-login 이전 등 is_email_verified=False 상태로 User row 존재 시
        unverified = User.objects.create_user(
            email='unverified@mju.ac.kr',
            password=VALID_PWD,
        )
        # default is_email_verified=False 그대로
        self.assertFalse(unverified.is_email_verified)

        res = self.client.post(LOGIN_URL, {
            'email': 'unverified@mju.ac.kr',
            'password': VALID_PWD,
        }, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertIn('이메일 인증', res.data['detail'])

    def test_PendingSignup만_있는_상태는_401(self):
        # 신규 signup 흐름 — verify 전엔 User 미생성 → login 시도 시 미존재로 처리
        PendingSignup.objects.create(
            email='pending@mju.ac.kr',
            password_hash='dummy_hash',
            code='123456',
            code_expires_at=timezone.now() + timedelta(minutes=10),
        )
        res = self.client.post(LOGIN_URL, {
            'email': 'pending@mju.ac.kr',
            'password': VALID_PWD,
        }, format='json')
        # User row가 없으므로 미존재 이메일과 같은 401 (403 아님)
        self.assertEqual(res.status_code, 401)
        self.assertIn('이메일 또는 비밀번호', res.data['detail'])


# ─── #149: CurrentCourse offering_id 자동 hydrate + building 제거 ──────

class CurrentCourseHydrateTests(TestCase):
    """POST/PUT/PATCH /api/v1/accounts/current-courses/ 흐름 검증 (#149).

    offering_id 한 개로 7개 평문 필드(course_name·code·요일·시간·교수·강의실)가
    CourseOffering + CourseSchedule에서 자동 채워지고, 응답·DB에 building 키 없음.
    """

    url = '/api/v1/accounts/current-courses/'

    def setUp(self):
        from datetime import time

        from courses.models import Course, CourseOffering, CourseSchedule

        self.user = User.objects.create_user(email='cc@mju.ac.kr', password=VALID_PWD)
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # 테스트용 카탈로그 (seed_data 1행 모사)
        self.course = Course.objects.create(
            course_code='컴공296', name='컴퓨터하드웨어',
            college='반도체·ICT대학', department='컴퓨터정보통신공학부',
            major='컴퓨터공학전공',
            category='전공선택', credits=3, year_open=2, semester_open=1,
            professor='박정민',
        )
        self.offering = CourseOffering.objects.create(
            course=self.course, year=2026, semester=1, section_no='0729',
            professor='박정민', capacity=35,
        )
        CourseSchedule.objects.create(
            course=self.course, offering=self.offering,
            day_of_week='화', start_time=time(14, 0), end_time=time(16, 50),
            building='', room='Y5420',
        )

    def test_post_offering_id_creates_currentcourse_with_hydration(self):
        res = self.client.post(self.url, {'offering_id': self.offering.id}, format='json')
        self.assertEqual(res.status_code, 201, msg=res.data)
        # 응답 9키 (7 평문 + id + offering_id #226) — building 없음
        self.assertEqual(
            set(res.data.keys()),
            {'id', 'offering_id', 'course_name', 'course_code', 'day_of_week',
             'start_time', 'end_time', 'professor', 'room'},
        )
        self.assertEqual(res.data['course_name'], '컴퓨터하드웨어')
        self.assertEqual(res.data['course_code'], '컴공296')
        self.assertEqual(res.data['day_of_week'], '화')
        self.assertEqual(res.data['start_time'], '14:00:00')
        self.assertEqual(res.data['end_time'], '16:50:00')
        self.assertEqual(res.data['professor'], '박정민')
        self.assertEqual(res.data['room'], 'Y5420')
        # #226: 응답에 offering_id가 입력값과 동일하게 round-trip
        self.assertEqual(res.data['offering_id'], self.offering.id)

    def test_post_multiday_offering_combines_day_of_week(self):
        """월수 분반(CourseSchedule 2행) → day_of_week='월수' 단일 행 (#215)."""
        from datetime import time

        from courses.models import Course, CourseOffering, CourseSchedule
        from accounts.models import CurrentCourse

        c = Course.objects.create(
            course_code='컴공101', name='객체지향프로그래밍1',
            category='전공필수', credits=3, year_open=1, semester_open=1,
        )
        o = CourseOffering.objects.create(
            course=c, year=2026, semester=1, section_no='0693',
            professor='조세형', capacity=40,
        )
        # 요일 역순(수→월)으로 생성해 정렬 키가 실제로 동작하는지도 검증
        CourseSchedule.objects.create(
            course=c, offering=o, day_of_week='수',
            start_time=time(9, 0), end_time=time(10, 50), room='Y2510',
        )
        CourseSchedule.objects.create(
            course=c, offering=o, day_of_week='월',
            start_time=time(9, 0), end_time=time(10, 50), room='Y2510',
        )
        res = self.client.post(self.url, {'offering_id': o.id}, format='json')
        self.assertEqual(res.status_code, 201, msg=res.data)
        self.assertEqual(res.data['day_of_week'], '월수')  # 월화수목금 순 합본
        self.assertEqual(res.data['start_time'], '09:00:00')
        # 단일 행만 생성 (요일별 분리 X)
        self.assertEqual(
            CurrentCourse.objects.filter(user=self.user, course_code='컴공101').count(), 1,
        )

    def test_post_nonexistent_offering_id_returns_400(self):
        res = self.client.post(self.url, {'offering_id': 99999}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('offering_id', res.data)

    def test_post_offering_without_schedule_returns_400(self):
        from courses.models import CourseOffering

        empty = CourseOffering.objects.create(
            course=self.course, year=2026, semester=1, section_no='0730',
            professor='박정민',
        )
        res = self.client.post(self.url, {'offering_id': empty.id}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('offering_id', res.data)

    def test_unique_constraint_returns_400(self):
        # 1차 등록
        res = self.client.post(self.url, {'offering_id': self.offering.id}, format='json')
        self.assertEqual(res.status_code, 201)
        # 같은 (day_of_week, start_time) 슬롯에 또 다른 offering 등록 → 400
        from datetime import time

        from courses.models import Course, CourseOffering, CourseSchedule

        other_course = Course.objects.create(
            course_code='컴공297', name='운영체제',
            college='반도체·ICT대학', department='컴퓨터정보통신공학부',
            major='컴퓨터공학전공',
            category='전공선택', credits=3, year_open=3, semester_open=1,
        )
        other_offering = CourseOffering.objects.create(
            course=other_course, year=2026, semester=1, section_no='0801',
            professor='이교수',
        )
        CourseSchedule.objects.create(
            course=other_course, offering=other_offering,
            day_of_week='화', start_time=time(14, 0), end_time=time(16, 50),
            building='', room='Y5421',
        )
        res2 = self.client.post(self.url, {'offering_id': other_offering.id}, format='json')
        self.assertEqual(res2.status_code, 400)
        self.assertIn('offering_id', res2.data)

    def test_list_response_excludes_building(self):
        self.client.post(self.url, {'offering_id': self.offering.id}, format='json')
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        # 페이지네이션이 results 키일 수도, 리스트 자체일 수도 — 모두 대응
        items = res.data['results'] if isinstance(res.data, dict) else res.data
        self.assertEqual(len(items), 1)
        self.assertNotIn('building', items[0])

    def test_put_with_offering_id_rehydrates(self):
        # 1차 등록
        res1 = self.client.post(self.url, {'offering_id': self.offering.id}, format='json')
        cc_id = res1.data['id']
        # 다른 분반 만들기 (다른 요일·시간으로 unique 충돌 회피)
        from datetime import time

        from courses.models import Course, CourseOffering, CourseSchedule

        c2 = Course.objects.create(
            course_code='컴공297', name='운영체제',
            college='반도체·ICT대학', department='컴퓨터정보통신공학부',
            major='컴퓨터공학전공',
            category='전공선택', credits=3, year_open=3, semester_open=1,
        )
        o2 = CourseOffering.objects.create(
            course=c2, year=2026, semester=1, section_no='0801', professor='이교수',
        )
        CourseSchedule.objects.create(
            course=c2, offering=o2,
            day_of_week='목', start_time=time(9, 0), end_time=time(11, 50),
            building='', room='Y5437',
        )
        # PUT으로 분반 변경 → 7개 필드 재 hydrate
        res2 = self.client.put(
            f'{self.url}{cc_id}/', {'offering_id': o2.id}, format='json',
        )
        self.assertEqual(res2.status_code, 200, msg=res2.data)
        self.assertEqual(res2.data['course_name'], '운영체제')
        self.assertEqual(res2.data['day_of_week'], '목')
        self.assertEqual(res2.data['room'], 'Y5437')
        # #226: offering_id도 새 값으로 갱신
        self.assertEqual(res2.data['offering_id'], o2.id)

    def test_building_field_removed_from_model(self):
        """모델에서 building 필드가 실제로 제거됐는지 회귀 (#149)."""
        from accounts.models import CurrentCourse

        field_names = {f.name for f in CurrentCourse._meta.get_fields()}
        self.assertNotIn('building', field_names)

    # ─── #226: offering_id 응답·DB 노출 ─────────────────────────────────

    def test_db_stores_offering_id_after_post(self):
        """POST 후 DB row에 offering_id가 정상 저장됨 (#226)."""
        from accounts.models import CurrentCourse
        res = self.client.post(self.url, {'offering_id': self.offering.id}, format='json')
        cc_id = res.data['id']
        cc = CurrentCourse.objects.get(pk=cc_id)
        self.assertEqual(cc.offering_id, self.offering.id)

    def test_list_response_includes_offering_id(self):
        """GET /current-courses/ 응답에 offering_id 노출 (#226)."""
        self.client.post(self.url, {'offering_id': self.offering.id}, format='json')
        res = self.client.get(self.url)
        items = res.data['results'] if isinstance(res.data, dict) else res.data
        self.assertEqual(len(items), 1)
        self.assertIn('offering_id', items[0])
        self.assertEqual(items[0]['offering_id'], self.offering.id)

    def test_profile_response_includes_offering_id(self):
        """GET /accounts/profile/ current_courses[]에 offering_id 노출 (#226).

        프론트가 분반 역추적·PUT 재전송에 사용.
        """
        self.client.post(self.url, {'offering_id': self.offering.id}, format='json')
        res = self.client.get('/api/v1/accounts/profile/')
        self.assertEqual(res.status_code, 200, msg=res.data)
        cc_list = res.data['current_courses']
        self.assertEqual(len(cc_list), 1)
        self.assertIn('offering_id', cc_list[0])
        self.assertEqual(cc_list[0]['offering_id'], self.offering.id)


# ─── #230: offering_id backfill 명령 ─────────────────────────────────

class BackfillCurrentCourseOfferingIdTests(TestCase):
    """`backfill_current_course_offering_id` 명령 — 기존 NULL row를 매칭으로 채움."""

    def setUp(self):
        from datetime import time

        from courses.models import Course, CourseOffering, CourseSchedule
        from accounts.models import CurrentCourse

        self.user = User.objects.create_user(email='bf@mju.ac.kr', password=VALID_PWD)
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])

        # 카탈로그 — 2025-2 분반과 2026-1 분반 두 학기
        course = Course.objects.create(
            course_code='컴공296', name='컴퓨터하드웨어',
            college='반도체·ICT대학', department='컴퓨터정보통신공학부',
            major='컴퓨터공학전공',
            category='전공선택', credits=3, year_open=2, semester_open=1,
        )
        # 가장 최근 학기 (우선 매칭 대상)
        self.recent_offering = CourseOffering.objects.create(
            course=course, year=2026, semester=1, section_no='0729',
            professor='박정민',
        )
        CourseSchedule.objects.create(
            course=course, offering=self.recent_offering,
            day_of_week='화', start_time=time(14, 0), end_time=time(16, 50),
            building='', room='Y5420',
        )
        # 옛 학기 (먼저 매칭되면 안 됨)
        old = CourseOffering.objects.create(
            course=course, year=2025, semester=2, section_no='0501',
            professor='이교수',
        )
        CourseSchedule.objects.create(
            course=course, offering=old,
            day_of_week='화', start_time=time(14, 0), end_time=time(16, 50),
            building='', room='Y5301',
        )

        # 기존 row (마이그 직후 상태 모사 — offering_id NULL)
        self.cc_null = CurrentCourse.objects.create(
            user=self.user,
            course_name='컴퓨터하드웨어', course_code='컴공296',
            day_of_week='화', start_time=time(14, 0), end_time=time(16, 50),
            professor='박정민', room='Y5420',
            # offering_id 미설정 → NULL
        )

    def _run(self, *flags):
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command('backfill_current_course_offering_id', *flags, stdout=out)
        return out.getvalue()

    def test_매칭_성공시_offering_id_채움_가장_최근학기_우선(self):
        out = self._run()
        self.cc_null.refresh_from_db()
        self.assertEqual(self.cc_null.offering_id, self.recent_offering.id)
        self.assertIn('매칭 1건', out)

    def test_dry_run은_DB_변경_없음(self):
        out = self._run('--dry-run')
        self.cc_null.refresh_from_db()
        self.assertIsNone(self.cc_null.offering_id)
        self.assertIn('dry-run', out)

    def test_매칭_안되는_row는_NULL_유지(self):
        from datetime import time

        from accounts.models import CurrentCourse

        # 카탈로그에 없는 course_code
        no_match = CurrentCourse.objects.create(
            user=self.user,
            course_name='없는과목', course_code='UNKNOWN001',
            day_of_week='월', start_time=time(9, 0), end_time=time(10, 30),
            professor='', room='',
        )
        self._run()
        no_match.refresh_from_db()
        self.assertIsNone(no_match.offering_id)

    def test_이미_채워진_row는_안_건드림_멱등(self):
        from accounts.models import CurrentCourse

        self.cc_null.offering_id = 99999  # 임의 값
        self.cc_null.save(update_fields=['offering_id'])
        self._run()
        self.cc_null.refresh_from_db()
        self.assertEqual(self.cc_null.offering_id, 99999)  # 그대로

    def test_NULL_row_없으면_조용히_종료(self):
        from accounts.models import CurrentCourse
        CurrentCourse.objects.all().update(offering_id=1)  # 모두 채운 상태
        out = self._run()
        self.assertIn('NULL row 없음', out)
