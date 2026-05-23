"""미전송 알림 → FCM 디바이스 푸시 오케스트레이터 (spec 6.9 / 9.3).

매일 06:35 KST cron(`send_pending_pushes` 명령)에서 호출된다.
`is_pushed` 상태머신과 recency guard, 죽은 토큰 비활성화를 담당.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from notifications import fcm
from notifications.models import FCMDevice, Notification

logger = logging.getLogger(__name__)

# 누락된 cron 백로그가 오래된 알림을 한꺼번에 푸시하는 것을 방지하는 recency guard.
# notices/fanout.py의 NOTICE_FANOUT_RECENCY_DAYS(7일)와 같은 철학 — 푸시는 더 짧게.
PUSH_RECENCY_HOURS = 24


@dataclass
class PushRunResult:
    total: int = 0              # 송신 대상 알림 수 (recency 창 내 미전송)
    pushed: int = 0             # 송신 성공 → is_pushed=True
    deferred: int = 0           # transient 실패 → is_pushed=False 유지 (재시도)
    skipped_no_device: int = 0  # 활성 디바이스 없어 푸시 없이 종결
    skipped_stale: int = 0      # recency guard로 푸시 없이 종결한 노후 알림
    dead_tokens: int = 0        # 비활성화한 FCMDevice 수


def _recency_threshold():
    return timezone.now() - timedelta(hours=PUSH_RECENCY_HOURS)


def expire_stale_notifications() -> int:
    """recency 창보다 오래된 미전송 알림을 푸시 없이 is_pushed=True로 종결.

    누락된 cron 복구 후에도 노후 알림이 영구 재조회되지 않도록 한다.
    인앱 Notification row는 그대로 남아 사용자가 앱에서 확인 가능.
    """
    return Notification.objects.filter(
        is_pushed=False, created_at__lt=_recency_threshold(),
    ).update(is_pushed=True)


def get_pending_notifications():
    """is_pushed=False & 최근 PUSH_RECENCY_HOURS 이내 알림 (오래된 순)."""
    return (Notification.objects
            .filter(is_pushed=False, created_at__gte=_recency_threshold())
            .select_related('user')
            .order_by('created_at'))


def send_pending_pushes(*, dry_run: bool = False) -> PushRunResult:
    """미전송 알림을 FCM으로 송신. Firebase 미설정 시 graceful no-op."""
    result = PushRunResult()

    # 1) recency guard — 노후 미전송 알림 종결 (푸시 없이 마킹)
    if not dry_run:
        result.skipped_stale = expire_stale_notifications()

    pending = list(get_pending_notifications())
    result.total = len(pending)
    if result.total == 0:
        return result

    # 2) Firebase 미설정 → no-op (로컬·테스트). 알림은 미전송 상태로 둔다.
    if not fcm.is_configured():
        logger.info('Firebase 미설정 — 푸시 %d건 skip (no-op).', result.total)
        return result

    if dry_run:
        return result

    # 3) 활성 디바이스 토큰을 user_id별로 prefetch (N+1 회피)
    user_ids = {n.user_id for n in pending}
    tokens_by_user: dict[int, list[str]] = {}
    for device in FCMDevice.objects.filter(
        user_id__in=user_ids, is_active=True,
    ):
        tokens_by_user.setdefault(device.user_id, []).append(
            device.registration_token,
        )

    dead_tokens: set[str] = set()

    for notif in pending:
        tokens = tokens_by_user.get(notif.user_id, [])
        if not tokens:
            # 활성 디바이스 없음 → 영구 재시도 방지 위해 종결
            notif.is_pushed = True
            notif.save(update_fields=['is_pushed'])
            result.skipped_no_device += 1
            continue
        try:
            delivered = _send_one(notif, tokens, dead_tokens)
        except Exception:
            # 알림 1건 실패가 루프 전체를 막지 않도록 격리
            logger.exception('알림 %d 푸시 처리 실패', notif.id)
            result.deferred += 1
            continue
        if delivered:
            result.pushed += 1
        else:
            result.deferred += 1

    # 4) 죽은 토큰 일괄 비활성화
    if dead_tokens:
        result.dead_tokens = FCMDevice.objects.filter(
            registration_token__in=dead_tokens, is_active=True,
        ).update(is_active=False)

    return result


def _send_one(notif: Notification, tokens: list[str],
              dead_tokens: set[str]) -> bool:
    """알림 1건을 사용자 토큰들로 송신. is_pushed 확정 가능하면 True.

    죽은 토큰은 dead_tokens 집합에 누적 (호출자가 일괄 비활성화).
    transient 실패가 하나라도 있으면 is_pushed를 그대로 둬 다음 cron 재시도.
    """
    data = {
        'notification_type': notif.notification_type,
        'related_id': str(notif.related_id or ''),
        'notification_id': str(notif.id),
    }
    transient_failure = False
    for i in range(0, len(tokens), fcm.FCM_MULTICAST_LIMIT):
        chunk = tokens[i:i + fcm.FCM_MULTICAST_LIMIT]
        try:
            mc = fcm.send_to_tokens(
                chunk, title=notif.title, body=notif.message, data=data,
            )
        except fcm.PushClientError as e:
            # 인프라 수준 실패 → transient, 다음 cron 재시도
            logger.warning('알림 %d 푸시 호출 실패: %s', notif.id, e)
            transient_failure = True
            continue
        dead_tokens.update(mc.dead_tokens)
        if mc.has_transient_failure:
            transient_failure = True

    if transient_failure:
        return False  # is_pushed 그대로 False — 다음 cron 재시도
    notif.is_pushed = True
    notif.save(update_fields=['is_pushed'])
    return True
