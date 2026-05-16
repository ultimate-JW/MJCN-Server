import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.db import OperationalError, transaction
from django.utils import timezone

from .models import EmailVerification


# ─── 카카오 OAuth (spec 5.1.3) ──────────────────────────────────────

KAKAO_TOKEN_URL = 'https://kauth.kakao.com/oauth/token'
KAKAO_USER_INFO_URL = 'https://kapi.kakao.com/v2/user/me'


class KakaoOAuthError(Exception):
    """카카오 OAuth 호출/응답 오류의 베이스. 4xx 응답·네트워크 실패 모두 포함."""

    def __init__(self, message: str, *, kind: str = 'unknown'):
        super().__init__(message)
        # 'invalid_code' : authorization_code 만료/재사용/redirect_uri 불일치
        # 'upstream'     : 카카오 측 5xx 또는 네트워크 장애
        # 'malformed'    : 응답 본문 형식 이상
        self.kind = kind


def exchange_kakao_token(code: str) -> dict:
    """authorization_code → 카카오 access_token 교환 (spec 5.1.3 1단계).

    응답 dict 예시:
      {
        "access_token": "...",
        "token_type": "bearer",
        "refresh_token": "...",
        "expires_in": 21599,
        "scope": "...",
        "refresh_token_expires_in": 5183999
      }
    """
    payload = {
        'grant_type': 'authorization_code',
        'client_id': settings.KAKAO_REST_API_KEY,
        'redirect_uri': settings.KAKAO_REDIRECT_URI,
        'code': code,
    }
    # client_secret은 콘솔에서 발급한 경우에만 포함 (없으면 생략 OK)
    if settings.KAKAO_CLIENT_SECRET:
        payload['client_secret'] = settings.KAKAO_CLIENT_SECRET

    try:
        response = requests.post(
            KAKAO_TOKEN_URL,
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8'},
            timeout=settings.KAKAO_REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise KakaoOAuthError(f'카카오 token API 호출 실패: {e}', kind='upstream') from e

    if response.status_code >= 500:
        raise KakaoOAuthError(
            f'카카오 token API 5xx: {response.status_code}',
            kind='upstream',
        )
    if response.status_code != 200:
        # 4xx — invalid code, redirect_uri 불일치 등
        raise KakaoOAuthError(
            f'카카오 token 교환 실패 ({response.status_code}): {response.text[:200]}',
            kind='invalid_code',
        )

    try:
        data = response.json()
    except ValueError as e:
        raise KakaoOAuthError(f'카카오 token 응답 JSON 파싱 실패: {e}', kind='malformed') from e

    if 'access_token' not in data:
        raise KakaoOAuthError(
            f'카카오 token 응답에 access_token 없음: {data}',
            kind='malformed',
        )
    return data


def fetch_kakao_user(access_token: str) -> dict:
    """카카오 access_token → 사용자 정보 조회 (spec 5.1.3 2단계).

    반환 dict 예시:
      {
        "id": 12345678,                                    # 카카오 user id (kakao_id)
        "kakao_account": {
          "email": "user@example.com",                     # 동의 안 했으면 없음
          "email_needs_agreement": false,
          "profile": {
            "nickname": "홍길동",
            "profile_image_url": "https://..."
          }
        }
      }
    """
    try:
        response = requests.get(
            KAKAO_USER_INFO_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=settings.KAKAO_REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise KakaoOAuthError(f'카카오 user API 호출 실패: {e}', kind='upstream') from e

    if response.status_code >= 500:
        raise KakaoOAuthError(
            f'카카오 user API 5xx: {response.status_code}',
            kind='upstream',
        )
    if response.status_code != 200:
        raise KakaoOAuthError(
            f'카카오 user 조회 실패 ({response.status_code}): {response.text[:200]}',
            kind='invalid_code',
        )

    try:
        data = response.json()
    except ValueError as e:
        raise KakaoOAuthError(f'카카오 user 응답 JSON 파싱 실패: {e}', kind='malformed') from e

    if 'id' not in data:
        raise KakaoOAuthError(f'카카오 user 응답에 id 없음: {data}', kind='malformed')
    return data


def extract_kakao_profile(user_info: dict) -> dict:
    """카카오 user_info 응답에서 우리 모델에 저장할 필드만 추려서 반환.

    이메일 동의 안 한 케이스에 대비해 모든 필드 안전하게 .get() 처리.
    """
    account = user_info.get('kakao_account') or {}
    profile = account.get('profile') or {}

    # 이메일은 사용자가 동의 안 했거나 카카오 계정에 없을 수 있음 → 빈 문자열로
    email = ''
    if not account.get('email_needs_agreement', False):
        email = (account.get('email') or '').strip().lower()

    return {
        'kakao_id': str(user_info['id']),  # CharField라 문자열 변환
        'email': email,
        'name': (profile.get('nickname') or '').strip(),
    }


def generate_verification_code():
    # 인증 코드는 암호학적으로 안전한 PRNG로 생성해야 함
    # (random 모듈은 예측 가능하여 인증/보안 용도에 부적합)
    return ''.join(str(secrets.randbelow(10)) for _ in range(6))


def send_verification_email(user, purpose='signup'):
    # DB 변경(기존 코드 무효화 + 새 코드 생성)을 하나의 트랜잭션으로 묶어
    # 중간 실패 시 "기존 코드는 무효화됐는데 새 코드는 없는" 상태 방지.
    # send_mail은 트랜잭션 밖에서 호출 — 커밋된 후 발송해야 메일 발송 실패 시
    # DB 롤백으로 기존 유효 코드를 되돌릴 수 있고, 발송만 성공하고 DB 롤백되는
    # 역전 케이스도 방지.
    with transaction.atomic():
        EmailVerification.objects.filter(
            user=user,
            purpose=purpose,
            is_used=False,
        ).update(is_used=True)

        code = generate_verification_code()
        expires_at = timezone.now() + timedelta(minutes=3)

        EmailVerification.objects.create(
            user=user,
            code=code,
            purpose=purpose,
            expires_at=expires_at,
        )

    subject_map = {
        'signup': '[MJCN] 이메일 인증 코드',
        'password_reset': '[MJCN] 비밀번호 재설정 인증 코드',
    }

    send_mail(
        subject=subject_map.get(purpose, '[MJCN] 인증 코드'),
        message=f'인증 코드: {code}\n\n3분 이내에 입력해주세요.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )

    return code


def verify_code(user, code, purpose='signup', consume=True):
    # 동시성 방어: 같은 코드로 동시에 두 요청이 들어올 때
    # is_used 체크와 업데이트 사이의 race condition 방지.
    # consume=False: 코드 유효성만 확인하고 소모하지 않음 (verify→confirm
    # 2단계 플로우 지원용; verify 단계에서 소모되면 confirm이 실패함)
    try:
        with transaction.atomic():
            verification = (
                EmailVerification.objects
                .select_for_update()
                .filter(
                    user=user,
                    code=code,
                    purpose=purpose,
                    is_used=False,
                )
                .order_by('-created_at')
                .first()
            )

            if not verification:
                return None, '인증 코드가 일치하지 않습니다.'

            if timezone.now() > verification.expires_at:
                return None, '인증 코드가 만료되었습니다. 다시 요청해주세요.'

            if consume:
                verification.is_used = True
                verification.save(update_fields=['is_used'])
            return verification, None
    except OperationalError:
        # SQLite의 경우 row-level lock 미지원으로 database lock 예외 발생 가능
        # 이 경우 다른 요청이 이미 처리 중이라는 뜻이므로 동일한 일반 오류로 응답
        return None, '인증 코드가 일치하지 않습니다.'
