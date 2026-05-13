from django.db import models


class Information(models.Model):
    """학생이 활용할 수 있는 외부/내부 기회 정보 (공모전/대외활동/지원사업/교육·강의/부트캠프).

    spec 4.5 / 5.5 참조.

    출처 식별:
    - source: 'wevity', 'mju', 'linkpedia' 등 출처 식별자
    - source_id: 출처 내 고유 ID (예: 위비티 ix=NNNNN의 NNNNN)
    - (source, source_id) unique → 같은 공모전이 여러 카테고리에 노출돼도 1행만 저장
    """
    title = models.CharField(max_length=300)
    organizer = models.CharField(max_length=200, blank=True, default='')
    description = models.TextField(blank=True, default='')
    url = models.URLField(max_length=500)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    categories = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # 출처 식별 (예: source='wevity', source_id='106194')
    source = models.CharField(max_length=20, blank=True, default='')
    source_id = models.CharField(max_length=50, blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'source_id'],
                name='unique_information_source',
            ),
        ]
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['end_date']),
            models.Index(fields=['is_active']),
            models.Index(fields=['source', 'source_id']),
        ]

    def __str__(self):
        return self.title
