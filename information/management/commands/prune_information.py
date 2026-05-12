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
            help='대상 출처 식별자. 현재 모델은 별도 source 컬럼이 없으므로 '
                 'URL 도메인 기준으로 필터링 (예: wevity → wevity.com 포함).',
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

        # Information 모델에 source 컬럼이 없으므로 URL 도메인 매칭으로 대체.
        # 위비티는 url에 'wevity.com'이 포함되어 있음.
        domain_keyword = self._source_to_domain_keyword(source)

        qs = Information.objects.filter(
            end_date__isnull=False,
            end_date__lt=cutoff,
            url__icontains=domain_keyword,
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

    @staticmethod
    def _source_to_domain_keyword(source: str) -> str:
        """source 식별자 → URL 도메인 키워드 매핑.

        Information 모델에 source 컬럼이 없는 한 URL 기반으로 출처를 식별한다.
        """
        mapping = {
            'wevity': 'wevity.com',
        }
        return mapping.get(source, source)
