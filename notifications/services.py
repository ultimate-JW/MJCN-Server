"""알림 생성 헬퍼 (spec 6.9 정책).

다른 앱(notices, information, courses 등)에서 이벤트 발생 시
이 모듈의 create_notification(user, ...) 호출.
사용자의 notification_enabled 와 카테고리별 토글 체크해 INSERT 결정.
"""
from __future__ import annotations

from typing import Optional

from .models import Notification


# notification_type → User의 카테고리 토글 필드명 매핑
_TYPE_TO_USER_FLAG = {
    Notification.TYPE_NOTICE: 'notification_notice',
    Notification.TYPE_INFORMATION: 'notification_information',
    Notification.TYPE_CHAT: 'notification_chat',
    # course / system 은 별도 토글 없음 — 전체 토글 only
}


def create_notification(
    user,
    *,
    title: str,
    message: str,
    notification_type: str,
    related_id: Optional[int] = None,
) -> Optional[Notification]:
    """사용자별 알림 INSERT. 토글 OFF 시 None 반환.

    체크 순서:
      1. user.notification_enabled == False → 전체 OFF, INSERT 안 함
      2. 카테고리 토글 (예: notification_notice) == False → 해당 카테고리 OFF, INSERT 안 함
      3. 통과 → DB에 알림 INSERT 후 반환
    """
    # 전체 알림 OFF
    if not getattr(user, 'notification_enabled', True):
        return None

    # 카테고리별 토글 체크
    flag_name = _TYPE_TO_USER_FLAG.get(notification_type)
    if flag_name and not getattr(user, flag_name, True):
        return None

    return Notification.objects.create(
        user=user,
        title=title,
        message=message,
        notification_type=notification_type,
        related_id=related_id,
    )
