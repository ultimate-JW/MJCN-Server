from django.db import models


class Notice(models.Model):
    SOURCE_CHOICES = [
        ('academic', '학사공지'),
        ('general', '일반공지'),
        ('event', '행사공지'),
        ('scholarship', '장학공지'),
        ('overseas', '해외공지'),
        ('student_activity', '학생활동공지'),
        ('career', '진로/취업/창업'),
        ('contest', '공모전공지'),
        ('opentalk', '오픈톡'),
    ]

    source = models.CharField(max_length=30, choices=SOURCE_CHOICES)
    title = models.CharField(max_length=300)
    content = models.TextField(blank=True, default='')
    url = models.URLField(max_length=500)
    published_at = models.DateTimeField()
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    tags = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = 'notices_notice'
        ordering = ['-published_at']
        # 동일 출처 + 동일 URL은 동일 공지로 간주 → 크롤링 재실행 시 upsert 키
        unique_together = ('source', 'url')
        indexes = [
            models.Index(fields=['source', '-published_at']),
            models.Index(fields=['-published_at']),
        ]

    def __str__(self):
        return f"[{self.get_source_display()}] {self.title}"
