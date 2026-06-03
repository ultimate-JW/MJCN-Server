"""CurrentCourse.offering_id 운영 backfill (#230).

PR #227 머지로 offering_id 컬럼이 추가됐으나 마이그(AddField)가 기존 row를
모두 NULL로 채움. 이 명령은 기존 NULL row들을 (course_code, day_of_week,
start_time) 매칭으로 CourseOffering PK를 찾아 채운다.

매칭 키:
  Course.course_code = CurrentCourse.course_code
  CourseSchedule.day_of_week = CurrentCourse.day_of_week
  CourseSchedule.start_time = CurrentCourse.start_time
여러 분반 매칭 시 가장 최근 학기 (year DESC, semester DESC) 우선.

옵션:
  --dry-run : 매칭 결과만 출력, DB 변경 없음
  멱등      : 이미 채워진 row는 건드리지 않음
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import CurrentCourse
from courses.models import CourseOffering


class Command(BaseCommand):
    help = 'CurrentCourse.offering_id NULL row를 카탈로그 매칭으로 backfill (#230).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='매칭 결과만 출력, DB 변경 없음',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # 멱등 — 이미 offering_id가 채워진 row는 제외
        null_rows = list(CurrentCourse.objects.filter(offering_id__isnull=True))
        total = len(null_rows)
        if total == 0:
            self.stdout.write(self.style.SUCCESS('NULL row 없음 — 완료.'))
            return

        self.stdout.write(f'NULL row {total}건 backfill 시작 (dry-run={dry_run})…')

        matched = 0
        unmatched = 0
        for cc in null_rows:
            offering = self._find_offering(cc)
            if offering is None:
                unmatched += 1
                self.stdout.write(self.style.WARNING(
                    f'  [SKIP] cc.id={cc.id} '
                    f'course_code={cc.course_code} {cc.day_of_week} {cc.start_time} '
                    f'— 매칭되는 분반 없음'
                ))
                continue
            self.stdout.write(
                f'  [MATCH] cc.id={cc.id} → offering_id={offering.id} '
                f'({offering.year}-{offering.semester} {offering.section_no})'
            )
            if not dry_run:
                cc.offering_id = offering.id
                cc.save(update_fields=['offering_id'])
            matched += 1

        # 결과 요약
        suffix = '' if not dry_run else ' (dry-run — DB 변경 안 됨)'
        self.stdout.write(self.style.SUCCESS(
            f'\n총 {total}건 / 매칭 {matched}건 / 미매칭 {unmatched}건{suffix}'
        ))

    def _find_offering(self, cc: CurrentCourse):
        """매칭되는 CourseOffering 1건 반환 (가장 최근 학기 우선)."""
        return (
            CourseOffering.objects
            .filter(
                course__course_code=cc.course_code,
                schedules__day_of_week=cc.day_of_week,
                schedules__start_time=cc.start_time,
            )
            .order_by('-year', '-semester')
            .distinct()
            .first()
        )
