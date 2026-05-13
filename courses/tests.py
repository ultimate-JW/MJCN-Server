from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import CourseHistory, CurrentCourse
from courses.models import (
    AcademicCalendar,
    Course,
    CoursePrerequisite,
    CourseSchedule,
    GraduationRequirement,
)

User = get_user_model()


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


# Course 모델 전체를 검증하기 위한 테스트 클래스
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

    # category 필드에 허용되지 않은 값 입력시 에러가 발생하는지
    def test_category_choices_검증(self): 
        course = _make_course(category='교양심화')
        with self.assertRaises(ValidationError):
            course.full_clean()

    # course 객체의 문자열 출력(__str__)이 올바른지
    def test_str_표현(self):
        course = _make_course()
        self.assertEqual(str(course), '[CSE1001] 프로그래밍기초')

    # course 조회 시 기본 정렬 기준이 course_code인지
    def test_정렬은_course_code_기준(self):
        _make_course(course_code='CSE2001', name='자료구조')
        _make_course(course_code='CSE1001', name='프로그래밍기초')
        codes = list(Course.objects.values_list('course_code', flat=True))
        self.assertEqual(codes, ['CSE1001', 'CSE2001'])


# 선수과목 관계 모델 검증
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

    # 중복으로 선수과목 저장되는지 검사
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

    # 과목 삭제 시, 선수과목 관계 데이터도 함께 삭제되는지
    def test_course_삭제_시_관계도_삭제됨(self):
        CoursePrerequisite.objects.create(course=self.next, prerequisite=self.base)
        self.next.delete()
        self.assertEqual(CoursePrerequisite.objects.count(), 0)

    def test_related_name_접근(self):
        CoursePrerequisite.objects.create(course=self.next, prerequisite=self.base)
        self.assertEqual(self.next.prerequisites.count(), 1)
        self.assertEqual(self.base.required_by.count(), 1)


# 강의 시간표 모델 검증
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

    # 허용되지 않은 요일값 에러 테스트
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


# 졸업 요건 모델 검증
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

    # 동일 학과여도 입학년도가 다르면 별도 졸업요건이 저장되는지
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


# 학사일정 모델 검증
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

    # 동일 연도, 학기에 중복 금지 제약조건이 잘 동작하는지
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


# ===== API 통합 테스트 =====

def _make_user(email='student@mju.ac.kr', **overrides):
    defaults = dict(
        password='testpass123',
        name='홍길동',
        grade=2,
        semester=2,
        admission_year=2024,
        graduation_year=2027,
        graduation_month=8,
        major='데이터테크놀로지전공',
        is_email_verified=True,
        is_onboarding_completed=True,
    )
    defaults.update(overrides)
    return User.objects.create_user(email=email, **defaults)


class CourseSearchAPITests(APITestCase):
    url = '/api/v1/courses/'

    def setUp(self):
        self.user = _make_user()
        _make_course(course_code='CSE1001', name='프로그래밍기초', category='전공필수', credits=3)
        _make_course(course_code='CSE2001', name='자료구조', category='전공필수', credits=3)
        _make_course(course_code='GEN1001', name='글쓰기', category='교양필수', credits=2,
                     department='교양', major='교양')

    def test_인증_없으면_401(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_전체_목록_반환(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 3)
        self.assertEqual(len(res.data['results']), 3)

    def test_q로_과목명_검색(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'q': '자료'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['course_code'], 'CSE2001')

    def test_q로_과목코드_검색(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'q': 'CSE'})
        codes = {c['course_code'] for c in res.data['results']}
        self.assertEqual(codes, {'CSE1001', 'CSE2001'})

    def test_category_필터(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'category': '교양필수'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['course_code'], 'GEN1001')

    def test_credits_필터(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'credits': '3'})
        self.assertEqual(res.data['count'], 2)

    def test_여러_필터_AND(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'category': '전공필수', 'credits': '3'})
        self.assertEqual(res.data['count'], 2)

    def test_결과_없으면_빈_배열(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'q': '존재하지않는과목'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 0)
        self.assertEqual(res.data['results'], [])


# 과목 검색 페이지네이션 검증 (spec 6.14 공통 응답 형식)
class CourseSearchPaginationTests(APITestCase):
    url = '/api/v1/courses/'

    def setUp(self):
        self.user = _make_user()
        # 페이지 동작 검증을 위해 25개 생성 (기본 page_size 20 초과)
        for i in range(25):
            _make_course(
                course_code=f'CSE{i:04d}',
                name=f'테스트과목{i:02d}',
                category='전공필수' if i % 2 == 0 else '전공선택',
                credits=3,
            )

    def test_응답_구조에_count_next_previous_results_포함(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('count', res.data)
        self.assertIn('next', res.data)
        self.assertIn('previous', res.data)
        self.assertIn('results', res.data)
        self.assertEqual(res.data['count'], 25)
        # 기본 page_size=20이라 첫 페이지 결과는 20개
        self.assertEqual(len(res.data['results']), 20)
        # 다음 페이지 존재
        self.assertIsNotNone(res.data['next'])
        # 첫 페이지에선 previous None
        self.assertIsNone(res.data['previous'])

    def test_page_size_쿼리파라미터_동작(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'page_size': 5})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data['results']), 5)
        self.assertEqual(res.data['count'], 25)

    def test_page_2_접근(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'page': 2})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # 25개 중 첫 페이지 20개를 제외한 나머지 5개
        self.assertEqual(len(res.data['results']), 5)
        # 두 번째 페이지에선 previous가 있어야 함, next는 None
        self.assertIsNotNone(res.data['previous'])
        self.assertIsNone(res.data['next'])

    def test_필터와_페이지네이션_조합(self):
        self.client.force_authenticate(user=self.user)
        # 25개 중 짝수 인덱스(0,2,...,24) 13개가 전공필수
        res = self.client.get(self.url, {'category': '전공필수', 'page_size': 5})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 13)
        self.assertEqual(len(res.data['results']), 5)
        # 결과들이 전부 전공필수인지
        for item in res.data['results']:
            self.assertEqual(item['category'], '전공필수')


class CompletionStatusAPITests(APITestCase):
    url = '/api/v1/courses/status/'

    def setUp(self):
        self.user = _make_user()
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='전공필수', required_credits=42, total_required=130,
        )
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='전공선택', required_credits=24, total_required=130,
        )
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='교양필수', required_credits=18, total_required=130,
        )
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='교양선택', required_credits=16, total_required=130,
        )

    def test_인증_없으면_401(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_빈_이수내역이면_모두_0(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['total_completed'], 0)
        self.assertEqual(res.data['total_required'], 130)
        self.assertEqual(res.data['total_remaining'], 130)

    def test_카테고리는_5개(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        cats = [c['category'] for c in res.data['categories']]
        self.assertEqual(
            cats,
            ['전공필수', '전공선택', '교양필수', '교양선택', '일반선택'],
        )

    def test_CourseHistory_가_이수학점에_반영(self):
        CourseHistory.objects.create(
            user=self.user, course_name='프로그래밍기초', course_code='CSE1001',
            year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        major_required = next(c for c in res.data['categories'] if c['category'] == '전공필수')
        self.assertEqual(major_required['completed'], 3)
        self.assertEqual(major_required['remaining'], 39)

    def test_CurrentCourse_도_이수학점에_반영(self):
        _make_course(course_code='CSE2001', name='자료구조', category='전공필수', credits=3)
        CurrentCourse.objects.create(
            user=self.user, course_name='자료구조', course_code='CSE2001',
            day_of_week='월', start_time=time(9, 0), end_time=time(10, 30),
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        major_required = next(c for c in res.data['categories'] if c['category'] == '전공필수')
        self.assertEqual(major_required['completed'], 3)


class NextSemesterRecommendAPITests(APITestCase):
    url = '/api/v1/courses/recommend/next/'

    def setUp(self):
        self.user = _make_user()
        # 다음 학기는 view 로직상 (현재년도, 2) 또는 (다음년도, 1)
        # 두 케이스 모두 매칭되도록 과목을 풍부하게 생성
        from datetime import date as _date
        today = _date.today()
        if self.user.semester in (1, 2):
            self.next_year, self.next_sem = today.year, 2
        else:
            self.next_year, self.next_sem = today.year + 1, 1

        self.prog = _make_course(
            course_code='CSE2001', name='자료구조', category='전공필수',
            year_open=self.next_year, semester_open=self.next_sem,
        )
        self.algo = _make_course(
            course_code='CSE3001', name='알고리즘', category='전공선택',
            year_open=self.next_year, semester_open=self.next_sem,
        )
        self.liberal = _make_course(
            course_code='GEN1001', name='글쓰기', category='교양필수',
            department='교양', major='교양',
            year_open=self.next_year, semester_open=self.next_sem,
        )

    def test_인증_없으면_401(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_응답_4개_카테고리_키(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(res.data.keys()),
            {'major_required', 'major_elective', 'liberal_required', 'liberal_elective'},
        )

    def test_이미_들은_과목은_제외(self):
        CourseHistory.objects.create(
            user=self.user, course_name='자료구조', course_code='CSE2001',
            year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        names = [c['name'] for c in res.data['major_required']]
        self.assertNotIn('자료구조', names)

    def test_현재_수강중인_과목도_제외(self):
        CurrentCourse.objects.create(
            user=self.user, course_name='자료구조', course_code='CSE2001',
            day_of_week='월', start_time=time(9, 0), end_time=time(10, 30),
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        names = [c['name'] for c in res.data['major_required']]
        self.assertNotIn('자료구조', names)

    def test_선수과목_미이수면_제외(self):
        base = _make_course(
            course_code='CSE1001', name='프로그래밍기초', category='전공필수',
            year_open=1, semester_open=1,
        )
        CoursePrerequisite.objects.create(course=self.prog, prerequisite=base)
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        names = [c['name'] for c in res.data['major_required']]
        self.assertNotIn('자료구조', names)

    def test_선수과목_이수했으면_포함(self):
        base = _make_course(
            course_code='CSE1001', name='프로그래밍기초', category='전공필수',
            year_open=1, semester_open=1,
        )
        CoursePrerequisite.objects.create(course=self.prog, prerequisite=base)
        CourseHistory.objects.create(
            user=self.user, course_name='프로그래밍기초', course_code='CSE1001',
            year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        names = [c['name'] for c in res.data['major_required']]
        self.assertIn('자료구조', names)


class CurriculumRecommendAPITests(APITestCase):
    url = '/api/v1/courses/recommend/curriculum/'

    def setUp(self):
        self.user = _make_user()
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='전공필수', required_credits=9, total_required=12,
        )
        _make_course(course_code='CSE1001', name='프로그래밍기초', category='전공필수',
                     year_open=1, semester_open=1)
        _make_course(course_code='CSE2001', name='자료구조', category='전공필수',
                     year_open=2, semester_open=1)
        _make_course(course_code='CSE3001', name='알고리즘', category='전공필수',
                     year_open=3, semester_open=1)

    def test_인증_없으면_401(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_플랜_배열_반환(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsInstance(res.data, list)
        self.assertGreater(len(res.data), 0)

    def test_각_플랜은_plan_number와_semesters를_가짐(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        for plan in res.data:
            self.assertIn('plan_number', plan)
            self.assertIn('semesters', plan)
            for sem in plan['semesters']:
                self.assertIn('year', sem)
                self.assertIn('semester', sem)
                self.assertIn('courses', sem)
