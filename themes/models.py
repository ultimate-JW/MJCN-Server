from django.db import models


class Theme(models.Model):
    """학사 흐름 기반 가이드 테마 (spec §4.7).

    카테고리는 영문 slug로 DB/API에 저장하고, 한글 표시는 get_category_display()
    / admin에서만 노출 — i18n·URL 안전성·후속 enum 추가에 유리 (notifications.TYPE_* 컨벤션).
    """
    CATEGORY_COURSE_REGISTRATION = 'course_registration'
    CATEGORY_CAREER = 'career'
    CATEGORY_EXCHANGE = 'exchange'
    CATEGORY_GRANT = 'grant'
    CATEGORY_ACADEMIC = 'academic'
    CATEGORY_CHOICES = [
        (CATEGORY_COURSE_REGISTRATION, '수강신청'),
        (CATEGORY_CAREER, '취업진로'),
        (CATEGORY_EXCHANGE, '교환학생'),
        (CATEGORY_GRANT, '지원사업'),
        (CATEGORY_ACADEMIC, '학업관리'),
    ]

    title = models.CharField(max_length=200)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', '-created_at']
        indexes = [
            models.Index(fields=['is_active', 'order']),
            models.Index(fields=['category']),
        ]

    def __str__(self):
        return f'[{self.get_category_display()}] {self.title}'


class ThemeItem(models.Model):
    """테마 상세 항목 (spec §4.7).

    item_type: guide(설명형), checklist(체크리스트), link(외부 링크 바로가기)
    """
    ITEM_TYPE_GUIDE = 'guide'
    ITEM_TYPE_CHECKLIST = 'checklist'
    ITEM_TYPE_LINK = 'link'
    ITEM_TYPE_CHOICES = [
        (ITEM_TYPE_GUIDE, '가이드'),
        (ITEM_TYPE_CHECKLIST, '체크리스트'),
        (ITEM_TYPE_LINK, '외부 링크'),
    ]

    theme = models.ForeignKey(Theme, related_name='items', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    content = models.TextField(blank=True, default='')
    external_url = models.URLField(max_length=500, blank=True, default='')
    item_type = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        indexes = [
            models.Index(fields=['theme', 'order']),
        ]

    def __str__(self):
        return f'{self.theme.title} / {self.title}'
