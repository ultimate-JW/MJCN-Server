"""선수과목 관계 csv import (graduation_requirements.md §7).

사용 예:
    python manage.py import_prerequisites_from_csv seed_data/prerequisites/컴퓨터공학과.csv

csv 형식 (파이프 구분, utf-8):
    학과명|후수교과코드|후수교과목명|선수교과코드|선수교과목명|선·후수지정연도

매칭은 **과목명(name)** 기준. csv의 학과코드(예: JEJ02220)는 학교 옛 내부 코드라
DB Course.course_code(예: 컴공311)와 형식이 달라 코드 매칭 불가.

동일과목 매핑(graduation_requirements.md §9.예시 `C언어프로그래밍 = C언어`)은
csv 원본을 수동 정리하는 방식으로 처리한다 (별칭 dict 코드화 X).

매칭 실패 시 stdout WARN + skip — DB 미커버 후수과목은 해당 학기에 미개설
(CLAUDE.md `## 선이수 데이터 — DB 미커버 후수과목` 참고).

여러 번 실행해도 안전 (CoursePrerequisite unique_together 기준 update_or_create).
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from courses.models import Course, CoursePrerequisite


class Command(BaseCommand):
    help = '선수과목 관계 csv import'

    def add_arguments(self, parser):
        parser.add_argument('path', help='csv 경로 (파이프 구분)')

    @transaction.atomic
    def handle(self, *args, **opts):
        path = Path(opts['path'])
        if not path.exists():
            raise CommandError(f'파일 없음: {path}')

        created = 0
        updated = 0
        skipped = []   # (후수명, 선수명, 사유)

        with path.open('r', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter='|')
            header = next(reader, None)
            if not header or '후수교과목명' not in header:
                raise CommandError(f'csv 헤더 이상: {header}')

            for row in reader:
                if not row or len(row) < 5:
                    continue
                # 컬럼: 학과명 | 후수코드 | 후수명 | 선수코드 | 선수명 | 지정연도
                next_name = (row[2] or '').strip()
                prereq_name = (row[4] or '').strip()

                next_course = Course.objects.filter(name=next_name).first()
                prereq_course = Course.objects.filter(name=prereq_name).first()

                if not next_course:
                    skipped.append((next_name, prereq_name, '후수 DB 미커버'))
                    continue
                if not prereq_course:
                    skipped.append((next_name, prereq_name, '선수 DB 미커버'))
                    continue

                _, was_created = CoursePrerequisite.objects.update_or_create(
                    course=next_course, prerequisite=prereq_course,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(f'CoursePrerequisite: 신규 {created} / 기존 {updated} / skip {len(skipped)}')
        if skipped:
            self.stdout.write('  [WARN] 매칭 실패 행:')
            for n, p, reason in skipped:
                self.stdout.write(f'    - {n!r} ← {p!r} ({reason})')
