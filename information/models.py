from django.db import models


class Contest(models.Model):
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
        db_table = 'information_contest'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['end_date']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return self.title
