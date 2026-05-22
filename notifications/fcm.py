"""firebase-admin SDK 래퍼 — FCM 푸시 송신 (spec 9.3).

settings.FIREBASE_CREDENTIALS_PATH로 서비스 계정 자격증명을 로드한다.
미설정 시 is_configured()=False — 호출자가 graceful no-op 하도록 한다
(이메일 콘솔 백엔드 폴백과 동일 철학). DB 접근 없음.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.conf import settings

logger = logging.getLogger(__name__)

# firebase-admin send_each_for_multicast 1회 호출 토큰 상한 (SDK 하드 제한).
FCM_MULTICAST_LIMIT = 500

# firebase_admin App 이름 — Django 기본 네임스페이스와 분리.
_APP_NAME = 'mjcn-fcm'

_app = None  # firebase_admin.App 싱글턴 (프로세스 내)


class PushClientError(Exception):
    """푸시 인프라 수준 오류 (앱 초기화 실패, 멀티캐스트 호출 전체 실패)."""


@dataclass
class TokenSendResult:
    """토큰 1개의 송신 결과."""
    token: str
    success: bool
    is_dead: bool = False   # 영구 무효 토큰 → FCMDevice 비활성화 대상
    error: str = ''         # transient 오류 메시지 (재시도 대상)


@dataclass
class MulticastResult:
    """send_to_tokens 1회 호출 결과 집계."""
    results: list[TokenSendResult] = field(default_factory=list)

    @property
    def has_transient_failure(self) -> bool:
        """재시도가 필요한 (성공도 죽은 토큰도 아닌) 실패가 있는가."""
        return any(not r.success and not r.is_dead for r in self.results)

    @property
    def dead_tokens(self) -> list[str]:
        return [r.token for r in self.results if r.is_dead]


def is_configured() -> bool:
    """Firebase 자격증명이 설정되어 있는가."""
    return bool(getattr(settings, 'FIREBASE_CREDENTIALS_PATH', ''))


def get_app():
    """firebase_admin App 싱글턴 반환. 미설정/초기화 실패 시 PushClientError."""
    global _app
    if _app is not None:
        return _app
    if not is_configured():
        raise PushClientError('FIREBASE_CREDENTIALS_PATH 미설정 — FCM 송신 불가.')
    # firebase_admin은 무겁다 — 미설정/일반 API 서빙 시 import되지 않도록 lazy.
    import firebase_admin
    from firebase_admin import credentials
    try:
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        _app = firebase_admin.initialize_app(cred, name=_APP_NAME)
    except Exception as e:
        raise PushClientError(f'firebase-admin 초기화 실패: {e}') from e
    return _app


def reset_app() -> None:
    """App 싱글턴 초기화 (테스트 정리용)."""
    global _app
    if _app is not None:
        try:
            import firebase_admin
            firebase_admin.delete_app(_app)
        except Exception:
            pass
    _app = None


def send_to_tokens(tokens, *, title: str, body: str,
                   data: dict | None = None) -> MulticastResult:
    """토큰 묶음(<=500)에 동일 알림을 멀티캐스트 전송.

    Args:
        tokens: FCM 등록 토큰 list (<= FCM_MULTICAST_LIMIT).
        title / body: 알림 표시 내용.
        data: 추가 data 페이로드. 값은 str로 강제 변환됨.

    Returns:
        MulticastResult — 토큰별 성공 / 죽은 토큰 / transient 실패 분류.

    Raises:
        PushClientError: 앱 초기화 실패 또는 멀티캐스트 호출 자체 실패.
    """
    from firebase_admin import messaging
    from firebase_admin import exceptions as fb_exc

    app = get_app()
    tokens = list(tokens)
    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
    )
    try:
        batch = messaging.send_each_for_multicast(message, app=app)
    except Exception as e:
        raise PushClientError(f'FCM 멀티캐스트 호출 실패: {e}') from e

    out = MulticastResult()
    for token, resp in zip(tokens, batch.responses):
        if resp.success:
            out.results.append(TokenSendResult(token=token, success=True))
            continue
        exc = resp.exception
        # 영구 무효 토큰 — 앱 삭제/토큰 만료(Unregistered), malformed(InvalidArgument).
        is_dead = isinstance(
            exc, (messaging.UnregisteredError, fb_exc.InvalidArgumentError),
        )
        out.results.append(TokenSendResult(
            token=token, success=False, is_dead=is_dead, error=str(exc),
        ))
    return out
