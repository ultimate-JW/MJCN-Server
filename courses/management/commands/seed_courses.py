"""courses 앱 더미 데이터 시딩 명령.

사용 예:
    python manage.py seed_courses

여러 번 실행해도 안전 (course_code / unique_together 기준 update_or_create).
실제 학교 데이터는 추후 SAMPLE_* 리스트만 교체하면 됨.

"""

from datetime import date, time

from django.core.management.base import BaseCommand
from django.db import transaction

from courses.models import (
    AcademicCalendar,
    Course,
    CoursePrerequisite,
    CourseSchedule,
    GraduationRequirement,
)


# 샘플 과목 (컴퓨터공학전공 + 교양 일부)
SAMPLE_COURSES = [
    {
        'course_code': 'COMP101', 'name': '프로그래밍기초',
        'college': '반도체·ICT대학', 'department': '컴퓨터정보통신공학부', 'major': '컴퓨터공학전공',
        'category': '전공필수', 'credits': 3, 'year_open': 1, 'semester_open': 1,
        'professor': '이설하',
    },
    {
        'course_code': 'COMP102', 'name': '이산수학',
        'college': '반도체·ICT대학', 'department': '컴퓨터정보통신공학부', 'major': '컴퓨터공학전공',
        'category': '전공필수', 'credits': 3, 'year_open': 1, 'semester_open': 2,
        'professor': '김세훈',
    },
    {
        'course_code': 'COMP201', 'name': '자료구조',
        'college': '반도체·ICT대학', 'department': '컴퓨터정보통신공학부', 'major': '컴퓨터공학전공',
        'category': '전공필수', 'credits': 3, 'year_open': 2, 'semester_open': 1,
        'professor': '최진원',
    },
    {
        'course_code': 'COMP202', 'name': '객체지향프로그래밍',
        'college': '반도체·ICT대학', 'department': '컴퓨터정보통신공학부', 'major': '컴퓨터공학전공',
        'category': '전공필수', 'credits': 3, 'year_open': 2, 'semester_open': 1,
        'professor': '진소은',
    },
    {
        'course_code': 'COMP301', 'name': '알고리즘',
        'college': '반도체·ICT대학', 'department': '컴퓨터정보통신공학부', 'major': '컴퓨터공학전공',
        'category': '전공필수', 'credits': 3, 'year_open': 3, 'semester_open': 1,
        'professor': '신다예',
    },
    {
        'course_code': 'COMP302', 'name': '데이터베이스',
        'college': '반도체·ICT대학', 'department': '컴퓨터정보통신공학부', 'major': '컴퓨터공학전공',
        'category': '전공선택', 'credits': 3, 'year_open': 3, 'semester_open': 2,
        'professor': '김윤하',
    },
    {
        'course_code': 'GEN101', 'name': '대학영어',
        'college': '교양', 'department': None, 'major': None,
        'category': '교양필수', 'credits': 2, 'year_open': 1, 'semester_open': 1,
        'professor': '카레왕',
    },
    {
        'course_code': 'GEN102', 'name': '글쓰기',
        'college': '교양', 'department': None, 'major': None,
        'category': '교양필수', 'credits': 2, 'year_open': 1, 'semester_open': 2,
        'professor': '한로로',
    },
]


# 선수과목 관계 (course_code 기준)
SAMPLE_PREREQUISITES = [
    ('COMP201', 'COMP101'),  # 자료구조 ← 프로그래밍기초
    ('COMP202', 'COMP101'),  # 객체지향프로그래밍 ← 프로그래밍기초
    ('COMP301', 'COMP201'),  # 알고리즘 ← 자료구조
    ('COMP302', 'COMP201'),  # 데이터베이스 ← 자료구조
]


# 시간표 (course_code, day, start, end, building, room)
SAMPLE_SCHEDULES = [
    ('COMP101', '월', time(9, 0), time(10, 30), '창조관', '101'),
    ('COMP101', '수', time(9, 0), time(10, 30), '창조관', '101'),
    ('COMP102', '화', time(10, 30), time(12, 0), '창조관', '102'),
    ('COMP201', '월', time(13, 0), time(14, 30), '창조관', '201'),
    ('COMP201', '수', time(13, 0), time(14, 30), '창조관', '201'),
    ('COMP202', '화', time(13, 0), time(14, 30), '창조관', '202'),
    ('COMP301', '월', time(15, 0), time(16, 30), '창조관', '301'),
    ('COMP302', '목', time(10, 30), time(12, 0), '창조관', '302'),
    ('GEN101', '금', time(9, 0), time(10, 30), '인문관', '101'),
    ('GEN102', '금', time(11, 0), time(12, 30), '인문관', '102'),
]


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
    help = 'courses 앱 더미 데이터 시딩 (개발/테스트용)'

    @transaction.atomic
    def handle(self, *args, **options):
        course_map = self._seed_courses()
        self._seed_schedules(course_map)
        self._seed_prerequisites(course_map)
        self._seed_graduation_requirements()
        self._seed_academic_calendar()
        self.stdout.write(self.style.SUCCESS('시딩 완료'))

    def _seed_courses(self):
        course_map = {}
        for data in SAMPLE_COURSES:
            course, _ = Course.objects.update_or_create(
                course_code=data['course_code'],
                defaults={k: v for k, v in data.items() if k != 'course_code'},
            )
            course_map[data['course_code']] = course
        self.stdout.write(f'  Course: {len(course_map)}개')
        return course_map

    def _seed_schedules(self, course_map):
        # 시간표는 (course, day, start_time) 조합 기준 중복 제거
        count = 0
        for code, day, start, end, building, room in SAMPLE_SCHEDULES:
            CourseSchedule.objects.update_or_create(
                course=course_map[code], day_of_week=day, start_time=start,
                defaults={'end_time': end, 'building': building, 'room': room},
            )
            count += 1
        self.stdout.write(f'  CourseSchedule: {count}개')

    def _seed_prerequisites(self, course_map):
        count = 0
        for course_code, prereq_code in SAMPLE_PREREQUISITES:
            CoursePrerequisite.objects.update_or_create(
                course=course_map[course_code],
                prerequisite=course_map[prereq_code],
            )
            count += 1
        self.stdout.write(f'  CoursePrerequisite: {count}개')

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
