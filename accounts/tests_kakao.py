"""카카오 OAuth 로그인 테스트 (spec 5.1.3 / 6.1).

requests 호출은 unittest.mock으로 가짜 응답 주입 — 실제 카카오 API 호출 없음.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.services import (
    KakaoOAuthError, extract_kakao_profile,
    exchange_kakao_token, fetch_kakao_user,
)

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────
# 헬퍼 — requests 응답 mock
# ─────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int, body=None, text: str = ''):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError('no json body')
        return self._body


def _ok_token_response():
    return _FakeResponse(200, {
        'access_token': 'kakao_access_xxx',
        'refresh_token': 'kakao_refresh_xxx',
        'expires_in': 21599,
        'token_type': 'bearer',
    })


def _ok_user_response(kakao_id=12345678, email='kakao@example.com', email_agreed=True):
    account = {
        'profile': {'nickname': '카카오유저', 'profile_image_url': 'http://x'},
        'email_needs_agreement': not email_agreed,
    }
    if email_agreed:
        account['email'] = email
    return _FakeResponse(200, {
        'id': kakao_id,
        'kakao_account': account,
    })


# ─────────────────────────────────────────────────────────────────────
# services.py — extract_kakao_profile
# ─────────────────────────────────────────────────────────────────────

class ExtractKakaoProfileTests(TestCase):

    def test_정상_프로필_추출(self):
        info = _ok_user_response(kakao_id=42, email='u@k.com').json()
        p = extract_kakao_profile(info)
        self.assertEqual(p, {
            'kakao_id': '42',  # str로 변환됨
            'email': 'u@k.com',
            'name': '카카오유저',
        })

    def test_이메일_동의_거부면_빈_이메일(self):
        info = _ok_user_response(email_agreed=False).json()
        p = extract_kakao_profile(info)
        self.assertEqual(p['email'], '')

    def test_kakao_account_없는_응답_안전_처리(self):
        info = {'id': 99}
        p = extract_kakao_profile(info)
        self.assertEqual(p['kakao_id'], '99')
        self.assertEqual(p['email'], '')
        self.assertEqual(p['name'], '')

    def test_이메일_대소문자_정규화(self):
        info = _ok_user_response(email='User@Example.COM').json()
        p = extract_kakao_profile(info)
        self.assertEqual(p['email'], 'user@example.com')


# ─────────────────────────────────────────────────────────────────────
# services.py — exchange_kakao_token / fetch_kakao_user
# ─────────────────────────────────────────────────────────────────────

@override_settings(
    KAKAO_REST_API_KEY='test-key',
    KAKAO_REDIRECT_URI='http://test/cb',
    KAKAO_CLIENT_SECRET='test-secret',
)
class ExchangeKakaoTokenTests(TestCase):

    @patch('accounts.services.requests.post')
    def test_200_정상_응답(self, mock_post):
        mock_post.return_value = _ok_token_response()
        result = exchange_kakao_token('code123')
        self.assertEqual(result['access_token'], 'kakao_access_xxx')
        # client_secret이 payload에 포함되어야 함 (settings에 있을 때)
        sent_data = mock_post.call_args.kwargs['data']
        self.assertEqual(sent_data['client_secret'], 'test-secret')
        self.assertEqual(sent_data['code'], 'code123')

    @patch('accounts.services.requests.post')
    def test_400_invalid_code는_invalid_code_에러(self, mock_post):
        mock_post.return_value = _FakeResponse(400, text='{"error":"invalid_grant"}')
        with self.assertRaises(KakaoOAuthError) as ctx:
            exchange_kakao_token('expired')
        self.assertEqual(ctx.exception.kind, 'invalid_code')

    @patch('accounts.services.requests.post')
    def test_500은_upstream_에러(self, mock_post):
        mock_post.return_value = _FakeResponse(500, text='Server Error')
        with self.assertRaises(KakaoOAuthError) as ctx:
            exchange_kakao_token('code')
        self.assertEqual(ctx.exception.kind, 'upstream')

    @patch('accounts.services.requests.post')
    def test_네트워크_실패는_upstream_에러(self, mock_post):
        import requests as req_mod
        mock_post.side_effect = req_mod.ConnectionError('boom')
        with self.assertRaises(KakaoOAuthError) as ctx:
            exchange_kakao_token('code')
        self.assertEqual(ctx.exception.kind, 'upstream')

    @patch('accounts.services.requests.post')
    def test_access_token_누락은_malformed_에러(self, mock_post):
        mock_post.return_value = _FakeResponse(200, {'token_type': 'bearer'})
        with self.assertRaises(KakaoOAuthError) as ctx:
            exchange_kakao_token('code')
        self.assertEqual(ctx.exception.kind, 'malformed')


@override_settings(KAKAO_REQUEST_TIMEOUT=5)
class FetchKakaoUserTests(TestCase):

    @patch('accounts.services.requests.get')
    def test_정상_사용자_정보_조회(self, mock_get):
        mock_get.return_value = _ok_user_response(kakao_id=777)
        data = fetch_kakao_user('access_xxx')
        self.assertEqual(data['id'], 777)
        # Authorization 헤더 검증
        sent_headers = mock_get.call_args.kwargs['headers']
        self.assertEqual(sent_headers['Authorization'], 'Bearer access_xxx')

    @patch('accounts.services.requests.get')
    def test_id_누락은_malformed(self, mock_get):
        mock_get.return_value = _FakeResponse(200, {'kakao_account': {}})
        with self.assertRaises(KakaoOAuthError) as ctx:
            fetch_kakao_user('access_xxx')
        self.assertEqual(ctx.exception.kind, 'malformed')


# ─────────────────────────────────────────────────────────────────────
# views.py — kakao_login 통합 (full flow with mocks)
# ─────────────────────────────────────────────────────────────────────

@override_settings(
    KAKAO_REST_API_KEY='test-key',
    KAKAO_REDIRECT_URI='http://test/cb',
)
class KakaoLoginIntegrationTests(TestCase):
    URL = '/api/v1/accounts/login/kakao/'

    def setUp(self):
        self.client = APIClient()

    def _patch_kakao(self, token_resp=None, user_resp=None):
        """카카오 두 API 응답을 동시에 mock."""
        token_resp = token_resp or _ok_token_response()
        user_resp = user_resp or _ok_user_response()
        return (
            patch('accounts.services.requests.post', return_value=token_resp),
            patch('accounts.services.requests.get', return_value=user_resp),
        )

    def test_신규_사용자_가입(self):
        post_mock, get_mock = self._patch_kakao()
        with post_mock, get_mock:
            res = self.client.post(self.URL, {'authorization_code': 'code'}, format='json')

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['is_new_user'])
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)
        # DB 검증
        user = User.objects.get(kakao_id='12345678')
        self.assertEqual(user.email, 'kakao@example.com')
        self.assertEqual(user.name, '카카오유저')
        self.assertTrue(user.is_email_verified)
        self.assertFalse(user.is_onboarding_completed)
        # 카카오 전용 계정 — 비밀번호 unusable
        self.assertFalse(user.has_usable_password())

    def test_기존_사용자_재로그인(self):
        existing = User.objects.create(
            email='kakao@example.com',
            name='기존이름',
            kakao_id='12345678',
            is_email_verified=True,
            is_onboarding_completed=True,
        )
        existing.set_unusable_password()
        existing.save(update_fields=['password'])

        post_mock, get_mock = self._patch_kakao()
        with post_mock, get_mock:
            res = self.client.post(self.URL, {'authorization_code': 'code'}, format='json')

        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['is_new_user'])
        self.assertEqual(res.data['user']['id'], existing.id)
        # User 추가 생성 안 됨
        self.assertEqual(User.objects.filter(kakao_id='12345678').count(), 1)

    def test_이메일_동의_거부_케이스(self):
        post_mock, get_mock = self._patch_kakao(
            user_resp=_ok_user_response(email_agreed=False),
        )
        with post_mock, get_mock:
            res = self.client.post(self.URL, {'authorization_code': 'code'}, format='json')

        self.assertEqual(res.status_code, 200)
        user = User.objects.get(kakao_id='12345678')
        self.assertEqual(user.email, '')  # 빈 채로 저장
        self.assertTrue(res.data['is_new_user'])

    def test_이메일_중복시_기존_계정에_kakao_id_연동(self):
        # 일반 이메일 가입한 사용자가 같은 이메일로 카카오 로그인
        existing = User.objects.create(
            email='kakao@example.com',
            name='일반가입자',
            is_email_verified=True,
        )
        existing.set_password('pw1234abc')
        existing.save(update_fields=['password'])

        post_mock, get_mock = self._patch_kakao()
        with post_mock, get_mock:
            res = self.client.post(self.URL, {'authorization_code': 'code'}, format='json')

        self.assertEqual(res.status_code, 200)
        # 신규 가입 아님 — 기존 계정 연동
        self.assertFalse(res.data['is_new_user'])
        self.assertEqual(res.data['user']['id'], existing.id)
        # 같은 행에 kakao_id 추가됨
        existing.refresh_from_db()
        self.assertEqual(existing.kakao_id, '12345678')
        # 기존 비밀번호도 유지 (일반 로그인도 여전히 가능)
        self.assertTrue(existing.has_usable_password())
        # User 추가 생성 안 됨
        self.assertEqual(User.objects.count(), 1)

    def test_authorization_code_누락은_400(self):
        res = self.client.post(self.URL, {}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_카카오_token_교환_실패는_401(self):
        post_mock, get_mock = self._patch_kakao(
            token_resp=_FakeResponse(400, text='{"error":"invalid_grant"}'),
        )
        with post_mock, get_mock:
            res = self.client.post(self.URL, {'authorization_code': 'expired'}, format='json')
        self.assertEqual(res.status_code, 401)
        # 신규 User 생성 안 됨
        self.assertEqual(User.objects.count(), 0)

    def test_카카오_user_API_5xx는_502(self):
        post_mock, get_mock = self._patch_kakao(
            user_resp=_FakeResponse(503, text='Service Unavailable'),
        )
        with post_mock, get_mock:
            res = self.client.post(self.URL, {'authorization_code': 'code'}, format='json')
        self.assertEqual(res.status_code, 502)
        self.assertEqual(User.objects.count(), 0)

    def test_네트워크_실패는_502(self):
        import requests as req_mod
        with patch('accounts.services.requests.post', side_effect=req_mod.ConnectionError()):
            res = self.client.post(self.URL, {'authorization_code': 'code'}, format='json')
        self.assertEqual(res.status_code, 502)
