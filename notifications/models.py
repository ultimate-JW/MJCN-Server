"""알림 + FCM 디바이스 모델 (spec 4.6 / 6.9)."""
from django.conf import settings
from django.db import models


class Notification(models.Model):
    """사용자별 인앱 알림.

    실제 FCM 푸시 송신은 별도 PR에서 처리 (is_pushed=False 기본).
    이번 범위는 DB 저장 + 조회·읽음처리 API.
    """
    TYPE_NOTICE = 'notice'
    TYPE_INFORMATION = 'information'
    TYPE_COURSE = 'course'
    TYPE_CHAT = 'chat'
    TYPE_SYSTEM = 'system'
    TYPE_CHOICES = [
        (TYPE_NOTICE, '공지'),
        (TYPE_INFORMATION, '정보'),
        (TYPE_COURSE, '수강'),
        (TYPE_CHAT, 'AI 채팅'),
        (TYPE_SYSTEM, '시스템'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    # 관련 객체 ID (notification_type에 따라 Notice/Information/Course 등의 PK).
    # 프론트가 화면 이동 시 사용. FK가 아니라 IntegerField (cross-app 의존 회피).
    related_id = models.IntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    # FCM 푸시 전송 여부. 푸시 송신 로직은 별도 PR에서 추가됨 (기본 False).
    is_pushed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # 자주 쓸 쿼리: WHERE user=? AND is_read=False ORDER BY created_at DESC
            models.Index(fields=['user', 'is_read', '-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f'{self.user_id} - {self.title[:30]}'


class FCMDevice(models.Model):
    """FCM 푸시 알림용 디바이스 토큰.

    한 사용자가 여러 디바이스(폰/태블릿)에서 로그인할 수 있어 N:1 관계.
    `registration_token` 자체에 unique 제약 — 디바이스 양도 시 기존 매핑 해제.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='fcm_devices',
    )
    registration_token = models.TextField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'is_active']),
        ]

    def __str__(self):
        return f'{self.user_id} - {self.registration_token[:20]}...'
