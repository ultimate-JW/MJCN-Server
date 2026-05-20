"""courses 앱 졸업요건·학사일정 시딩 명령.

사용 예:
    python manage.py seed_courses

여러 번 실행해도 안전 (unique_together 기준 update_or_create).

과목/분반/시간표는 강의시간표 엑셀 import(import_courses_from_xlsx, #36)로 들어온다.
기존 더미 과목(COMP101 등)·더미 시간표·더미 선수과목은 실제 데이터 도입에 따라 제거됨.
실제 선수과목 관계는 강의시간표 엑셀에 없어 별도 import 경로로 추가 예정 (#36 후속).
"""

from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from courses.models import AcademicCalendar, GraduationRequirement


# 졸업요건 (컴퓨터공학전공 / 2024년 입학)
SAMPLE_GRADUATION_REQUIREMENTS = [
    {
        'department': '컴퓨터공학전공', 'admission_year': 2024,
        'category': '전공필수', 'required_credits': 30, 'total_required': 130,
    },
    {
        'department': '컴퓨터공학전공', 'admission_year': 2024,
        'category': '전공선택', 'required_credits': 30, 'total_required': 130,
    },
    {
        'department': '컴퓨터공학전공', 'admission_year': 2024,
        'category': '교양필수', 'required_credits': 18, 'total_required': 130,
    },
    {
        'department': '컴퓨터공학전공', 'admission_year': 2024,
        'category': '교양선택', 'required_credits': 12, 'total_required': 130,
    },
]


# 학사일정 (2026년)
SAMPLE_ACADEMIC_CALENDAR = [
    {
        'year': 2026, 'semester': 1,
        'pre_registration_start': date(2026, 2, 10), 'pre_registration_end': date(2026, 2, 12),
        'registration_start': date(2026, 2, 17), 'registration_end': date(2026, 2, 19),
        'adjustment_start': date(2026, 3, 2), 'adjustment_end': date(2026, 3, 6),
        'semester_start': date(2026, 3, 2), 'semester_end': date(2026, 6, 19),
    },
    {
        'year': 2026, 'semester': 2,
        'pre_registration_start': date(2026, 8, 10), 'pre_registration_end': date(2026, 8, 12),
        'registration_start': date(2026, 8, 17), 'registration_end': date(2026, 8, 19),
        'adjustment_start': date(2026, 9, 1), 'adjustment_end': date(2026, 9, 5),
        'semester_start': date(2026, 9, 1), 'semester_end': date(2026, 12, 18),
    },
]


class Command(BaseCommand):
    help = 'courses 앱 졸업요건·학사일정 시딩 (과목 데이터는 import_courses_from_xlsx 사용)'

    @transaction.atomic
    def handle(self, *args, **options):
        self._seed_graduation_requirements()
        self._seed_academic_calendar()
        self.stdout.write(self.style.SUCCESS('시딩 완료'))

    def _seed_graduation_requirements(self):
        for data in SAMPLE_GRADUATION_REQUIREMENTS:
            GraduationRequirement.objects.update_or_create(
                department=data['department'],
                admission_year=data['admission_year'],
                category=data['category'],
                defaults={
                    'required_credits': data['required_credits'],
                    'total_required': data['total_required'],
                },
            )
        self.stdout.write(f'  GraduationRequirement: {len(SAMPLE_GRADUATION_REQUIREMENTS)}개')

    def _seed_academic_calendar(self):
        for data in SAMPLE_ACADEMIC_CALENDAR:
            AcademicCalendar.objects.update_or_create(
                year=data['year'], semester=data['semester'],
                defaults={k: v for k, v in data.items() if k not in ('year', 'semester')},
            )
        self.stdout.write(f'  AcademicCalendar: {len(SAMPLE_ACADEMIC_CALENDAR)}개')
