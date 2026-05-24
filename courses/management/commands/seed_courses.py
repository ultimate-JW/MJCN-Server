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


# 졸업요건 (컴퓨터공학전공 / 2024학번 / 비인증 트랙)
# 출처: graduation_requirements.md §2 (일반과정 비인증 2024학번) + §4.3 핵심교양 4영역 + §5.1 전공필수 8과목=24
# 총 134학점 = 전공 70 + 교양 54 + 자유선택 10
# 교양 54 = 공통17 + 핵심12 + 학문기초15 + 일반10
# 핵심교양 12 = 영역별 3학점 × 4영역 (역사·철학/사회·공동체/문화·예술/과학기술·정보)
SAMPLE_GRADUATION_REQUIREMENTS = [
    # 전공 (70 = 전필 24 + 전선 46)
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '전공필수', 'liberal_subtype': None, 'core_area': None,
     'required_credits': 24, 'total_required': 134},
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '전공선택', 'liberal_subtype': None, 'core_area': None,
     'required_credits': 46, 'total_required': 134},
    # 교양 4종 (54) — 학칙 7분류로 펼침 (#47 Phase 3). liberal_subtype은 category와 동기화(호환용).
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '공통교양', 'liberal_subtype': '공통교양', 'core_area': None,
     'required_credits': 17, 'total_required': 134},
    # 핵심교양 — 영역별 1과목(3학점) 4row로 분해. 영역당 부족 시 그 영역 과목에 가산.
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '핵심교양', 'liberal_subtype': '핵심교양', 'core_area': '역사와 철학',
     'required_credits': 3, 'total_required': 134},
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '핵심교양', 'liberal_subtype': '핵심교양', 'core_area': '사회와 공동체',
     'required_credits': 3, 'total_required': 134},
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '핵심교양', 'liberal_subtype': '핵심교양', 'core_area': '문화와 예술',
     'required_credits': 3, 'total_required': 134},
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '핵심교양', 'liberal_subtype': '핵심교양', 'core_area': '과학기술과 정보',
     'required_credits': 3, 'total_required': 134},
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '학문기초교양', 'liberal_subtype': '학문기초교양', 'core_area': None,
     'required_credits': 15, 'total_required': 134},
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '일반교양', 'liberal_subtype': '일반교양', 'core_area': None,
     'required_credits': 10, 'total_required': 134},
    # 자유선택 (10)
    {'department': '컴퓨터공학전공', 'admission_year': 2024,
     'category': '자유선택', 'liberal_subtype': None, 'core_area': None,
     'required_credits': 10, 'total_required': 134},
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
        # 4종 분해 전(0006 이전) row 잔재 정리 — (department, admission_year)+category만 같고
        # liberal_subtype=null인 옛 row가 unique 제약 새 조합과 충돌하지 않도록 같은 학번 row 다 지우고 다시 박는다.
        GraduationRequirement.objects.filter(
            department='컴퓨터공학전공', admission_year=2024,
        ).delete()
        for data in SAMPLE_GRADUATION_REQUIREMENTS:
            GraduationRequirement.objects.create(**data)
        self.stdout.write(f'  GraduationRequirement: {len(SAMPLE_GRADUATION_REQUIREMENTS)}개')

    def _seed_academic_calendar(self):
        for data in SAMPLE_ACADEMIC_CALENDAR:
            AcademicCalendar.objects.update_or_create(
                year=data['year'], semester=data['semester'],
                defaults={k: v for k, v in data.items() if k not in ('year', 'semester')},
            )
        self.stdout.write(f'  AcademicCalendar: {len(SAMPLE_ACADEMIC_CALENDAR)}개')
