from django.db import models


class Information(models.Model):
    """학생이 활용할 수 있는 외부/내부 기회 정보 (공모전/대외활동/지원사업/교육·강의/부트캠프).

    spec 4.5 / 5.5 참조.
    """
    title = models.CharField(max_length=300)
    organizer = models.CharField(max_length=200, blank=True, default='')
    description = models.TextField(blank=True, default='')
    url = models.URLField(max_length=500, unique=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    categories = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['end_date']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return self.title
