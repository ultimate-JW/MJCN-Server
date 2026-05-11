from datetime import date, time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from courses.models import (
    AcademicCalendar,
    Course,
    CoursePrerequisite,
    CourseSchedule,
    GraduationRequirement,
)


def _make_course(**overrides):
    defaults = dict(
        course_code='CSE1001',
        name='프로그래밍기초',
        college='ICT융합대학',
        department='융합소프트웨어학부',
        major='데이터테크놀로지전공',
        category='전공필수',
        credits=3,
        year_open=1,
        semester_open=1,
    )
    defaults.update(overrides)
    return Course.objects.create(**defaults)


class CourseModelTests(TestCase):
    def test_정상_생성(self):
        course = _make_course()
        self.assertEqual(course.course_code, 'CSE1001')
        self.assertEqual(course.credits, 3)
        self.assertEqual(course.professor, '')

    def test_course_code_unique(self):
        _make_course()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _make_course(name='다른과목')

    def test_category_choices_검증(self):
        course = _make_course(category='교양심화')
        with self.assertRaises(ValidationError):
            course.full_clean()

    def test_str_표현(self):
        course = _make_course()
        self.assertEqual(str(course), '[CSE1001] 프로그래밍기초')

    def test_정렬은_course_code_기준(self):
        _make_course(course_code='CSE2001', name='자료구조')
        _make_course(course_code='CSE1001', name='프로그래밍기초')
        codes = list(Course.objects.values_list('course_code', flat=True))
        self.assertEqual(codes, ['CSE1001', 'CSE2001'])


class CoursePrerequisiteModelTests(TestCase):
    def setUp(self):
        self.base = _make_course(course_code='CSE1001', name='프로그래밍기초')
        self.next = _make_course(course_code='CSE2001', name='자료구조')

    def test_정상_생성(self):
        prereq = CoursePrerequisite.objects.create(
            course=self.next, prerequisite=self.base
        )
        self.assertEqual(prereq.course, self.next)
        self.assertEqual(prereq.prerequisite, self.base)

    def test_unique_together(self):
        CoursePrerequisite.objects.create(course=self.next, prerequisite=self.base)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CoursePrerequisite.objects.create(
                    course=self.next, prerequisite=self.base
                )

    def test_str_표현(self):
        prereq = CoursePrerequisite.objects.create(
            course=self.next, prerequisite=self.base
        )
        self.assertEqual(str(prereq), '자료구조 <- 프로그래밍기초')

    def test_course_삭제_시_관계도_삭제됨(self):
        CoursePrerequisite.objects.create(course=self.next, prerequisite=self.base)
        self.next.delete()
        self.assertEqual(CoursePrerequisite.objects.count(), 0)

    def test_related_name_접근(self):
        CoursePrerequisite.objects.create(course=self.next, prerequisite=self.base)
        self.assertEqual(self.next.prerequisites.count(), 1)
        self.assertEqual(self.base.required_by.count(), 1)


class CourseScheduleModelTests(TestCase):
    def setUp(self):
        self.course = _make_course()

    def test_정상_생성(self):
        schedule = CourseSchedule.objects.create(
            course=self.course,
            day_of_week='월',
            start_time=time(9, 0),
            end_time=time(10, 30),
            building='본관',
            room='101',
        )
        self.assertEqual(schedule.day_of_week, '월')
        self.assertEqual(schedule.building, '본관')

    def test_day_of_week_choices_검증(self):
        schedule = CourseSchedule(
            course=self.course,
            day_of_week='토',
            start_time=time(9, 0),
            end_time=time(10, 30),
        )
        with self.assertRaises(ValidationError):
            schedule.full_clean()

    def test_str_표현(self):
        schedule = CourseSchedule.objects.create(
            course=self.course,
            day_of_week='화',
            start_time=time(13, 0),
            end_time=time(14, 30),
        )
        self.assertEqual(
            str(schedule), '프로그래밍기초 화 13:00:00-14:30:00'
        )

    def test_course_삭제_시_시간표도_삭제됨(self):
        CourseSchedule.objects.create(
            course=self.course,
            day_of_week='월',
            start_time=time(9, 0),
            end_time=time(10, 30),
        )
        self.course.delete()
        self.assertEqual(CourseSchedule.objects.count(), 0)

    def test_related_name_접근(self):
        CourseSchedule.objects.create(
            course=self.course,
            day_of_week='월',
            start_time=time(9, 0),
            end_time=time(10, 30),
        )
        self.assertEqual(self.course.schedules.count(), 1)


class GraduationRequirementModelTests(TestCase):
    def test_정상_생성(self):
        req = GraduationRequirement.objects.create(
            department='융합소프트웨어학부',
            admission_year=2024,
            category='전공필수',
            required_credits=42,
            total_required=130,
        )
        self.assertEqual(req.required_credits, 42)

    def test_unique_together(self):
        GraduationRequirement.objects.create(
            department='융합소프트웨어학부',
            admission_year=2024,
            category='전공필수',
            required_credits=42,
            total_required=130,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GraduationRequirement.objects.create(
                    department='융합소프트웨어학부',
                    admission_year=2024,
                    category='전공필수',
                    required_credits=45,
                    total_required=130,
                )

    def test_같은_학과_다른_입학년도는_허용(self):
        GraduationRequirement.objects.create(
            department='융합소프트웨어학부',
            admission_year=2024,
            category='전공필수',
            required_credits=42,
            total_required=130,
        )
        GraduationRequirement.objects.create(
            department='융합소프트웨어학부',
            admission_year=2025,
            category='전공필수',
            required_credits=45,
            total_required=130,
        )
        self.assertEqual(GraduationRequirement.objects.count(), 2)

    def test_str_표현(self):
        req = GraduationRequirement.objects.create(
            department='융합소프트웨어학부',
            admission_year=2024,
            category='전공필수',
            required_credits=42,
            total_required=130,
        )
        self.assertEqual(
            str(req), '융합소프트웨어학부 2024 전공필수: 42학점'
        )


class AcademicCalendarModelTests(TestCase):
    def test_정상_생성(self):
        cal = AcademicCalendar.objects.create(
            year=2026,
            semester=1,
            semester_start=date(2026, 3, 2),
            semester_end=date(2026, 6, 19),
        )
        self.assertEqual(cal.year, 2026)
        self.assertEqual(cal.semester_start, date(2026, 3, 2))

    def test_날짜_필드들_null_허용(self):
        cal = AcademicCalendar.objects.create(year=2026, semester=2)
        self.assertIsNone(cal.semester_start)
        self.assertIsNone(cal.registration_start)

    def test_unique_together(self):
        AcademicCalendar.objects.create(year=2026, semester=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicCalendar.objects.create(year=2026, semester=1)

    def test_같은_연도_다른_학기는_허용(self):
        AcademicCalendar.objects.create(year=2026, semester=1)
        AcademicCalendar.objects.create(year=2026, semester=2)
        self.assertEqual(AcademicCalendar.objects.count(), 2)

    def test_str_표현(self):
        cal = AcademicCalendar.objects.create(year=2026, semester=1)
        self.assertEqual(str(cal), '2026년 1학기')
