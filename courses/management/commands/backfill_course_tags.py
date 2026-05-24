"""Course.tags 자동 채움 명령 (#48, #36 후속).

사용 예:
    python manage.py backfill_course_tags
    python manage.py backfill_course_tags --overwrite   # 기존 tags 있는 Course도 덮어쓰기
    python manage.py backfill_course_tags --dry-run     # DB 변경 없이 결과만 출력

룰: courses/tag_rules.py 의 MAJOR_DEFAULT_TAGS + NAME_KEYWORD_TAGS.
기본은 tags가 비어있는 Course만 채움 — 수동 보강한 태그 보호.

결과: 매칭된 Course 수 + 카테고리별 분포 + 미매칭 Course list.
"""

from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from courses.models import Course
from courses.tag_rules import infer_tags


class Command(BaseCommand):
    help = 'Course.tags 룰 기반 자동 채움'

    def add_arguments(self, parser):
        parser.add_argument(
            '--overwrite', action='store_true',
            help='기존 tags 있는 Course도 덮어쓰기 (수동 보강분 손실 위험)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='DB 변경 없이 결과만 출력',
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        overwrite = opts['overwrite']
        dry_run = opts['dry_run']

        qs = Course.objects.all() if overwrite else Course.objects.filter(tags=[])
        target_count = qs.count()
        self.stdout.write(f'대상 Course: {target_count}개 (overwrite={overwrite})')

        updated = 0
        unmatched = []     # (course_code, name) — 룰 매칭 실패 (룰 보강 시그널)
        tag_counter = Counter()

        for c in qs:
            tags = sorted(infer_tags(c))
            if not tags:
                unmatched.append((c.course_code, c.name))
                continue
            tag_counter.update(tags)
            if not dry_run:
                c.tags = tags
                c.save(update_fields=['tags'])
            updated += 1

        self.stdout.write(f'채움 성공: {updated} / 미매칭: {len(unmatched)}')
        self.stdout.write('카테고리별 적용 수 (한 Course가 여러 태그 갖는 경우 중복 카운트):')
        for tag, n in tag_counter.most_common():
            self.stdout.write(f'  {tag}: {n}')

        if unmatched:
            self.stdout.write('')
            self.stdout.write(f'미매칭 Course ({len(unmatched)}개) — tag_rules.py 보강 시그널:')
            for code, name in unmatched[:30]:
                self.stdout.write(f'  [{code}] {name}')
            if len(unmatched) > 30:
                self.stdout.write(f'  ... 외 {len(unmatched) - 30}개')

        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] DB 변경 안 됨'))
            # transaction.atomic 롤백 위해 raise
            raise SystemExit(0)
