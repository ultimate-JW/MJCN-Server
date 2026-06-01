"""정보 키워드 태깅 명령 (spec 9.1.7).

매일 06:40 KST 운영 cron에서 실행 (정보 크롤링 06:00 직후).
tags가 비어 있는 Information을 메타데이터(title+categories+organizer) 기반으로
태깅한다. 위비티 상세 본문은 크롤링하지 않는다.

사용 예:
    python manage.py tag_information
    python manage.py tag_information --limit 50
    python manage.py tag_information --ids 1 5 12
    python manage.py tag_information --reprocess
"""
from django.core.management.base import BaseCommand

from information.tagging import get_tagging_targets, tag_information


class Command(BaseCommand):
    help = '정보(Information) AI 키워드 태깅 (spec 9.1.7)'

    def add_arguments(self, parser):
        parser.add_argument('--ids', nargs='+', type=int, default=None,
                            help='특정 Information ID만 처리.')
        parser.add_argument('--limit', type=int, default=None,
                            help='처리 건수 제한.')
        parser.add_argument('--reprocess', action='store_true',
                            help='이미 tags가 있어도 강제 재태깅.')

    def handle(self, *args, **options):
        reprocess = options.get('reprocess', False)
        items = get_tagging_targets(
            ids=options.get('ids'),
            limit=options.get('limit'),
            reprocess=reprocess,
        )
        total = items.count()
        if total == 0:
            self.stdout.write(self.style.WARNING('태깅 대상 없음.'))
            return

        self.stdout.write(f'태깅 대상: {total}건 (reprocess={reprocess})')
        result = tag_information(items, force=reprocess)
        self.stdout.write(self.style.SUCCESS(
            f'완료 - success={result.success} '
            f'skipped={result.skipped} failed={result.failed} '
            f'(total={result.total})'
        ))
