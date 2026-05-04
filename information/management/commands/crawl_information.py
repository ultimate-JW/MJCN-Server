"""정보(공모전) 크롤링 명령. 매일 03:00 KST 운영 cron에서 실행 (spec 8.2).

사용 예:
    python manage.py crawl_information
    python manage.py crawl_information --source mju_contest
"""
from django.core.management.base import BaseCommand

from information.crawlers.registry import get_crawlers


class Command(BaseCommand):
    help = '명지대 공모전 게시판 크롤링 (학교 자체 게시판 한정)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            nargs='+',
            default=None,
            help='특정 source만 실행. 미지정 시 전체.',
        )

    def handle(self, *args, **options):
        crawler_classes = get_crawlers(options.get('source'))

        if not crawler_classes:
            self.stdout.write(self.style.WARNING(
                '실행할 크롤러가 없음. information/crawlers/registry.py에 등록 필요.'
            ))
            return

        total_created = total_updated = total_failed = 0

        for crawler_cls in crawler_classes:
            crawler = crawler_cls()
            try:
                result = crawler.run()
                total_created += result.created
                total_updated += result.updated
                total_failed += result.failed
                self.stdout.write(self.style.SUCCESS(
                    f'[{result.source}] created={result.created} '
                    f'updated={result.updated} failed={result.failed}'
                ))
            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    f'[{crawler_cls.SOURCE}] 실패: {e}'
                ))

        self.stdout.write(self.style.SUCCESS(
            f'전체 완료 - created={total_created} '
            f'updated={total_updated} failed={total_failed}'
        ))
