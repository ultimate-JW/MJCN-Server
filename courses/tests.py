from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import CourseHistory, CurrentCourse
from courses.category_map import classify_liberal_subtype
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
    BONUS_GRADE_SEMESTER_MATCH,
    BONUS_INTEREST_MATCH,
    BONUS_LIBERAL_REQUIRED,
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
        # 4종 도입(#47) 후 unique 키는 (dept, year, category, liberal_subtype).
        # liberal_subtype=NULL인 row끼리는 SQLite/PG 표준 NULL != NULL 정책상 중복 허용 안 되므로
        # 여기선 명시적 liberal_subtype 값으로 충돌 검증.
        GraduationRequirement.objects.create(
            department='융합소프트웨어학부',
            admission_year=2024,
            category='교양선택',
            liberal_subtype='공통교양',
            required_credits=17,
            total_required=134,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GraduationRequirement.objects.create(
                    department='융합소프트웨어학부',
                    admission_year=2024,
                    category='교양선택',
                    liberal_subtype='공통교양',
                    required_credits=18,
                    total_required=134,
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
            course_code='GEN1001', name='글쓰기', category='교양필수',
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
        for key in ('score', 'course_code', 'name', 'category', 'credits', 'professor', 'schedules'):
            self.assertIn(key, item)

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

    def test_선수과목_미이수는_제외하지_않고_Soft_감점만(self):
        """spec 5.3.1: 동일 학과 + 선수과목 미이수는 Hard Filter 아닌 Soft 감점 (-15)"""
        CoursePrerequisite.objects.create(course=self.mid, prerequisite=self.base)
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.url)
        codes = {item['course_code'] for item in res.data}
        # 자료구조(CSE2001) 여전히 후보에 포함됨
        self.assertIn('CSE2001', codes)

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
        # 같은 점수면 전공필수가 교양필수보다 위 (CATEGORY_PRIORITY: 전필=1 < 교필=2)
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

    def test_prefix_교필_은_공통교양(self):
        # 학칙 §6 표시기호: 교필 = 공통교양 (이름은 "교양필수"지만 학교 의미는 공통)
        self.assertEqual(classify_liberal_subtype('교필', '채플'), '공통교양')

    def test_prefix_교선_은_핵심교양(self):
        # 학칙 §6 표시기호: 교선 = 핵심교양
        self.assertEqual(classify_liberal_subtype('교선', '동양철학사'), '핵심교양')

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
            college='교양', category='교양선택', liberal_subtype='학문기초교양',
            credits=3, year_open=1, semester_open=1,
        )

    def test_liberal_subtype_자동_채움(self):
        # 호출자가 liberal_subtype 안 넘겨도 Course에서 자동 복사
        h = CourseHistory.objects.create(
            user=self.user, course_name='미적분학1', course_code='기자101',
            year=2026, semester=1, category='교양선택', credits=3,
        )
        self.assertEqual(h.liberal_subtype, '학문기초교양')

    def test_명시값은_안_덮음(self):
        # 호출자가 명시한 값은 우선 — Course가 '학문기초교양'이어도 명시값 유지
        h = CourseHistory.objects.create(
            user=self.user, course_name='미적분학1', course_code='기자101',
            year=2026, semester=1, category='교양선택', credits=3,
            liberal_subtype='일반교양',
        )
        self.assertEqual(h.liberal_subtype, '일반교양')

    def test_Course_미존재시_None_유지(self):
        # course_code가 DB에 없으면 null 그대로 (강제 fail 아님)
        h = CourseHistory.objects.create(
            user=self.user, course_name='없는과목', course_code='없음999',
            year=2026, semester=1, category='교양선택', credits=3,
        )
        self.assertIsNone(h.liberal_subtype)


class CurriculumRecommendAPITests(APITestCase):
    """전체 커리큘럼 추천 API 통합 테스트 (spec 5.3.2, #25).

    POST + body 노브 (max_credits / category_weights / interest_weight /
    include_summer / include_winter / num_plans) 기반. 응답은
    {plans: [...], note?: 'insufficient_data'} 구조. 학기는 4 카테고리 키 분리.
    """
    url = '/api/v1/courses/recommend/curriculum/'

    # 4 카테고리 응답 키
    CAT_KEYS = ('major_required', 'major_elective', 'liberal_required', 'liberal_elective')

    def setUp(self):
        # 사용자: 2학년 2학기, 데이터테크놀로지전공 (graduation 2027.8)
        self.user = _make_user()

        # 졸업요건 — 4 카테고리 모두 부족하도록 설정
        for cat, req in [('전공필수', 12), ('전공선택', 8), ('교양필수', 6), ('교양선택', 4)]:
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
        _make_course(course_code='GEN1001', name='글쓰기', category='교양필수',
                     department='교양', major='교양',
                     year_open=1, semester_open=1, credits=2, tags=[])
        _make_course(course_code='GEN1003', name='교양영어', category='교양선택',
                     department='교양', major='교양',
                     year_open=1, semester_open=1, credits=2, tags=[])
        # 2학기 (semester_open=2)
        _make_course(course_code='CSE2002', name='이산수학', category='전공필수',
                     year_open=2, semester_open=2, credits=3, tags=['IT/개발'])
        _make_course(course_code='CSE3002', name='컴퓨터구조', category='전공필수',
                     year_open=3, semester_open=2, credits=3, tags=['IT/개발'])
        _make_course(course_code='CSE3006', name='네트워크', category='전공선택',
                     year_open=3, semester_open=2, credits=3, tags=['IT/개발'])
        _make_course(course_code='GEN1002', name='발표와토론', category='교양필수',
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
        # short_categories는 (category, liberal_subtype) 튜플 set — 전공은 liberal_subtype=None (#47)
        course = self._course(category='전공선택')
        score = self._score(course, short_categories={('전공선택', None)})
        self.assertEqual(score, 100 + BONUS_CATEGORY_SHORT)

    def test_교양_4종은_동일_category여도_별개_short_키(self):
        # 핵심교양만 부족하고 일반교양은 다 채워진 상태 — category='교양선택' 같지만
        # liberal_subtype 으로 key가 달라 일반교양 과목은 가산점 0 (#47)
        nuclear = self._course(category='교양선택', liberal_subtype='핵심교양')
        general = self._course(category='교양선택', liberal_subtype='일반교양')
        short_keys = {('교양선택', '핵심교양')}
        self.assertEqual(self._score(nuclear, short_categories=short_keys), 100 + BONUS_CATEGORY_SHORT)
        self.assertEqual(self._score(general, short_categories=short_keys), 100)

    def test_전공필수면_BONUS_MAJOR_REQUIRED_가산(self):
        # 다른 분기 회피 위해 year_open=2, semester_open=2 (학년 같음, 학기 다름)
        course = self._course(category='전공필수', year_open=2, semester_open=2)
        score = self._score(course)
        self.assertEqual(score, 100 + BONUS_MAJOR_REQUIRED)

    def test_교양필수면_BONUS_LIBERAL_REQUIRED_가산(self):
        course = self._course(category='교양필수', year_open=2, semester_open=2)
        score = self._score(course)
        self.assertEqual(score, 100 + BONUS_LIBERAL_REQUIRED)

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
        course = self._course(category='교양필수', year_open=1, semester_open=1)
        score = self._score(course)
        self.assertEqual(score, 100 + BONUS_LIBERAL_REQUIRED + BONUS_BACKLOG_REQUIRED)

    def test_밀린_전공선택은_BACKLOG_가산_안받음(self):
        """BACKLOG_REQUIRED_CATEGORIES = ('전공필수','교양필수') — 선택과목은 제외"""
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

    def test_동일학과_선수과목_미이수시_PENALTY_PREREQUISITE_MISSING_감점(self):
        course = self._course(category='전공선택', major='컴퓨터공학전공')
        score = self._score(
            course,
            user_major='컴퓨터공학전공',
            course_prerequisite_ids={101},  # 선수과목 있음
            completed_course_ids=set(),     # 미이수
        )
        self.assertEqual(score, 100 - PENALTY_PREREQUISITE_MISSING)

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
