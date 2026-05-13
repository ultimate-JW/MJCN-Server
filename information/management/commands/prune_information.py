"""정보 데이터 보관 정책 cron (spec 5.5 — 위비티 운영팀 정책 반영).

매일 06:45 KST 실행해서 보관 기간이 지난 정보 레코드를 삭제한다.

기본 정책:
- source='wevity' 인 항목 중 end_date < (today - 365일) 인 것은 삭제
- end_date가 NULL인 항목은 보존 (상시 모집 등)
- --days 옵션으로 보관 기간 조정 가능
- --source 옵션으로 특정 출처만 대상 (미지정 시 wevity)
- --dry-run 으로 삭제 없이 영향 행만 출력

사용 예:
    python manage.py prune_information                  # wevity, 365일
    python manage.py prune_information --days 730       # 2년 보관
    python manage.py prune_information --source wevity --dry-run
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from information.models import Information


class Command(BaseCommand):
    help = '보관 기간(기본 365일) 지난 정보 레코드 삭제 (spec 5.5 위비티 정책)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=365,
            help='보관 기간(일). 기본 365.',
        )
        parser.add_argument(
            '--source',
            default='wevity',
            help='대상 출처 식별자 (Information.source 컬럼 값). 기본 wevity.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='실제 삭제 없이 영향 행만 출력.',
        )

    def handle(self, *args, **options):
        days = options['days']
        source = options['source']
        dry_run = options['dry_run']

        if days < 0:
            self.stderr.write(self.style.ERROR('--days는 0 이상이어야 함.'))
            return

        cutoff = timezone.localdate() - timedelta(days=days)

        # source 컬럼(0003 마이그레이션에서 도입) 기준 필터.
        qs = Information.objects.filter(
            source=source,
            end_date__isnull=False,
            end_date__lt=cutoff,
        )

        count = qs.count()

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'[dry-run] source={source} cutoff={cutoff} '
                f'삭제 예정: {count}건'
            ))
            for info in qs[:20]:
                self.stdout.write(
                    f'  - id={info.id} end_date={info.end_date} title={info.title[:50]}'
                )
            if count > 20:
                self.stdout.write(f'  ... 외 {count - 20}건')
            return

        deleted, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'완료 - source={source} cutoff={cutoff} 삭제={deleted}건'
        ))
