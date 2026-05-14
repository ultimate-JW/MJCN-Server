from datetime import date

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import AcademicCalendar

User = get_user_model()


def _make_user(graduation_year=2027, graduation_month=8, **overrides):
    defaults = dict(
        email='student@mju.ac.kr',
        password='testpass123',
        name='홍길동',
        grade=4,
        semester=2,
        admission_year=2024,
        major='데이터테크놀로지전공',
        is_email_verified=True,
        is_onboarding_completed=True,
    )
    defaults.update(overrides)
    return User.objects.create_user(
        graduation_year=graduation_year,
        graduation_month=graduation_month,
        **defaults,
    )


# 졸업일·D-day 응답 필드 검증 (spec 5.3.3)
class GraduationDateAPITests(APITestCase):
    url = '/api/v1/courses/status/'

    # --- AcademicCalendar 등록된 경우: 실제 일자 사용 ---

    def test_학사일정_등록되면_하계_졸업일은_실제_일자_사용(self):
        # 하계졸업(2027년 8월) → 학사일정 키 (year=2027, semester=1)
        AcademicCalendar.objects.create(
            year=2027, semester=1,
            semester_end=date(2027, 6, 25),
        )
        user = _make_user(graduation_year=2027, graduation_month=8)
        self.client.force_authenticate(user=user)

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['graduation_date'], '2027-06-25')
        self.assertFalse(res.data['is_estimated'])

    def test_학사일정_등록되면_동계_졸업일은_실제_일자_사용(self):
        # 동계졸업(2027년 2월) → 학사일정 키 (year=2026, semester=2)
        AcademicCalendar.objects.create(
            year=2026, semester=2,
            semester_end=date(2026, 12, 22),
        )
        user = _make_user(graduation_year=2027, graduation_month=2)
        self.client.force_authenticate(user=user)

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['graduation_date'], '2026-12-22')
        self.assertFalse(res.data['is_estimated'])

    # --- AcademicCalendar 미등록: 폴백 규칙 적용 ---

    def test_학사일정_미등록이면_하계는_폴백_8월20일(self):
        user = _make_user(graduation_year=2027, graduation_month=8)
        self.client.force_authenticate(user=user)

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['graduation_date'], '2027-08-20')
        self.assertTrue(res.data['is_estimated'])

    def test_학사일정_미등록이면_동계는_폴백_2월10일(self):
        user = _make_user(graduation_year=2027, graduation_month=2)
        self.client.force_authenticate(user=user)

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['graduation_date'], '2027-02-10')
        self.assertTrue(res.data['is_estimated'])

    # --- 사용자가 졸업 정보 안 설정한 경우 ---

    def test_사용자_졸업연도_없으면_관련_필드_모두_None(self):
        user = _make_user(graduation_year=None, graduation_month=None)
        self.client.force_authenticate(user=user)

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data['graduation_date'])
        self.assertIsNone(res.data['is_estimated'])

    def test_graduation_month가_2_8이_아니면_None(self):
        # graduation_month=6 (잘못된 입력) → 헬퍼가 (None, None) 반환
        user = _make_user(graduation_year=2027, graduation_month=6)
        self.client.force_authenticate(user=user)

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data['graduation_date'])
        self.assertIsNone(res.data['is_estimated'])
