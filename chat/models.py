from django.conf import settings
from django.db import models

# 단일 출처 — 분류기 프롬프트와 공유 (#220 #10). 기존 import 경로(chat.models.CHAT_CATEGORIES)
# 유지를 위해 재노출한다.
from .categories import CHAT_CATEGORIES


class ChatRoom(models.Model):
    """AI 채팅방 (spec 4.2 / 5.2).

    title·category는 PR 2에서 첫 메시지 도착 시 AI가 자동으로 채운다.
    PR 1 범위에서는 빈 채팅방 생성만 허용.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chatrooms',
        verbose_name='사용자',
    )
    title = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='채팅방 제목',
    )
    category = models.CharField(
        max_length=20,
        choices=CHAT_CATEGORIES,
        default='기타',
        verbose_name='카테고리',
    )
    last_message_preview = models.CharField(
        max_length=200,
        blank=True,
        default='',
        verbose_name='마지막 메시지 미리보기',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성 시각')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='갱신 시각')

    class Meta:
        ordering = ['-updated_at']
        verbose_name = '채팅방'
        verbose_name_plural = '채팅방'

    def __str__(self):
        return f'{self.user.email} - {self.title or "(제목 미정)"}'


class ChatMessage(models.Model):
    """채팅 메시지 (spec 4.2)."""

    ROLE_USER = 'user'
    ROLE_ASSISTANT = 'assistant'
    ROLE_CHOICES = [
        (ROLE_USER, 'user'),
        (ROLE_ASSISTANT, 'assistant'),
    ]

    room = models.ForeignKey(
        ChatRoom,
        on_delete=models.CASCADE,
        related_name='messages',
        verbose_name='채팅방',
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, verbose_name='역할')
    content = models.TextField(verbose_name='메시지 내용')
    referenced_items = models.JSONField(
        default=list,
        blank=True,
        verbose_name='참조 항목 (공지/정보 title+url snapshot)',
        help_text=(
            'AI가 search_notices / search_information tool로 가져온 카드 데이터 '
            'snapshot — [{type, title, url}] 형식. type ∈ {notice, information}. '
            'user 메시지는 빈 배열.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성 시각')

    class Meta:
        ordering = ['created_at']
        verbose_name = '채팅 메시지'
        verbose_name_plural = '채팅 메시지'

    def __str__(self):
        return f'[{self.room_id}] {self.role}: {self.content[:30]}'


class ChatAttachment(models.Model):
    """채팅 첨부파일 (spec 4.2 / 5.2).

    MVP는 MEDIA_ROOT 로컬 저장. S3 마이그레이션은 follow-up PR.
    """
    FILE_TYPE_IMAGE = 'image'
    FILE_TYPE_VIDEO = 'video'
    FILE_TYPE_DOCUMENT = 'document'
    FILE_TYPE_CHOICES = [
        (FILE_TYPE_IMAGE, 'image'),
        (FILE_TYPE_VIDEO, 'video'),
        (FILE_TYPE_DOCUMENT, 'document'),
    ]

    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='메시지',
    )
    file = models.FileField(upload_to='chat/%Y/%m/', verbose_name='업로드 파일')
    file_type = models.CharField(max_length=20, choices=FILE_TYPE_CHOICES, verbose_name='파일 종류')
    original_name = models.CharField(max_length=255, verbose_name='원본 파일명')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성 시각')

    class Meta:
        verbose_name = '채팅 첨부파일'
        verbose_name_plural = '채팅 첨부파일'

    def __str__(self):
        return self.original_name
