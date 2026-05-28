"""만료된 PendingSignup row 청소 명령. 매일 06:50 KST 운영 cron에서 실행 (spec 5.1.1).

PendingSignup은 이메일 가입 임시 보관 — 인증 코드 검증 통과 시 User 생성 + 자동 삭제.
인증 미완료로 끝난 row(사용자가 가입 시도 후 인증 안 함)는 자동 삭제 안 되므로
시간 지나며 dead row 누적. 이 명령이 주기적으로 청소.

삭제 기준: `updated_at < now() - hours` — auto_now 필드라 resend 활동 시 자동 갱신.
활동 없는 row만 청소되고 사용자가 resend로 복구한 row는 살아남음.

사용 예:
    python manage.py prune_pending_signups              # 기본 24시간
    python manage.py prune_pending_signups --hours 48   # 임계값 조정
    python manage.py prune_pending_signups --dry-run    # 대상 수만 출력
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import PendingSignup


class Command(BaseCommand):
    help = '활동 없는 PendingSignup row 청소 (updated_at 기준).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours', type=int, default=24,
            help='이 시간 이상 updated_at이 지난 row 삭제 (기본 24)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='실제 삭제 없이 대상 수만 출력',
        )

    def handle(self, *args, hours, dry_run, **opts):
        threshold = timezone.now() - timedelta(hours=hours)
        qs = PendingSignup.objects.filter(updated_at__lt=threshold)
        count = qs.count()

        if dry_run:
            self.stdout.write(
                f'dry-run - target={count} threshold_hours={hours}'
            )
            return

        deleted, _ = qs.delete()
        self.stdout.write(
            f'완료 - deleted={deleted} threshold_hours={hours}'
        )
