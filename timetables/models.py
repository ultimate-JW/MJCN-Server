from django.conf import settings
from django.db import models


# 시간표 추천용 사용자 선호 (이슈 #97 5.3.6).
# InterestArea(전반 관심사)와 도메인 분리 — 여기는 시간표 한정 조건만.
class UserPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='timetable_preference',
    )
    # JSONField list[str] — DAY_CHOICES와 동일한 한글 코드 ('월','화','수','목','금')
    prefer_off_days = models.JSONField(default=list, blank=True)  # Soft 가산
    banned_days = models.JSONField(default=list, blank=True)      # Hard filter
    no_morning = models.BooleanField(default=False)   # Hard — start_time < 10:00 분반 제외 (1교시)
    no_evening = models.BooleanField(default=False)   # Hard — start_time >= 18:00 분반 제외
    lunch_break = models.BooleanField(default=False)  # Soft — 12~14시 연속 2 slot 공강 가산
    max_credits = models.IntegerField(default=18)     # Hard 상한 (DFS 가지치기)
    min_credits = models.IntegerField(default=15)     # Soft 하한 (미달 시 패널티)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'timetables_userpreference'

    def __str__(self):
        return f"Pref<{self.user_id}> max={self.max_credits}/min={self.min_credits}"
