"""공지사항 AI 처리 파이프라인 실행 명령 (spec 9.1.1).

매일 03:30 KST 운영 cron에서 크롤링(03:00) 직후 실행.

기본 동작: status가 'success'가 아닌 Notice를 모두 처리.
본문 변경(content_hash 불일치) 감지는 process_notice 내부에서 처리.

사용 예:
    python manage.py process_notices_ai
    python manage.py process_notices_ai --source academic --limit 10
    python manage.py process_notices_ai --ids 1 5 12
    python manage.py process_notices_ai --reprocess          # 모든 항목 강제 재처리
"""
from django.core.management.base import BaseCommand

from notices.ai.processor import get_pending_notices, process_notices


class Command(BaseCommand):
    help = '공지사항 AI 처리 파이프라인 (분류/요약/카드 구조화)'

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
            help='success 결과도 강제로 재처리.',
        )

    def handle(self, *args, **options):
        sources = options.get('source')
        ids = options.get('ids')
        limit = options.get('limit')
        reprocess = options.get('reprocess', False)

        notices = get_pending_notices(
            sources=sources,
            ids=ids,
            limit=limit,
            reprocess=reprocess,
        )
        total = notices.count()

        if total == 0:
            self.stdout.write(self.style.WARNING('처리 대상 없음.'))
            return

        self.stdout.write(f'처리 대상: {total}건 (reprocess={reprocess})')

        result = process_notices(notices, force=reprocess)

        self.stdout.write(self.style.SUCCESS(
            f'완료 - success={result.success} '
            f'skipped={result.skipped} failed={result.failed} '
            f'(total={result.total})'
        ))
