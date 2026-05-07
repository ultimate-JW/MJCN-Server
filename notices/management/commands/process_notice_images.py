"""VLM 전처리 명령 — 이미지로만 구성된 공지의 텍스트를 추출 (spec 9.1.6).

매일 03:15 KST 운영 cron에서 실행 (텍스트 파이프라인 03:30 직전).
실행 후 process_notices_ai가 content_hash 변경을 감지해 자동 재처리한다.

사용 예:
    python manage.py process_notice_images
    python manage.py process_notice_images --source academic --limit 10
    python manage.py process_notice_images --ids 1 5 12
    python manage.py process_notice_images --reprocess
"""
from django.core.management.base import BaseCommand

from notices.ai.vlm import get_vlm_targets, process_notice_images


class Command(BaseCommand):
    help = '공지 이미지 → 텍스트 추출 (VLM 전처리, spec 9.1.6)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            nargs='+',
            default=None,
            help='특정 source(s)만 처리. 예: academic general',
        )
        parser.add_argument(
            '--ids',
            nargs='+',
            type=int,
            default=None,
            help='특정 Notice ID만 처리.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='처리 건수 제한.',
        )
        parser.add_argument(
            '--reprocess',
            action='store_true',
            help='이미 extracted_content가 있어도 강제 재추출.',
        )

    def handle(self, *args, **options):
        notices = get_vlm_targets(
            sources=options.get('source'),
            ids=options.get('ids'),
            limit=options.get('limit'),
            reprocess=options.get('reprocess', False),
        )
        total = notices.count()

        if total == 0:
            self.stdout.write(self.style.WARNING('처리 대상 없음.'))
            return

        self.stdout.write(f'처리 대상: {total}건 (reprocess={options.get("reprocess", False)})')

        result = process_notice_images(notices, force=options.get('reprocess', False))

        self.stdout.write(self.style.SUCCESS(
            f'완료 - success={result.success} '
            f'skipped={result.skipped} failed={result.failed} '
            f'(total={result.total})'
        ))
