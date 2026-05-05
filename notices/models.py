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


class NoticeAIResult(models.Model):
    """공지사항 AI 처리 결과 (spec 9.1.1).

    Notice 1:1로 매칭. 3단계 파이프라인의 각 결과와 처리 상태를 보관.
    Notice 모델과 분리해 재처리·실패 추적·LLM 모델 버전 관리를 단순화.
    """

    NOTICE_TYPE_CHOICES = [
        ('정보형', '정보형'),
        ('행동형', '행동형'),
    ]

    STATUS_CHOICES = [
        ('pending', '대기'),
        ('processing', '처리중'),
        ('success', '성공'),
        ('failed', '실패'),
    ]

    STAGE_CHOICES = [
        ('classify', '유형 분류'),
        ('summarize', '요약'),
        ('build_cards', '카드 구조화'),
    ]

    notice = models.OneToOneField(
        Notice, on_delete=models.CASCADE, related_name='ai_result'
    )

    # Stage 1: 분류
    notice_type = models.CharField(
        max_length=10, choices=NOTICE_TYPE_CHOICES, blank=True, default=''
    )

    # Stage 2: 요약 (spec 9.1.3 - 100자 이내)
    summary = models.CharField(max_length=200, blank=True, default='')

    # Stage 3: 카드 구조화 (spec 9.1.4 / 9.1.5)
    # [{"title": "📌 등록 기간", "items": ["...", "..."]}, ...]
    cards = models.JSONField(default=list, blank=True)

    # 처리 상태 추적
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending'
    )
    last_stage = models.CharField(
        max_length=20, choices=STAGE_CHOICES, blank=True, default=''
    )
    error_message = models.TextField(blank=True, default='')
    retry_count = models.IntegerField(default=0)

    # 멱등성 / 재처리 트리거
    content_hash = models.CharField(max_length=64, blank=True, default='')
    model_name = models.CharField(max_length=50, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notices_ai_result'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['-updated_at']),
        ]

    def __str__(self):
        return f"AI[{self.status}] {self.notice_id}"
