"""공지 신규 생성 시 매칭 사용자에게 알림 fanout (spec 6.9).

크롤러가 새 Notice를 INSERT한 직후 호출. 사용자의 관심사 키워드와
Notice.tags의 교집합 점수가 1 이상인 사용자에게 인앱 알림을 생성한다.

LLM 호출이나 외부 HTTP는 하지 않는다 — 단순 DB INSERT 반복.
실제 FCM 푸시 송신은 별도 PR (spec 6.9 Out of Scope).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from common.matching import extract_user_keywords, score_match
from notifications.models import Notification
from notifications.services import create_notification

logger = logging.getLogger(__name__)

User = get_user_model()

# 백필 가드 — published_at이 이 일수보다 오래된 공지는 fanout 안 함.
# 초기 백필이나 --max-pages 큰 값으로 과거 공지 대량 수집 시 알림 폭주 방지.
NOTICE_FANOUT_RECENCY_DAYS = 7

_FANOUT_MESSAGE = '관심사 기반 새 공지가 등록되었습니다.'


def fanout_new_notice(notice) -> int:
    """신규 Notice 1건에 대해 매칭 사용자에게 알림 생성.

    - 백필 가드: published_at이 7일 이전이면 skip
    - 태그가 없으면 매칭 불가 → skip
    - 매칭 점수 >= 1 + 활성 사용자 + 알림 토글 ON 사용자에게만 알림
    - 호출자(crawler.save)는 예외를 격리하므로 여기서는 예외 raise 가능

    Returns:
        실제 생성된 Notification 개수
    """
    if not notice.published_at:
        return 0
    threshold = timezone.now() - timedelta(days=NOTICE_FANOUT_RECENCY_DAYS)
    if notice.published_at < threshold:
        return 0

    tags = notice.tags or []
    if not tags:
        return 0

    # DB에서 미리 토글 OFF 사용자를 제외 → 매칭 계산량 절감.
    # (create_notification도 토글 체크하지만 거기 도달 전에 거르는 게 빠름)
    users = User.objects.filter(
        is_active=True,
        notification_enabled=True,
        notification_notice=True,
    ).prefetch_related('interests')

    created = 0
    for user in users:
        user_keywords = extract_user_keywords(user)
        if score_match(user_keywords, tags) < 1:
            continue
        notif = create_notification(
            user,
            title=notice.title[:200],
            message=_FANOUT_MESSAGE,
            notification_type=Notification.TYPE_NOTICE,
            related_id=notice.id,
        )
        if notif is not None:
            created += 1
    return created
