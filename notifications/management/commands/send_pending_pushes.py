"""미전송 알림을 FCM 디바이스로 푸시 (spec 6.9 / 9.3).

매일 06:35 KST 운영 cron에서 crawl(06:00)+fanout 완료 직후 실행.
Firebase 자격증명(FIREBASE_CREDENTIALS_PATH) 미설정 시 graceful no-op.

    python manage.py send_pending_pushes
    python manage.py send_pending_pushes --dry-run
"""
from django.core.management.base import BaseCommand

from notifications.push import send_pending_pushes


class Command(BaseCommand):
    help = '미전송 Notification을 FCM 푸시로 송신하고 is_pushed 마킹'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='송신 대상만 집계하고 실제 전송·마킹은 하지 않음',
        )

    def handle(self, *args, **options):
        result = send_pending_pushes(dry_run=options['dry_run'])
        self.stdout.write(self.style.SUCCESS(
            f'완료 - total={result.total} pushed={result.pushed} '
            f'deferred={result.deferred} no_device={result.skipped_no_device} '
            f'stale={result.skipped_stale} dead_tokens={result.dead_tokens}'
        ))
