"""정보 신규 생성 시 매칭 사용자에게 알림 fanout (spec 6.9).

크롤러가 새 Information을 INSERT한 직후 호출. 사용자 관심사 키워드와
Information.categories 교집합 점수가 1 이상인 사용자에게 인앱 알림 생성.

LLM 호출이나 외부 HTTP는 하지 않는다.
실제 FCM 푸시 송신은 별도 PR (spec 6.9 Out of Scope).
"""
from __future__ import annotations

import logging
from datetime import date

from django.contrib.auth import get_user_model

from common.matching import extract_user_keywords, score_match
from notifications.models import Notification
from notifications.services import create_notification

logger = logging.getLogger(__name__)

User = get_user_model()

_FANOUT_MESSAGE = '관심사 기반 새 정보가 등록되었습니다.'


def fanout_new_information(info) -> int:
    """신규 Information 1건에 대해 매칭 사용자에게 알림 생성.

    - end_date가 미경과인 경우만 fanout (마감 지난 정보는 알림 X)
    - categories가 없으면 매칭 불가 → skip
    - 매칭 점수 >= 1 + 활성 사용자 + 알림 토글 ON 사용자에게만 알림

    Returns:
        실제 생성된 Notification 개수
    """
    if info.end_date and info.end_date < date.today():
        return 0

    categories = info.categories or []
    if not categories:
        return 0

    users = User.objects.filter(
        is_active=True,
        notification_enabled=True,
        notification_information=True,
    ).prefetch_related('interests')

    created = 0
    for user in users:
        user_keywords = extract_user_keywords(user)
        if score_match(user_keywords, categories) < 1:
            continue
        notif = create_notification(
            user,
            title=info.title[:200],
            message=_FANOUT_MESSAGE,
            notification_type=Notification.TYPE_INFORMATION,
            related_id=info.id,
        )
        if notif is not None:
            created += 1
    return created
