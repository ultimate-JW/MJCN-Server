from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import CourseHistory, CurrentCourse
from courses.category_map import classify_liberal_subtype, classify_core_area
from courses.management.commands.import_courses_from_xlsx import parse_year_open
from courses.models import (
    AcademicCalendar,
    Course,
    CourseOffering,
    CoursePrerequisite,
    CourseSchedule,
    GraduationRequirement,
)
from courses.services import (
    BONUS_BACKLOG_REQUIRED,
    BONUS_CATEGORY_SHORT,
    BONUS_DESIGNATED_REQUIRED,
    BONUS_GRADE_SEMESTER_MATCH,
    BONUS_INTEREST_MATCH,
    BONUS_MAJOR_REQUIRED,
    PENALTY_GRADE_EXCEEDED,
    PENALTY_PREREQUISITE_MISSING,
    calculate_recommendation_score,
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
        # 4종 + 핵심교양 4영역 도입(#47 Phase 2) 후 unique 키는
        # (dept, year, category, liberal_subtype, core_area).
        # SQLite NULL != NULL 정책상 core_area=NULL row끼리는 충돌 안 잡히므로,
        # 핵심교양 + 영역 명시 값으로 모든 컬럼 같을 때만 검증한다.
        GraduationRequirement.objects.create(
            department='융합소프트웨어학부',
            admission_year=2024,
            category='일반교양',
            liberal_subtype='핵심교양',
            core_area='역사와 철학',
            required_credits=3,
            total_required=134,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GraduationRequirement.objects.create(
                    department='융합소프트웨어학부',
                    admission_year=2024,
                    category='일반교양',
                    liberal_subtype='핵심교양',
                    core_area='역사와 철학',
                    required_credits=4,
                    total_required=134,
                )

    def test_핵심교양_4영역은_별개_row로_허용(self):
        # 같은 liberal_subtype='핵심교양'이라도 core_area가 다르면 별개 row (#47 Phase 2)
        for area in ['역사와 철학', '사회와 공동체', '문화와 예술', '과학기술과 정보']:
            GraduationRequirement.objects.create(
                department='컴퓨터공학전공',
                admission_year=2024,
                category='일반교양',
                liberal_subtype='핵심교양',
                core_area=area,
                required_credits=3,
                total_required=134,
            )
        self.assertEqual(
            GraduationRequirement.objects.filter(liberal_subtype='핵심교양').count(), 4
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
        _make_course(course_code='GEN1001', name='글쓰기', category='공통교양', credits=2,
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
        res = self.client.get(self.url, {'category': '공통교양'})
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


# spec 6.6 추가 필터 5종 — college/department/major/year_open/semester_open (#123 §1)
class CourseSearchAdditionalFilterTests(APITestCase):
    url = '/api/v1/courses/'

    def setUp(self):
        self.user = _make_user()
        # 학과·학년·학기 분산하여 5 필터 각각 격리 검증 가능한 fixture
        _make_course(course_code='ICT1001', name='ICT개론',
                     college='반도체ICT대학', department='컴퓨터정보통신공학부',
                     major='컴퓨터공학전공', year_open=1, semester_open=1)
        _make_course(course_code='ICT2001', name='운영체제',
                     college='반도체ICT대학', department='컴퓨터정보통신공학부',
                     major='컴퓨터공학전공', year_open=3, semester_open=2)
        _make_course(course_code='SEM3001', name='반도체공학',
                     college='반도체ICT대학', department='반도체공학부',
                     major='반도체공학전공', year_open=2, semester_open=1)
        _make_course(course_code='HUM1001', name='문학개론',
                     college='인문대학', department='국어국문학부',
                     major='국어국문학전공', year_open=1, semester_open=2)
        # 학년 sentinel 0 — 전학년 공통 (#36 import 명령의 학년 무관 처리)
        _make_course(course_code='COM0001', name='벤처창업',
                     college='반도체ICT대학', department='컴퓨터정보통신공학부',
                     major='컴퓨터공학전공', year_open=0, semester_open=1)
        # 계절학기 분기 — 3=하계 / 4=동계 (#25 매핑)
        _make_course(course_code='SUM3001', name='하계특강',
                     college='반도체ICT대학', department='컴퓨터정보통신공학부',
                     major='컴퓨터공학전공', year_open=2, semester_open=3)
        _make_course(course_code='WIN4001', name='동계특강',
                     college='반도체ICT대학', department='컴퓨터정보통신공학부',
                     major='컴퓨터공학전공', year_open=3, semester_open=4)

    def test_college_필터(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'college': '반도체ICT대학'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        codes = {c['course_code'] for c in res.data['results']}
        self.assertEqual(codes, {'ICT1001', 'ICT2001', 'SEM3001',
                                  'COM0001', 'SUM3001', 'WIN4001'})

    def test_department_필터(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'department': '컴퓨터정보통신공학부'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        codes = {c['course_code'] for c in res.data['results']}
        self.assertEqual(codes, {'ICT1001', 'ICT2001', 'COM0001',
                                  'SUM3001', 'WIN4001'})

    def test_major_필터(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'major': '반도체공학전공'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['course_code'], 'SEM3001')

    def test_year_open_필터_1학년(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'year_open': '1'})
        codes = {c['course_code'] for c in res.data['results']}
        self.assertEqual(codes, {'ICT1001', 'HUM1001'})

    def test_year_open_필터_sentinel_0_전학년(self):
        # year_open=0 sentinel은 학년 무관 강의를 명시 조회 (필터로도 분리 가능해야 함)
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'year_open': '0'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['course_code'], 'COM0001')

    def test_semester_open_필터_하계_3(self):
        # 계절학기 매핑 검증 — 3=하계, 단일 row 격리되는지 확인
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'semester_open': '3'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['course_code'], 'SUM3001')

    def test_semester_open_필터_동계_4(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'semester_open': '4'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['course_code'], 'WIN4001')


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
            category='공통교양', required_credits=18, total_required=130,
        )
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='일반교양', required_credits=16, total_required=130,
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
            ['전공필수', '공통교양', '핵심교양', '학문기초교양', '전공선택', '일반교양', '자유선택'],
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

    def test_채플_2024학번은_4회_required(self):
        # graduation_requirements.md §2.1 — 1999학번 이후 채플 4회 의무
        self.user.chapel_count = 1
        self.user.save()
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.data['chapel']['completed'], 1)
        self.assertEqual(res.data['chapel']['required'], 4)
        self.assertEqual(res.data['chapel']['remaining'], 3)

    def test_채플_옛학번은_2회_required(self):
        # 1996~1998학번은 2회 의무 (시연 범위 외지만 학칙 분기 검증)
        self.user.admission_year = 1997
        self.user.chapel_count = 2
        self.user.save()
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.data['chapel']['required'], 2)
        self.assertEqual(res.data['chapel']['remaining'], 0)

    def test_전공_초과분은_자유선택으로_자동_합산(self):
        # graduation_requirements.md §6 — 카테고리 required 초과분이 자유선택으로 자동 인정
        # setUp의 전공필수 42 required인데 45학점 들으면 3학점이 자유선택으로 이동
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='자유선택', required_credits=10, total_required=130,
        )
        for i in range(15):
            CourseHistory.objects.create(
                user=self.user, course_name=f'전공{i}', course_code=f'MAJ{i:03d}',
                year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
            )  # 총 45학점 = required 42 + 초과 3
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        free = next(c for c in res.data['categories'] if c['category'] == '자유선택')
        self.assertEqual(free['completed'], 3)   # 초과 3학점 자동 자유선택
        self.assertEqual(free['remaining'], 7)   # required 10 - 3


class CompletionStatusBreakdownTests(APITestCase):
    """이수현황 응답 영역/필수 과목 분해 (#68).

    공통교양/핵심교양 → areas (core_area 4영역)
    전공필수/학문기초교양 → required_courses (학칙 §5.1, §4.4 강제 과목)
    그 외 카테고리 → 둘 다 None
    """
    url = '/api/v1/courses/status/'

    def _gr(self, **kwargs):
        defaults = dict(
            department='컴퓨터공학전공', admission_year=2024, total_required=134,
        )
        defaults.update(kwargs)
        return GraduationRequirement.objects.create(**defaults)

    def setUp(self):
        # 컴공 2024 학생 — required_courses.py에 박힌 학과
        self.user = _make_user(major='컴퓨터공학전공')
        # 공통교양 17 = 4영역 분해 (graduation_requirements.md §4.2)
        self._gr(category='공통교양', liberal_subtype='공통교양', core_area='기독교', required_credits=6)
        self._gr(category='공통교양', liberal_subtype='공통교양', core_area='사고와 표현', required_credits=3)
        self._gr(category='공통교양', liberal_subtype='공통교양', core_area='언어', required_credits=6)
        self._gr(category='공통교양', liberal_subtype='공통교양', core_area='진로와 디지털리터러시', required_credits=2)
        # 핵심교양 12 = 4영역 × 3학점 (§4.3)
        self._gr(category='핵심교양', liberal_subtype='핵심교양', core_area='역사와 철학', required_credits=3)
        self._gr(category='핵심교양', liberal_subtype='핵심교양', core_area='사회와 공동체', required_credits=3)
        self._gr(category='핵심교양', liberal_subtype='핵심교양', core_area='문화와 예술', required_credits=3)
        self._gr(category='핵심교양', liberal_subtype='핵심교양', core_area='과학기술과 정보', required_credits=3)
        # 학문기초 / 전공필수 / 전공선택 / 일반교양 / 자유선택 — 분해 검증에는 합계만 있으면 됨
        self._gr(category='학문기초교양', liberal_subtype='학문기초교양', required_credits=15)
        self._gr(category='전공필수', required_credits=24)
        self._gr(category='전공선택', required_credits=46)
        self._gr(category='일반교양', liberal_subtype='일반교양', required_credits=10)
        self._gr(category='자유선택', required_credits=10)
        self.client.force_authenticate(user=self.user)

    # --- areas (공통교양 / 핵심교양) ---

    def test_공통교양_areas_4영역_노출(self):
        res = self.client.get(self.url)
        common = next(c for c in res.data['categories'] if c['category'] == '공통교양')
        areas = common['areas']
        self.assertEqual(len(areas), 4)
        names = [a['area'] for a in areas]
        self.assertIn('기독교', names)
        self.assertIn('사고와 표현', names)
        self.assertIn('언어', names)
        self.assertIn('진로와 디지털리터러시', names)

    def test_공통교양_areas_이수학점_반영(self):
        # 기독교 영역에 2학점 이수 → 그 영역만 completed 2
        CourseHistory.objects.create(
            user=self.user, course_name='성서와 인간이해', course_code='GEN1011',
            year=2024, semester=1, grade_received='A',
            category='공통교양', liberal_subtype='공통교양', core_area='기독교', credits=2,
        )
        res = self.client.get(self.url)
        common = next(c for c in res.data['categories'] if c['category'] == '공통교양')
        gidok = next(a for a in common['areas'] if a['area'] == '기독교')
        self.assertEqual(gidok['completed'], 2)
        self.assertEqual(gidok['remaining'], 4)   # required 6 - 2
        # 다른 영역은 0 그대로
        sago = next(a for a in common['areas'] if a['area'] == '사고와 표현')
        self.assertEqual(sago['completed'], 0)

    def test_핵심교양_areas_4영역_노출(self):
        res = self.client.get(self.url)
        core = next(c for c in res.data['categories'] if c['category'] == '핵심교양')
        self.assertEqual(len(core['areas']), 4)
        for area in core['areas']:
            self.assertEqual(area['required'], 3)

    # --- required_courses (전공필수 / 학문기초교양) ---

    def test_전공필수_required_courses_8과목(self):
        res = self.client.get(self.url)
        major_req = next(c for c in res.data['categories'] if c['category'] == '전공필수')
        names = [c['name'] for c in major_req['required_courses']]
        # 학칙 §5.1 컴공 전공필수 8과목 (graduation_requirements.md)
        self.assertEqual(len(names), 8)
        self.assertIn('C언어', names)
        self.assertIn('캡스톤디자인', names)
        # 미이수 상태 → 전부 false
        self.assertTrue(all(c['completed'] is False for c in major_req['required_courses']))

    def test_전공필수_이수한_과목만_completed_true(self):
        CourseHistory.objects.create(
            user=self.user, course_name='C언어', course_code='CSE1001',
            year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
        )
        res = self.client.get(self.url)
        major_req = next(c for c in res.data['categories'] if c['category'] == '전공필수')
        c_lang = next(c for c in major_req['required_courses'] if c['name'] == 'C언어')
        algo = next(c for c in major_req['required_courses'] if c['name'] == '알고리즘')
        self.assertTrue(c_lang['completed'])
        self.assertFalse(algo['completed'])

    def test_학문기초_required_courses_5과목(self):
        res = self.client.get(self.url)
        foundation = next(c for c in res.data['categories'] if c['category'] == '학문기초교양')
        names = [c['name'] for c in foundation['required_courses']]
        # 학칙 §4.4 컴공 학문기초 5과목
        self.assertEqual(names, ['미적분학1', '통계학개론', '공학수학1', '이산수학개론', '선형대수학개론'])

    # --- 분해 대상 외 카테고리 ---

    def test_분해_없는_카테고리는_areas_required_courses_모두_None(self):
        res = self.client.get(self.url)
        for cat_name in ('전공선택', '일반교양', '자유선택'):
            cat = next(c for c in res.data['categories'] if c['category'] == cat_name)
            self.assertIsNone(cat['areas'], f'{cat_name} areas')
            self.assertIsNone(cat['required_courses'], f'{cat_name} required_courses')

    def test_타과_학생은_required_courses_None(self):
        # required_courses.py에 등록 안 된 학과 → 필수 과목 분해 None
        other_user = _make_user(email='other@mju.ac.kr', major='데이터테크놀로지전공')
        self.client.force_authenticate(user=other_user)
        # 데이터테크놀로지 GR도 필요
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='전공필수', required_credits=30, total_required=130,
        )
        res = self.client.get(self.url)
        major_req = next(c for c in res.data['categories'] if c['category'] == '전공필수')
        self.assertIsNone(major_req['required_courses'])


class NextSemesterRecommendAPITests(APITestCase):
    """다음학기 추천 API 통합 테스트 (spec 5.3.1, 점수 기반 단일 리스트)"""
    url = '/api/v1/courses/recommend/next/'

    def setUp(self):
        # 사용자: 2학년 1학기, 데이터테크놀로지전공
        self.user = _make_user()

        # 졸업요건: 전공필수 30학점 필요 → 전공필수 카테고리가 부족 카테고리에 포함됨
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='전공필수', required_credits=30, total_required=130,
        )

        # 후보 과목 4개 — 점수 분포가 갈리도록 설계
        self.base = _make_course(
            course_code='CSE1001', name='프로그래밍기초', category='전공필수',
            year_open=1, semester_open=1, tags=['IT/개발'],
        )
        self.mid = _make_course(
            course_code='CSE2001', name='자료구조', category='전공필수',
            year_open=2, semester_open=1, tags=['IT/개발'],
        )
        self.elective = _make_course(
            course_code='CSE3001', name='알고리즘', category='전공선택',
            year_open=3, semester_open=1, tags=[],
        )
        self.liberal = _make_course(
            course_code='GEN1001', name='글쓰기', category='공통교양',
            department='교양', major='교양',
            year_open=1, semester_open=2, tags=[],
        )

    def test_인증_없으면_401(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_응답은_단일_리스트(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsInstance(res.data, list)
        # setUp의 4개 과목이 모두 후보 (이수/수강 중 없음)
        self.assertEqual(len(res.data), 4)

    def test_각_항목에_score와_과목정보가_포함됨(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        item = res.data[0]
        # offerings 안에 section_no/professor/schedules — Course 레벨에는 노출 X (#111)
        for key in ('score', 'course_code', 'name', 'category', 'credits', 'offerings'):
            self.assertIn(key, item)
        self.assertNotIn('professor', item)
        self.assertNotIn('schedules', item)

    def test_schedules에_building_키_없음_116(self):
        """#116 — xlsx에 건물명 정보 없어 building은 응답에서 제거. room만 노출.
        #111 merge 후 schedules는 offerings 안에 nest됨."""
        offering = CourseOffering.objects.create(
            course=self.base, year=2026, semester=1,
            section_no='01', professor='김교수',
        )
        CourseSchedule.objects.create(
            course=self.base, offering=offering, day_of_week='월',
            start_time=time(9, 0), end_time=time(10, 30),
            room='Y5407',
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'year': 2026, 'semester': 1})
        item = next(i for i in res.data if i['course_code'] == 'CSE1001')
        sch = item['offerings'][0]['schedules'][0]
        self.assertEqual(sch['room'], 'Y5407')
        self.assertNotIn('building', sch)
        for key in ('day_of_week', 'start_time', 'end_time', 'room'):
            self.assertIn(key, sch)

    def test_score_내림차순_정렬(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        scores = [item['score'] for item in res.data]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_이미_이수한_과목은_Hard_Filter로_제외(self):
        CourseHistory.objects.create(
            user=self.user, course_name='자료구조', course_code='CSE2001',
            year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        codes = {item['course_code'] for item in res.data}
        self.assertNotIn('CSE2001', codes)

    def test_현재_수강중인_과목도_Hard_Filter로_제외(self):
        CurrentCourse.objects.create(
            user=self.user, course_name='자료구조', course_code='CSE2001',
            day_of_week='월', start_time=time(9, 0), end_time=time(10, 30),
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        codes = {item['course_code'] for item in res.data}
        self.assertNotIn('CSE2001', codes)

    def test_동일학과_선수과목_미이수는_hard_filter(self):
        """#47 7번: 다음학기 추천도 전체커리큘럼처럼 hard filter — 선수 미이수는 결과에서 제외"""
        CoursePrerequisite.objects.create(course=self.mid, prerequisite=self.base)
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        codes = {item['course_code'] for item in res.data}
        # 자료구조(CSE2001)는 base(CSE1001) 선수 미이수라 결과에서 제외
        self.assertNotIn('CSE2001', codes)

    def test_관심사_매칭_과목이_상위에_노출(self):
        """관심사 IT/개발 → IT/개발 태그 과목이 비태그 과목보다 위"""
        self.user.interests.create(category='IT/개발')
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        # CSE2001(2-1 전공필수, IT/개발 태그)이 GEN1001(1-2 교양필수, 태그X)보다 위
        codes_ordered = [item['course_code'] for item in res.data]
        self.assertLess(codes_ordered.index('CSE2001'), codes_ordered.index('GEN1001'))

    def test_상위_학년_과목도_결과에_포함되되_하단(self):
        """학년 초과는 Hard Filter 아닌 Soft 감점 — 후보엔 들어가지만 점수 낮음"""
        adv = _make_course(
            course_code='CSE4001', name='고급주제', category='전공선택',
            year_open=4, semester_open=2, tags=[],
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        codes = [item['course_code'] for item in res.data]
        self.assertIn('CSE4001', codes)
        # CSE4001 점수가 CSE2001(2-1 전공필수)보다 낮아야 함
        adv_score = next(i['score'] for i in res.data if i['course_code'] == 'CSE4001')
        mid_score = next(i['score'] for i in res.data if i['course_code'] == 'CSE2001')
        self.assertLess(adv_score, mid_score)

    def test_동점일때_카테고리_우선순위로_정렬(self):
        """spec 5.3.1: score DESC → CATEGORY_PRIORITY ASC → course_code ASC"""
        # GEN1001(교양필수, 1-2)과 비교군 동점 만들기 — 같은 조건의 전공필수 추가
        same_score_major = _make_course(
            course_code='CSE0001', name='입문과목', category='전공필수',
            year_open=1, semester_open=2, tags=[],
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        # 같은 점수면 전공필수가 공통교양보다 위 (CATEGORY_PRIORITY: 전필=1 < 공통=2)
        ordered = [item['course_code'] for item in res.data]
        if 'CSE0001' in ordered and 'GEN1001' in ordered:
            cse_score = next(i['score'] for i in res.data if i['course_code'] == 'CSE0001')
            gen_score = next(i['score'] for i in res.data if i['course_code'] == 'GEN1001')
            if cse_score == gen_score:
                self.assertLess(ordered.index('CSE0001'), ordered.index('GEN1001'))

    # ----- 학기 필터 (Offering 매칭) + 쿼리 파라미터 (#36) -----

    def test_쿼리_파라미터_미지정시_자동결정_동작(self):
        """파라미터 안 줘도 정상 응답 — services가 사용자 학기 기반 자동 결정"""
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # setUp 4개 과목 다 Offering 없음 → 학기 필터 영향 없이 통과
        self.assertEqual(len(res.data), 4)

    def test_학기_파라미터_지정시_offering_매칭만_후보(self):
        """Offering 있는 Course는 target 학기 매칭만, Offering 없는 Course는 통과 (호환)"""
        CourseOffering.objects.create(
            course=self.base, year=2026, semester=1, section_no='B100',
        )
        CourseOffering.objects.create(
            course=self.mid, year=2026, semester=2, section_no='M200',
        )
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'year': 2026, 'semester': 1})
        codes = {item['course_code'] for item in res.data}
        self.assertIn('CSE1001', codes)       # base — 2026-1 매칭
        self.assertNotIn('CSE2001', codes)    # mid — 2026-2만 있음 → 제외
        self.assertIn('CSE3001', codes)       # elective — Offering 없음 → 통과
        self.assertIn('GEN1001', codes)       # liberal — Offering 없음 → 통과

    def test_잘못된_year_쿼리_파라미터_400(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'year': 'abc'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_잘못된_semester_쿼리_파라미터_400(self):
        """semester는 1/2/3/4만 허용"""
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'semester': '5'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ----- 분반(Offering) 단위 응답 그룹화 (#111) -----

    def test_offerings_분반별로_분리됨(self):
        """한 Course에 분반 N개면 응답 offerings 배열도 N건 — schedules 평탄화 X"""
        # base(CSE1001)에 2026-1 분반 2개 + 각각 schedule 1개
        off_a = CourseOffering.objects.create(
            course=self.base, year=2026, semester=1,
            section_no='01', professor='김교수',
        )
        off_b = CourseOffering.objects.create(
            course=self.base, year=2026, semester=1,
            section_no='02', professor='이교수',
        )
        CourseSchedule.objects.create(
            course=self.base, offering=off_a, day_of_week='월',
            start_time=time(9, 0), end_time=time(10, 30),
            building='M', room='301',
        )
        CourseSchedule.objects.create(
            course=self.base, offering=off_b, day_of_week='화',
            start_time=time(13, 0), end_time=time(14, 30),
            building='M', room='302',
        )

        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'year': 2026, 'semester': 1})

        item = next(i for i in res.data if i['course_code'] == 'CSE1001')
        offerings = item['offerings']
        self.assertEqual(len(offerings), 2)
        section_nos = {o['section_no'] for o in offerings}
        self.assertEqual(section_nos, {'01', '02'})
        # 분반별로 schedules가 독립 — 평탄화되지 않음 (#111 결함 G)
        for o in offerings:
            self.assertEqual(len(o['schedules']), 1)

    def test_offerings_타_학기_분반은_제외(self):
        """target term이 아닌 분반은 응답 offerings에 포함되지 않음"""
        CourseOffering.objects.create(
            course=self.base, year=2026, semester=1,
            section_no='01', professor='김교수',
        )
        CourseOffering.objects.create(
            course=self.base, year=2025, semester=2,
            section_no='99', professor='박교수',
        )

        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url, {'year': 2026, 'semester': 1})

        item = next(i for i in res.data if i['course_code'] == 'CSE1001')
        section_nos = {o['section_no'] for o in item['offerings']}
        self.assertEqual(section_nos, {'01'})  # 2025-2 분반은 제외

    def test_offering_없는_Course는_빈_offerings(self):
        """legacy 시드(Offering 없음) Course는 offerings 빈 배열로 응답"""
        # setUp의 elective(CSE3001)는 offering 없음 → 통과하되 offerings 빈 배열
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)

        item = next(i for i in res.data if i['course_code'] == 'CSE3001')
        self.assertEqual(item['offerings'], [])


# ===== parse_year_open 단위 테스트 (#36) =====

class ParseYearOpenTests(SimpleTestCase):
    """import_courses_from_xlsx.parse_year_open — 학년 셀 표현을 정수로 정규화."""

    def test_숫자_문자열은_정수_변환(self):
        self.assertEqual(parse_year_open('1'), 1)
        self.assertEqual(parse_year_open('4'), 4)

    def test_정수는_그대로(self):
        self.assertEqual(parse_year_open(2), 2)

    def test_전학년은_0_sentinel(self):
        self.assertEqual(parse_year_open('전학년'), 0)

    def test_빈값은_0(self):
        self.assertEqual(parse_year_open(None), 0)
        self.assertEqual(parse_year_open(''), 0)

    def test_숫자_접두_텍스트는_정수_부분_추출(self):
        # 학교 양식에서 '1학년' 같이 들어와도 1로 잡힘
        self.assertEqual(parse_year_open('1학년'), 1)
        self.assertEqual(parse_year_open('3학년'), 3)

    def test_알수없는_텍스트는_0_fallback(self):
        self.assertEqual(parse_year_open('abc'), 0)


# ===== 교양 4종 매핑 단위 테스트 (#? — graduation_requirements.md §3, 학칙 §6 표시기호) =====

class ClassifyLiberalSubtypeTests(SimpleTestCase):
    """classify_liberal_subtype — 학과코드 prefix + 교과목명 → 교양 4종 분류."""

    def test_prefix_교필_영역매핑_시_공통교양(self):
        # 학칙 §6 표시기호: 교필 = 공통교양. 단 §4.2 영역 매핑 통과 필요 (#80).
        self.assertEqual(classify_liberal_subtype('교필', '채플'), '공통교양')

    def test_prefix_교필_영역미매핑_시_일반교양_fallback(self):
        # 교필인데 §4.2 영역(기독교/사고와표현/언어/진로와디지털리터러시) 매핑 못 잡으면
        # 학칙 표 외 신규 과목 — 일반교양으로 강등 (#80).
        self.assertEqual(classify_liberal_subtype('교필', '미등록교필과목'), '일반교양')

    def test_prefix_교선_영역매핑_시_핵심교양(self):
        # 학칙 §6 표시기호: 교선 = 핵심교양. 단 §4.3 영역 매핑 통과 필요 (#80).
        self.assertEqual(classify_liberal_subtype('교선', '철학과 인간'), '핵심교양')

    def test_prefix_교선_영역미매핑_시_일반교양_fallback(self):
        # 교선인데 §4.3 영역(역사와철학/사회와공동체/문화와예술/과학기술과정보) 매핑 못 잡으면
        # 학칙 표 외 신규 과목 — 일반교양으로 강등 (#80).
        # 실제 사례: 4차산업혁명의이해 / 창업입문 / SW프로그래밍입문 등.
        self.assertEqual(classify_liberal_subtype('교선', '4차산업혁명의이해'), '일반교양')
        self.assertEqual(classify_liberal_subtype('교선', '창업입문'), '일반교양')

    def test_prefix_기자_는_학문기초교양(self):
        # 자연계 학문기초 — 미적분/물리/통계 등
        self.assertEqual(classify_liberal_subtype('기자', '일반화학'), '학문기초교양')

    def test_prefix_기컴_은_학문기초교양(self):
        # 컴퓨터 학문기초 — C언어/파이썬/엑셀 등
        self.assertEqual(classify_liberal_subtype('기컴', '파이썬프로그래밍입문'), '학문기초교양')

    def test_prefix_균_시작은_일반교양(self):
        # 균형교양은 '균자'/'균인' 등 1글자 더 붙는 코드도 있어 startswith로 매칭
        self.assertEqual(classify_liberal_subtype('균자', '환경과생활'), '일반교양')
        self.assertEqual(classify_liberal_subtype('균인', '문학산책'), '일반교양')

    def test_전공_prefix_는_None(self):
        # 컴정/컴공/반아는 전공이라 4종 분류 대상 외 (Course.category로 별도 관리)
        self.assertIsNone(classify_liberal_subtype('컴공', '자료구조'))
        self.assertIsNone(classify_liberal_subtype('컴정', 'C언어'))
        self.assertIsNone(classify_liberal_subtype('반아', '반도체개론'))

    def test_군사학_prefix_는_None(self):
        # 군*는 컴공 졸업요건 외 → 명시적 제외
        self.assertIsNone(classify_liberal_subtype('군과', '기초미적분학'))
        self.assertIsNone(classify_liberal_subtype('군인', '기초영어'))

    def test_예술_prefix_는_None(self):
        # 학칙 §6에 명시 없음 → 수동 보강 전까지 분류 보류
        self.assertIsNone(classify_liberal_subtype('예술', '미술감상'))

    def test_미지_prefix_는_None(self):
        # 매핑 룰에 없는 prefix는 None — import 시점에 경고로 표면화
        self.assertIsNone(classify_liberal_subtype('GEN', '대학영어'))
        self.assertIsNone(classify_liberal_subtype('', ''))

    def test_by_name_은_학문기초_필수5과목_확정(self):
        # graduation_requirements.md §3.3 명시 필수 5과목 — prefix가 어떻든 학문기초교양
        for n in ['미적분학1', '이산수학개론', '선형대수학개론', '공학수학1',
                  '통계학개론']:
            self.assertEqual(
                classify_liberal_subtype('기자', n), '학문기초교양',
                msg=f'{n} 이 학문기초교양으로 잡혀야 함',
            )

    def test_by_name_은_prefix_excluded_도_이김(self):
        # 만약 학교가 prefix를 군과로 분류해도 이름이 필수 7과목이면 학문기초교양으로 인정
        # (현재 PREFIX_EXCLUDED 체크보다 by_name 우선순위가 높음 — 학칙 명시 과목 보호)
        self.assertEqual(
            classify_liberal_subtype('군과', '미적분학1'), '학문기초교양',
        )

    def test_None_입력_안전(self):
        self.assertIsNone(classify_liberal_subtype(None, None))


# ===== 핵심교양 4영역 매핑 (#47 Phase 2) =====

class ClassifyCoreAreaTests(SimpleTestCase):
    """classify_core_area — 과목명 → 핵심교양 4영역 분류 (graduation_requirements.md §4.3)."""

    def test_영역별_대표_과목_매핑(self):
        # 4영역 각각 대표 과목 1개씩 — md §4.3.1~4.3.4
        self.assertEqual(classify_core_area('철학과 인간'), '역사와 철학')
        self.assertEqual(classify_core_area('민주주의와 현대사회'), '사회와 공동체')
        self.assertEqual(classify_core_area('예술과 창조성'), '문화와 예술')
        self.assertEqual(classify_core_area('인공지능입문'), '과학기술과 정보')

    def test_창업과_공동체는_사회와_공동체_단일(self):
        # md §4.3 ※ 주석상 (2)·(4) 양쪽 표기였으나 오타 확정 — 사회와 공동체로만 매핑
        self.assertEqual(classify_core_area('창업과 공동체'), '사회와 공동체')

    def test_외국인전용도_매핑됨(self):
        # 외국인학생전용도 같은 dict에 포함, 필터링 없이 매핑 반환
        self.assertEqual(classify_core_area('외국인학생을 위한 한국현대사'), '역사와 철학')
        self.assertEqual(classify_core_area('외국인학생을위한컴퓨터활용'), '과학기술과 정보')

    def test_미매핑_과목은_None(self):
        # 4영역 dict에 없는 과목 — 핵심교양인데 None 반환되면 호출자가 WARN 처리
        self.assertIsNone(classify_core_area('자료구조'))
        self.assertIsNone(classify_core_area('대학영어'))

    def test_None_빈문자열_안전(self):
        self.assertIsNone(classify_core_area(None))
        self.assertIsNone(classify_core_area(''))


# ===== CourseHistory.liberal_subtype 자동 동기화 (#47) =====

class CourseHistoryLiberalSubtypeSyncTests(TestCase):
    """CourseHistory 저장 시 course_code로 Course 찾아 liberal_subtype 복사 (#47)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='c@test.com', password='pw',
            name='이수자', major='컴퓨터공학전공', admission_year=2024,
        )
        # 4종 라벨된 Course 시드 — 학문기초교양 미적분학1
        cls.course = Course.objects.create(
            course_code='기자101', name='미적분학1',
            college='교양', category='학문기초교양', liberal_subtype='학문기초교양',
            credits=3, year_open=1, semester_open=1,
        )

    def test_liberal_subtype_자동_채움(self):
        # 호출자가 liberal_subtype 안 넘겨도 Course에서 자동 복사
        h = CourseHistory.objects.create(
            user=self.user, course_name='미적분학1', course_code='기자101',
            year=2026, semester=1, category='일반교양', credits=3,
        )
        self.assertEqual(h.liberal_subtype, '학문기초교양')

    def test_명시값은_안_덮음(self):
        # 호출자가 명시한 값은 우선 — Course가 '학문기초교양'이어도 명시값 유지
        h = CourseHistory.objects.create(
            user=self.user, course_name='미적분학1', course_code='기자101',
            year=2026, semester=1, category='일반교양', credits=3,
            liberal_subtype='일반교양',
        )
        self.assertEqual(h.liberal_subtype, '일반교양')

    def test_Course_미존재시_None_유지(self):
        # course_code가 DB에 없으면 null 그대로 (강제 fail 아님)
        h = CourseHistory.objects.create(
            user=self.user, course_name='없는과목', course_code='없음999',
            year=2026, semester=1, category='일반교양', credits=3,
        )
        self.assertIsNone(h.liberal_subtype)
        self.assertIsNone(h.core_area)

    def test_core_area_자동_채움(self):
        # 핵심교양 Course가 core_area 가지면 CourseHistory 저장 시 자동 복사 (#47 Phase 2)
        Course.objects.create(
            course_code='교선301', name='철학과 인간',
            college='교양', category='핵심교양', liberal_subtype='핵심교양',
            core_area='역사와 철학',
            credits=3, year_open=1, semester_open=1,
        )
        h = CourseHistory.objects.create(
            user=self.user, course_name='철학과 인간', course_code='교선301',
            year=2026, semester=1, category='일반교양', credits=3,
        )
        self.assertEqual(h.liberal_subtype, '핵심교양')
        self.assertEqual(h.core_area, '역사와 철학')


class CurriculumRecommendAPITests(APITestCase):
    """전체 커리큘럼 추천 API 통합 테스트 (spec 5.3.2, #25).

    POST + body 노브 (max_credits / category_weights / interest_weight /
    include_summer / include_winter / num_plans) 기반. 응답은
    {plans: [...], note?: 'insufficient_data'} 구조. 학기는 4 카테고리 키 분리.
    """
    url = '/api/v1/courses/recommend/curriculum/'

    # 학칙 7분류 응답 키 (#47 Phase 3)
    CAT_KEYS = (
        'major_required', 'major_elective',
        'liberal_common', 'liberal_core', 'liberal_foundation', 'liberal_general',
        'free_elective',
    )

    def setUp(self):
        # 사용자: 2학년 2학기, 데이터테크놀로지전공 (graduation 2027.8)
        self.user = _make_user()

        # 졸업요건 — 4 카테고리 모두 부족하도록 설정
        for cat, req in [('전공필수', 12), ('전공선택', 8), ('공통교양', 6), ('일반교양', 4)]:
            GraduationRequirement.objects.create(
                department='데이터테크놀로지전공', admission_year=2024,
                category=cat, required_credits=req, total_required=40,
            )

        # 후보 과목 — 학기/카테고리/학점 다양하게 풍성히 (변형 plan dedupe 회피용)
        # 1학기 (semester_open=1)
        _make_course(course_code='CSE2001', name='자료구조', category='전공필수',
                     year_open=2, semester_open=1, credits=3, tags=['IT/개발'])
        _make_course(course_code='CSE2003', name='운영체제', category='전공필수',
                     year_open=2, semester_open=1, credits=3, tags=['IT/개발'])
        _make_course(course_code='CSE3001', name='알고리즘', category='전공필수',
                     year_open=3, semester_open=1, credits=3, tags=['IT/개발'])
        _make_course(course_code='CSE3005', name='데이터베이스', category='전공선택',
                     year_open=3, semester_open=1, credits=3, tags=['IT/개발'])
        _make_course(course_code='GEN1001', name='글쓰기', category='공통교양',
                     department='교양', major='교양',
                     year_open=1, semester_open=1, credits=2, tags=[])
        _make_course(course_code='GEN1003', name='교양영어', category='일반교양',
                     department='교양', major='교양',
                     year_open=1, semester_open=1, credits=2, tags=[])
        # 2학기 (semester_open=2)
        _make_course(course_code='CSE2002', name='이산수학', category='전공필수',
                     year_open=2, semester_open=2, credits=3, tags=['IT/개발'])
        _make_course(course_code='CSE3002', name='컴퓨터구조', category='전공필수',
                     year_open=3, semester_open=2, credits=3, tags=['IT/개발'])
        _make_course(course_code='CSE3006', name='네트워크', category='전공선택',
                     year_open=3, semester_open=2, credits=3, tags=['IT/개발'])
        _make_course(course_code='GEN1002', name='발표와토론', category='공통교양',
                     department='교양', major='교양',
                     year_open=1, semester_open=2, credits=2, tags=[])

    # ── 헬퍼 ──
    def _all_course_codes(self, plans):
        """plan 묶음 안의 모든 추천 과목 course_code set."""
        codes = set()
        for plan in plans:
            for semester in plan['semesters']:
                for key in self.CAT_KEYS:
                    for c in semester.get(key, []):
                        codes.add(c['course_code'])
        return codes

    def _post(self, **body):
        self.client.force_authenticate(user=self.user)
        return self.client.post(self.url, body, format='json')

    # ── 기본 동작 ──
    def test_인증_없으면_401(self):
        res = self.client.post(self.url, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_GET은_405_POST_전용(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_plans_키로_응답_감쌈(self):
        res = self._post()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('plans', res.data)
        self.assertIsInstance(res.data['plans'], list)

    # ── #25 핵심 5 케이스 ──

    def test_plan_개수_2_5_범위(self):
        """spec: 최소 2안 이상, 최대 5안 이하. 데이터 부족 시 note 동반 1안 허용 (#25)"""
        res = self._post(num_plans=5)
        plans = res.data['plans']
        if 'note' in res.data:                  # fallback 동반이면 1안도 OK
            self.assertGreaterEqual(len(plans), 1)
        else:
            self.assertGreaterEqual(len(plans), 2)
        self.assertLessEqual(len(plans), 5)

    def test_plan들이_서로_다른지(self):
        """변형 plan은 학기-과목 시그니처가 서로 달라야 함 (가짜 복제 X, #25)"""
        res = self._post(num_plans=5)
        plans = res.data['plans']
        if len(plans) < 2:
            self.skipTest('plan이 충분히 생성되지 않음 (시드 부족)')
        signatures = []
        for p in plans:
            sig = tuple(
                (s['year'], s['semester'], tuple(sorted(
                    c['course_code']
                    for key in self.CAT_KEYS for c in s.get(key, [])
                )))
                for s in p['semesters']
            )
            signatures.append(sig)
        self.assertEqual(len(set(signatures)), len(plans))

    def test_학기별_4_카테고리_키_구조(self):
        """각 학기에 4 카테고리 키 모두 존재 + courses 단일 배열 키 없음 (#25)"""
        res = self._post()
        for plan in res.data['plans']:
            for sem in plan['semesters']:
                for key in self.CAT_KEYS:
                    self.assertIn(key, sem)
                    self.assertIsInstance(sem[key], list)
                self.assertIn('year', sem)
                self.assertIn('semester', sem)
                self.assertNotIn('courses', sem)        # 옛 단일 배열 키 잔재 X

    def test_include_summer_토글(self):
        """include_summer=True일 때만 하계(semester_open=3) 과목 등장 (#25)"""
        _make_course(
            course_code='CSE3091', name='알고리즘심화특강',
            category='전공선택', year_open=3, semester_open=3,    # 하계
            credits=2, tags=['IT/개발'],
        )
        # 토글 off → 등장 X
        res_off = self._post(include_summer=False)
        self.assertNotIn('CSE3091', self._all_course_codes(res_off.data['plans']))
        # 토글 on → 등장 O
        res_on = self._post(include_summer=True)
        self.assertIn('CSE3091', self._all_course_codes(res_on.data['plans']))

    def test_include_winter_토글(self):
        """include_winter=True일 때만 동계(semester_open=4) 과목 등장 (#25).

        동계 슬롯(sem=4)은 2학기 직후에 옴 — 마지막 정규학기가 2학기면
        루프 종료로 진입 불가. graduation을 미래로 밀어 동계 도달 보장.
        """
        _make_course(
            course_code='CSE3092', name='데이터분석실무특강',
            category='전공선택', year_open=3, semester_open=4,    # 동계
            credits=2, tags=['IT/개발'],
        )
        self.user.graduation_year = 2028
        self.user.save()

        res_off = self._post(include_winter=False)
        self.assertNotIn('CSE3092', self._all_course_codes(res_off.data['plans']))
        res_on = self._post(include_winter=True)
        self.assertIn('CSE3092', self._all_course_codes(res_on.data['plans']))

    def test_관심사_반영_plan(self):
        """관심사 등록 + interest_weight 가중 시 관심사 매칭 과목 추천 결과에 포함 (#25)"""
        self.user.interests.create(category='IT/개발')
        res = self._post(interest_weight=2.0)
        codes = self._all_course_codes(res.data['plans'])
        # IT/개발 태그 달린 후보 중 최소 하나는 추천 등장
        it_tagged = {'CSE2001', 'CSE2002', 'CSE2003', 'CSE3001', 'CSE3002'}
        self.assertTrue(it_tagged & codes, msg='관심사 매칭 과목이 plan에 하나도 없음')

    # ── 추가 검증 ──

    def test_max_credits_노브가_학기_학점_상한에_반영(self):
        """body.max_credits 작게 보내면 학기당 학점 합이 그 이하 (변형 오프셋 포함)"""
        res = self._post(max_credits=6, num_plans=2)
        for plan in res.data['plans']:
            for sem in plan['semesters']:
                total = sum(
                    c['credits']
                    for key in self.CAT_KEYS for c in sem[key]
                )
                self.assertLessEqual(total, plan['max_credits'])

    def test_fallback_데이터_부족시_note_insufficient_data(self):
        """후보 없는 사용자 → plan 부족 + note='insufficient_data' (#25 fallback 정책)"""
        empty_user = _make_user(email='empty@x.com', major='없는학과')
        self.client.force_authenticate(user=empty_user)
        res = self.client.post(self.url, {}, format='json')
        # 후보 0이면 plan 0~1개. plan < 2면 note 필수
        if len(res.data['plans']) < 2:
            self.assertEqual(res.data.get('note'), 'insufficient_data')

    def test_body_없이_호출해도_기본값으로_동작(self):
        """노브 전체 미지정 시 기본값으로 polite 200 응답"""
        res = self._post()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('plans', res.data)

    def test_category_weights_7종_키_호환(self):
        # #47 Phase 3 — category_weights가 7분류 키(전공필수/전공선택/공통/핵심/학문기초/일반/자유선택)로 동작
        self.client.force_authenticate(user=self.user)
        # 공통교양 2.0배 가중 + 일반교양 0.5배 — 공통교양 과목이 liberal_common에 더 많이 잡혀야 자연스러움
        res = self.client.post(self.url, {
            'category_weights': {'공통교양': 2.0, '일반교양': 0.5},
            'num_plans': 1,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        plan = res.data['plans'][0]
        # 응답 구조 살아있음 (옛 4분류만 인식해서 빈 응답 나오던 결함 회귀 방지)
        for s in plan['semesters']:
            for k in self.CAT_KEYS:
                self.assertIn(k, s)

    # ── #113 liberal_core core_area 노출 ──

    def test_liberal_core_응답에_core_area_노출_113(self):
        """핵심교양 추천 item에 core_area 필드 노출 — 4영역 식별 가능 (#113)"""
        # 핵심교양 4영역 GR 추가 + 영역별 후보 1개씩
        for area in ('역사와 철학', '사회와 공동체', '문화와 예술', '과학기술과 정보'):
            GraduationRequirement.objects.create(
                department='데이터테크놀로지전공', admission_year=2024,
                category='핵심교양', liberal_subtype='핵심교양', core_area=area,
                required_credits=3, total_required=40,
            )
        _make_course(course_code='COR1001', name='서양사', category='핵심교양',
                     liberal_subtype='핵심교양', core_area='역사와 철학',
                     department='교양', major='교양',
                     year_open=1, semester_open=1, credits=3)
        _make_course(course_code='COR1002', name='시민사회', category='핵심교양',
                     liberal_subtype='핵심교양', core_area='사회와 공동체',
                     department='교양', major='교양',
                     year_open=1, semester_open=1, credits=3)
        _make_course(course_code='COR1003', name='현대미술', category='핵심교양',
                     liberal_subtype='핵심교양', core_area='문화와 예술',
                     department='교양', major='교양',
                     year_open=1, semester_open=2, credits=3)
        _make_course(course_code='COR1004', name='AI개론', category='핵심교양',
                     liberal_subtype='핵심교양', core_area='과학기술과 정보',
                     department='교양', major='교양',
                     year_open=1, semester_open=2, credits=3)

        res = self._post(num_plans=1)
        plan = res.data['plans'][0]
        core_areas_seen = set()
        for sem in plan['semesters']:
            for item in sem.get('liberal_core', []):
                # 모든 liberal_core item에 core_area 키 존재
                self.assertIn('core_area', item, msg=f'liberal_core item에 core_area 누락: {item}')
                # 4영역 중 하나여야 함 (null이면 결함)
                self.assertIn(item['core_area'], {
                    '역사와 철학', '사회와 공동체', '문화와 예술', '과학기술과 정보',
                })
                core_areas_seen.add(item['core_area'])
        # 최소 한 영역 이상 추천에 잡혀야 정상 동작 (4학기·풍성한 후보 기준)
        self.assertGreater(len(core_areas_seen), 0)

    def test_비핵심교양_카테고리는_core_area_null_113(self):
        """전공필수·전공선택·일반교양 등은 core_area=null로 응답 (#113)"""
        res = self._post(num_plans=1)
        plan = res.data['plans'][0]
        non_core_keys = ('major_required', 'major_elective', 'liberal_general', 'free_elective')
        for sem in plan['semesters']:
            for key in non_core_keys:
                for item in sem.get(key, []):
                    self.assertIn('core_area', item)
                    self.assertIsNone(item['core_area'])

    # ── #112 전공선택 쿼터 (학기당 최소 6학점) ──

    def test_전공선택_short_시_plan에_최소_1건_노출_112(self):
        """#112 결함 J — 전공선택 잔여 > 0인데 plan에 0건 나오는 갭 해소.

        setUp: 전공선택 8학점 필요, 후보 CSE3005(1학기) + CSE3006(2학기) 각 3학점.
        쿼터 적용 시 학기마다 매칭 분반 1건씩 강제 추천 → plan 전체에서 ≥ 1건.
        """
        res = self._post()
        plans = res.data['plans']
        self.assertGreater(len(plans), 0)
        for plan in plans:
            codes = set()
            for sem in plan['semesters']:
                for c in sem.get('major_elective', []):
                    codes.add(c['course_code'])
            self.assertGreater(
                len(codes), 0,
                msg=f"plan {plan['plan_number']}: 전공선택 0건 (결함 J 재발)",
            )

    def test_전공선택_잔여_3학점이면_quota도_3학점으로_clamp_112(self):
        """전공선택 required 3학점만이면 target = min(6, 3) = 3 → 한 학기 3학점 채우고 끝"""
        GraduationRequirement.objects.filter(category='전공선택').delete()
        GraduationRequirement.objects.create(
            department='데이터테크놀로지전공', admission_year=2024,
            category='전공선택', required_credits=3, total_required=25,
        )
        res = self._post(num_plans=1)
        plan = res.data['plans'][0]
        elective_credits = [
            sum(c['credits'] for c in sem.get('major_elective', []))
            for sem in plan['semesters']
        ]
        # 첫 학기에 quota로 3학점 들어가야 함. 그 다음 학기는 잔여 0이라 quota X
        # (score 경쟁만 — 점수 낮으면 안 들어오는 게 정상)
        self.assertIn(3, elective_credits, msg=f'elective_credits={elective_credits}')
        # 누적은 quota target과 일치 (3학점) — score 경쟁으로 추가 들어와도 무방하니 >=3
        self.assertGreaterEqual(sum(elective_credits), 3)

    def test_전공선택_완료시_quota_skip_other_categories_정상_112(self):
        """전공선택 잔여 0이면 quota phase skip — 다른 부족 카테고리 추천에 영향 없음"""
        # 전공선택 required 8학점 이미 이수
        for code, name, credits in [('OLD3001', '과거전선1', 4), ('OLD3002', '과거전선2', 4)]:
            CourseHistory.objects.create(
                user=self.user, course_name=name, course_code=code,
                year=2024, semester=1, grade_received='A',
                category='전공선택', credits=credits,
            )
        res = self._post(num_plans=1)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        plan = res.data['plans'][0]
        # 부족 남은 전공필수(12) / 공통교양(6) / 일반교양(4)이 정상 채워져야 함
        total_required = sum(
            sum(c['credits'] for c in sem.get('major_required', []))
            for sem in plan['semesters']
        )
        total_common = sum(
            sum(c['credits'] for c in sem.get('liberal_common', []))
            for sem in plan['semesters']
        )
        self.assertGreater(total_required, 0)
        self.assertGreater(total_common, 0)


# 졸업까지 진척도(%) API 테스트는 dashboard 앱으로 이전됨
# (spec 6.10 — 단독 엔드포인트 제거, dashboard 응답으로 통합).
# → dashboard/tests.py DashboardGraduationProgressTests 참조.


# ===== 점수 계산 함수 단위 테스트 (spec 5.3.1, DB 안 띄움) =====

class CalculateScoreTests(SimpleTestCase):
    """`calculate_recommendation_score`의 분기별 가감 검증.

    Course는 ORM 인스턴스만 만들고 save() 안 함 → DB 의존 없음.
    `_score` 헬퍼의 기본값은 모든 분기를 미발동시켜 100점이 나오도록 설계됨.
    각 테스트는 하나의 분기만 발동시켜 그 항목의 가감산만 검증한다.
    """

    def _course(self, **kwargs):
        defaults = dict(
            course_code='TEST001',
            name='테스트과목',
            category='전공선택',
            year_open=1,
            semester_open=2,
            major='기타전공',
        )
        defaults.update(kwargs)
        return Course(**defaults)

    def _score(self, course, **overrides):
        """기본 baseline — 사용자 2학년 1학기, 컴공.
        course 기본(전공선택 1-2, 타과)과 합쳐서 모든 분기 미발동 → 100점."""
        defaults = dict(
            user_grade=2,
            user_semester=1,
            user_major='컴퓨터공학전공',
            user_interest_categories=set(),
            short_categories=set(),
            completed_course_ids=set(),
            course_prerequisite_ids=set(),
            course_tags=[],
        )
        defaults.update(overrides)
        return calculate_recommendation_score(course, **defaults)

    def test_baseline은_100점(self):
        self.assertEqual(self._score(self._course()), 100)

    def test_관심사_매칭시_BONUS_INTEREST_MATCH_가산(self):
        course = self._course()
        score = self._score(
            course,
            user_interest_categories={'IT/개발'},
            course_tags=['IT/개발'],
        )
        self.assertEqual(score, 100 + BONUS_INTEREST_MATCH)

    def test_졸업요건_부족_카테고리면_BONUS_CATEGORY_SHORT_가산(self):
        # short_categories는 (category, liberal_subtype, core_area) 트리플 set — 전공은 둘 다 None (#47 Phase 2)
        course = self._course(category='전공선택')
        score = self._score(course, short_categories={('전공선택', None, None)})
        self.assertEqual(score, 100 + BONUS_CATEGORY_SHORT)

    def test_교양_4종은_동일_category여도_별개_short_키(self):
        # 핵심교양만 부족하고 일반교양은 다 채워진 상태 — category='일반교양' 같지만
        # liberal_subtype 으로 key가 달라 일반교양 과목은 가산점 0 (#47)
        nuclear = self._course(category='핵심교양', liberal_subtype='핵심교양')
        general = self._course(category='일반교양', liberal_subtype='일반교양')
        short_keys = {('핵심교양', '핵심교양', None)}
        # 핵심교양은 학칙 의무 영역 → BONUS_DESIGNATED_REQUIRED(+15) 가산.
        # 헬퍼 디폴트(year_open=1, user_grade=2) 가 backlog 조건 맞아 BONUS_BACKLOG_REQUIRED(+10)도 발동.
        self.assertEqual(
            self._score(nuclear, short_categories=short_keys),
            100 + BONUS_CATEGORY_SHORT + BONUS_DESIGNATED_REQUIRED + BONUS_BACKLOG_REQUIRED,
        )
        self.assertEqual(self._score(general, short_categories=short_keys), 100)

    def test_핵심교양_4영역은_별개_short_키(self):
        # 역사·철학만 부족, 사회·공동체는 충족인 상태 — 같은 liberal_subtype='핵심교양' 안에서도
        # core_area로 key가 갈리므로 사회·공동체 과목은 가산점 0 (#47 Phase 2)
        history = self._course(category='핵심교양', liberal_subtype='핵심교양', core_area='역사와 철학')
        society = self._course(category='핵심교양', liberal_subtype='핵심교양', core_area='사회와 공동체')
        short_keys = {('핵심교양', '핵심교양', '역사와 철학')}
        # 둘 다 BONUS_DESIGNATED_REQUIRED(+15) + BONUS_BACKLOG_REQUIRED(+10) 기본.
        # 역사·철학만 short이라 그 영역 과목에만 BONUS_CATEGORY_SHORT(+15) 추가.
        self.assertEqual(
            self._score(history, short_categories=short_keys),
            100 + BONUS_CATEGORY_SHORT + BONUS_DESIGNATED_REQUIRED + BONUS_BACKLOG_REQUIRED,
        )
        self.assertEqual(
            self._score(society, short_categories=short_keys),
            100 + BONUS_DESIGNATED_REQUIRED + BONUS_BACKLOG_REQUIRED,
        )

    def test_전공필수면_BONUS_MAJOR_REQUIRED_가산(self):
        # 다른 분기 회피 위해 year_open=2, semester_open=2 (학년 같음, 학기 다름)
        course = self._course(category='전공필수', year_open=2, semester_open=2)
        score = self._score(course)
        self.assertEqual(score, 100 + BONUS_MAJOR_REQUIRED)

    def test_교양필수면_BONUS_DESIGNATED_REQUIRED_가산(self):
        course = self._course(category='공통교양', year_open=2, semester_open=2)
        score = self._score(course)
        self.assertEqual(score, 100 + BONUS_DESIGNATED_REQUIRED)

    def test_학년_학기_정확히_일치시_BONUS_GRADE_SEMESTER_MATCH_가산(self):
        course = self._course(category='전공선택', year_open=2, semester_open=1)
        score = self._score(course)
        self.assertEqual(score, 100 + BONUS_GRADE_SEMESTER_MATCH)

    def test_권장_학년_초과시_PENALTY_GRADE_EXCEEDED_감점(self):
        # user 2-1, course 4-1 → 권장 학년 > 사용자 학년 → -10
        course = self._course(category='전공선택', year_open=4, semester_open=1)
        score = self._score(course)
        self.assertEqual(score, 100 - PENALTY_GRADE_EXCEEDED)

    def test_밀린_전공필수면_BACKLOG_가산_포함(self):
        # course 1-1, user 2-1 → 권장 학년 < 사용자 + 전필 → BACKLOG +10
        # 전공필수 자체 가산 +25도 함께 발동
        course = self._course(category='전공필수', year_open=1, semester_open=1)
        score = self._score(course)
        self.assertEqual(score, 100 + BONUS_MAJOR_REQUIRED + BONUS_BACKLOG_REQUIRED)

    def test_밀린_교양필수면_BACKLOG_가산_포함(self):
        # 교양필수 자체 가산 +15도 함께 발동
        course = self._course(category='공통교양', year_open=1, semester_open=1)
        score = self._score(course)
        self.assertEqual(score, 100 + BONUS_DESIGNATED_REQUIRED + BONUS_BACKLOG_REQUIRED)

    def test_밀린_전공선택은_BACKLOG_가산_안받음(self):
        """BACKLOG_REQUIRED_CATEGORIES = ('전공필수','공통교양','핵심교양','학문기초교양') — 선택과목은 제외"""
        course = self._course(category='전공선택', year_open=1, semester_open=1)
        score = self._score(course)
        self.assertEqual(score, 100)

    # ----- year_open=0 (전학년) sentinel — 학년 관련 가감산 모두 skip (#36) -----

    def test_전학년_과목은_학년_비교_가감_모두_skip(self):
        """year_open=0 — 어떤 학년 학생에게도 중립. 학기 다르고 BACKLOG 대상 아니므로 100."""
        course = self._course(category='전공선택', year_open=0, semester_open=2)
        score = self._score(course)
        self.assertEqual(score, 100)

    def test_전학년_전공필수는_BACKLOG_가산_안받고_전필_자체_가산만(self):
        """전학년 전공필수 = '밀린 필수' 의미가 아님. BACKLOG +10 발동 X, 전필 +25만."""
        course = self._course(category='전공필수', year_open=0, semester_open=2)
        score = self._score(course)
        self.assertEqual(score, 100 + BONUS_MAJOR_REQUIRED)

    def test_전학년_같은_학기여도_GRADE_SEMESTER_MATCH_가산_없음(self):
        """학년 비교 자체 skip이라 매칭 조건도 발동 X."""
        course = self._course(category='전공선택', year_open=0, semester_open=1)
        score = self._score(course)
        self.assertEqual(score, 100)

    def test_동일학과_선수과목_미이수여도_점수식은_감점_없음(self):
        # #47 7번 — 선수 hard filter는 호출 전에 적용. 점수식 자체에는 감점 없음.
        course = self._course(category='전공선택', major='컴퓨터공학전공')
        score = self._score(
            course,
            user_major='컴퓨터공학전공',
            course_prerequisite_ids={101},  # 선수과목 있음
            completed_course_ids=set(),     # 미이수 (실제 호출 전에 후보에서 제외됨)
        )
        self.assertEqual(score, 100)

    def test_동일학과_선수과목_이수했으면_감점_없음(self):
        course = self._course(category='전공선택', major='컴퓨터공학전공')
        score = self._score(
            course,
            user_major='컴퓨터공학전공',
            course_prerequisite_ids={101},
            completed_course_ids={101},  # 이수 완료
        )
        self.assertEqual(score, 100)

    def test_타과생은_선수과목_미이수여도_감점_없음(self):
        """spec 5.3.1 정책: 타과생은 선수과목 제한 면제"""
        course = self._course(category='전공선택', major='컴퓨터공학전공')
        score = self._score(
            course,
            user_major='국문학과',  # 타과
            course_prerequisite_ids={101},
            completed_course_ids=set(),
        )
        self.assertEqual(score, 100)


class ImportPrerequisitesFromCsvTests(TestCase):
    """선수과목 csv import 명령 (graduation_requirements.md §7)"""

    def test_매칭_성공_과_미커버_skip(self):
        from io import StringIO
        from django.core.management import call_command
        import tempfile, os
        # DB 시드: 후수·선수 둘 다 있는 페어와 후수만 있는 페어
        c_lang = Course.objects.create(
            course_code='기컴101', name='C언어', college='교양',
            category='학문기초교양', credits=3, year_open=1, semester_open=1,
        )
        oop1 = Course.objects.create(
            course_code='컴공220', name='객체지향프로그래밍1', college='반도체ICT',
            major='컴퓨터공학전공', category='전공선택', credits=3, year_open=2, semester_open=1,
        )
        # 후수 DB 미커버 (알고리즘은 DB에 없음)

        csv_content = (
            '학과명|후수교과코드|후수교과목명|선수교과코드|선수교과목명|선·후수지정연도\n'
            '컴공|JEJ02220|객체지향프로그래밍1|JEJ02211|C언어|2010\n'
            '컴공|JEJ02316|알고리즘|JEJ02209|자료구조|2006\n'
        )
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.csv', encoding='utf-8') as f:
            f.write(csv_content)
            tmp = f.name
        try:
            out = StringIO()
            call_command('import_prerequisites_from_csv', tmp, stdout=out)
            # 객체지향프로그래밍1 ← C언어 1건 박힘
            self.assertEqual(CoursePrerequisite.objects.count(), 1)
            self.assertEqual(CoursePrerequisite.objects.first().course, oop1)
            self.assertEqual(CoursePrerequisite.objects.first().prerequisite, c_lang)
            # 알고리즘은 후수 DB 미커버 → skip + WARN 출력
            output = out.getvalue()
            self.assertIn('skip 1', output)
            self.assertIn('알고리즘', output)
        finally:
            os.unlink(tmp)


class DuplicateNameExclusionTests(APITestCase):
    """같은 이름 다른 코드 추천 후보 제외 (학칙 §9 동일과목, #47)"""
    url = '/api/v1/courses/recommend/next/'

    def test_같은_이름_다른_코드는_이수했으면_제외(self):
        # 컴공 학생이 컴정101 C언어 이수 → 기컴101 C언어도 추천 후보에서 빠져야 함
        user = _make_user()
        major_c = _make_course(
            course_code='컴정101', name='C언어', major='데이터테크놀로지전공',
            category='전공필수', year_open=1, semester_open=1,
        )
        liberal_c = _make_course(
            course_code='기컴101', name='C언어',  # 같은 이름, 다른 코드
            category='학문기초교양', year_open=1, semester_open=1,
        )
        CourseHistory.objects.create(
            user=user, course_name='C언어', course_code='컴정101',
            year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
        )
        self.client.force_authenticate(user=user)
        res = self.client.get(self.url)
        codes = {item['course_code'] for item in res.data}
        # 컴정101 이미 이수라 제외 + 기컴101은 동일 이름이라 제외
        self.assertNotIn('컴정101', codes)
        self.assertNotIn('기컴101', codes)


class ForeignMajorVersionExclusionTests(APITestCase):
    """학과별 교양 블랙리스트 — 컴공 전공필수 이름의 교양 버전 차단 (#47 A2)"""
    url = '/api/v1/courses/recommend/next/'

    def test_컴공_학생은_안_들었어도_기컴_C언어_제외(self):
        user = _make_user(major='컴퓨터공학전공')
        # 전공 버전(컴정101) + 교양 버전(기컴101) 둘 다 DB, 학생은 아직 안 들음
        _make_course(
            course_code='컴정101', name='C언어', major='컴퓨터공학전공',
            category='전공필수', year_open=1, semester_open=1,
        )
        _make_course(
            course_code='기컴101', name='C언어', major=None,
            category='학문기초교양', year_open=1, semester_open=1,
        )
        self.client.force_authenticate(user=user)
        res = self.client.get(self.url)
        codes = {item['course_code'] for item in res.data}
        # 전공 버전은 노출, 교양 버전은 차단
        self.assertIn('컴정101', codes)
        self.assertNotIn('기컴101', codes)


class TagRulesTests(SimpleTestCase):
    """Course.tags 룰 매핑 검증 (#48)"""

    def _course(self, name='', major=''):
        from courses.models import Course
        return Course(name=name, major=major)

    def test_컴공_전공_과목은_IT개발_default(self):
        from courses.tag_rules import infer_tags
        tags = infer_tags(self._course(name='임의이름', major='컴퓨터공학전공'))
        self.assertIn('IT/개발', tags)

    def test_AI_키워드_매칭(self):
        from courses.tag_rules import infer_tags
        tags = infer_tags(self._course(name='AI사회와인간'))
        self.assertIn('IT/개발', tags)
        self.assertIn('연구/R&D', tags)

    def test_채플은_기타(self):
        # 12 카테고리에 종교 없어 '기타'로 매핑
        from courses.tag_rules import infer_tags
        tags = infer_tags(self._course(name='채플'))
        self.assertEqual(tags, {'기타'})

    def test_매칭_없으면_빈_set(self):
        from courses.tag_rules import infer_tags
        tags = infer_tags(self._course(name='용인학'))  # 학교 특화 — 룰 매칭 없음
        self.assertEqual(tags, set())


class BackfillCourseTagsTests(TestCase):
    """backfill_course_tags 명령 (#48)"""

    def test_빈_tags만_채움_기존은_보호(self):
        from io import StringIO
        from django.core.management import call_command
        from courses.models import Course
        c_empty = Course.objects.create(
            course_code='TEST001', name='AI개론', college='X',
            category='전공선택', credits=3, year_open=1, semester_open=1,
        )
        c_manual = Course.objects.create(
            course_code='TEST002', name='AI개론2', college='X',
            category='전공선택', credits=3, year_open=1, semester_open=1,
            tags=['디자인'],  # 수동 보강 가정
        )
        call_command('backfill_course_tags', stdout=StringIO())
        c_empty.refresh_from_db()
        c_manual.refresh_from_db()
        self.assertIn('IT/개발', c_empty.tags)
        self.assertEqual(c_manual.tags, ['디자인'])  # 보호됨

    def test_overwrite_옵션은_기존도_덮어씀(self):
        from io import StringIO
        from django.core.management import call_command
        from courses.models import Course
        c = Course.objects.create(
            course_code='TEST003', name='AI개론', college='X',
            category='전공선택', credits=3, year_open=1, semester_open=1,
            tags=['디자인'],  # 잘못된 수동 태그 가정
        )
        call_command('backfill_course_tags', '--overwrite', stdout=StringIO())
        c.refresh_from_db()
        self.assertIn('IT/개발', c.tags)
        self.assertNotIn('디자인', c.tags)


# ─── #149: 분반 단위 검색 API (GET /api/v1/courses/offerings/) ──────────

class CourseOfferingSearchAPITests(APITestCase):
    """GET /api/v1/courses/offerings/ — 분반 단위 검색 응답 (#149).

    한 카드 = 한 offering. 응답의 `id`는 그대로 `POST /accounts/current-courses/`
    의 `offering_id`로 보낼 수 있음.
    """

    url = '/api/v1/courses/offerings/'

    def setUp(self):
        self.user = User.objects.create_user(email='os@mju.ac.kr', password='Pwd@1234')
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.client.force_authenticate(user=self.user)

        # 카탈로그: 2개 강의, 각 1~2 분반
        self.c_ai = Course.objects.create(
            course_code='ICTC202', name='AI프로그래밍',
            college='반도체·ICT대학', department='컴퓨터정보통신공학부',
            major='컴퓨터공학전공',
            category='전공선택', credits=3, year_open=3, semester_open=1,
            professor='김상귀',
        )
        self.off_ai_1 = CourseOffering.objects.create(
            course=self.c_ai, year=2026, semester=1, section_no='0727',
            professor='김상귀',
        )
        CourseSchedule.objects.create(
            course=self.c_ai, offering=self.off_ai_1,
            day_of_week='수', start_time=time(14, 0), end_time=time(16, 50),
            building='', room='Y5411',
        )
        # 같은 강의 다른 분반 (다른 학기)
        self.off_ai_2 = CourseOffering.objects.create(
            course=self.c_ai, year=2025, semester=2, section_no='0501',
            professor='홍길동',
        )
        CourseSchedule.objects.create(
            course=self.c_ai, offering=self.off_ai_2,
            day_of_week='화', start_time=time(9, 0), end_time=time(11, 50),
            building='', room='Y5301',
        )

        # 다른 강의 1개
        self.c_oop = Course.objects.create(
            course_code='ICTC201', name='객체지향프로그래밍',
            college='반도체·ICT대학', department='컴퓨터정보통신공학부',
            major='컴퓨터공학전공',
            category='전공선택', credits=3, year_open=2, semester_open=1,
            professor='이교수',
        )
        self.off_oop_1 = CourseOffering.objects.create(
            course=self.c_oop, year=2026, semester=1, section_no='0710',
            professor='이교수',
        )
        CourseSchedule.objects.create(
            course=self.c_oop, offering=self.off_oop_1,
            day_of_week='월', start_time=time(10, 0), end_time=time(12, 50),
            building='', room='Y5212',
        )

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 401)

    def test_no_filter_returns_all_offerings(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        items = res.data['results'] if isinstance(res.data, dict) else res.data
        self.assertEqual(len(items), 3)

    def test_response_shape_has_required_fields(self):
        res = self.client.get(self.url, {'query': 'AI프로그래밍'})
        self.assertEqual(res.status_code, 200)
        items = res.data['results'] if isinstance(res.data, dict) else res.data
        self.assertGreater(len(items), 0)
        item = items[0]
        for k in ('id', 'year', 'semester', 'section_no',
                  'course_code', 'name', 'college', 'department', 'major',
                  'category', 'credits', 'professor', 'schedules'):
            self.assertIn(k, item, msg=f'필드 누락: {k}')
        # college/dept/major Figma 카드 3단 표시
        self.assertEqual(item['college'], '반도체·ICT대학')
        self.assertEqual(item['department'], '컴퓨터정보통신공학부')
        self.assertEqual(item['major'], '컴퓨터공학전공')
        # schedules nested
        self.assertEqual(len(item['schedules']), 1)
        sch = item['schedules'][0]
        for k in ('day_of_week', 'start_time', 'end_time', 'room'):
            self.assertIn(k, sch)

    def test_query_filter_matches_course_name(self):
        res = self.client.get(self.url, {'query': 'AI'})
        items = res.data['results'] if isinstance(res.data, dict) else res.data
        names = {i['name'] for i in items}
        self.assertEqual(names, {'AI프로그래밍'})

    def test_query_filter_matches_course_code(self):
        res = self.client.get(self.url, {'query': 'ICTC201'})
        items = res.data['results'] if isinstance(res.data, dict) else res.data
        codes = {i['course_code'] for i in items}
        self.assertEqual(codes, {'ICTC201'})

    def test_query_filter_matches_professor(self):
        res = self.client.get(self.url, {'query': '홍길동'})
        items = res.data['results'] if isinstance(res.data, dict) else res.data
        # 2025-2학기 AI프로그래밍 (off_ai_2)에만 홍길동
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['professor'], '홍길동')

    def test_year_semester_filter(self):
        res = self.client.get(self.url, {'year': 2026, 'semester': 1})
        items = res.data['results'] if isinstance(res.data, dict) else res.data
        self.assertEqual(len(items), 2)  # AI 0727 + OOP 0710
        for it in items:
            self.assertEqual(it['year'], 2026)
            self.assertEqual(it['semester'], 1)
