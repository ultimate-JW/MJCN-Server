"""대시보드 집계 API 테스트 (spec 5.8 / 6.10)."""
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import CurrentCourse, InterestArea
from information.models import Information
from notices.models import Notice
from notifications.models import Notification

User = get_user_model()

_WEEKDAY_KO = ['월', '화', '수', '목', '금', '토', '일']


def _make_user(email='student@mju.ac.kr', **overrides):
    defaults = dict(
        password='testpass123',
        name='홍길동',
        major='데이터테크놀로지전공',
        is_email_verified=True,
        is_onboarding_completed=True,
    )
    defaults.update(overrides)
    return User.objects.create_user(email=email, **defaults)


def _today_kr():
    return _WEEKDAY_KO[timezone.localdate().weekday()]


def _other_kr():
    """오늘과 다른 요일 하나."""
    return _WEEKDAY_KO[(timezone.localdate().weekday() + 3) % 7]


class DashboardAPITests(APITestCase):
    url = '/api/v1/dashboard/'

    def setUp(self):
        self.user = _make_user()
        self.client.force_authenticate(user=self.user)

    def test_인증_없으면_401(self):
        self.client.force_authenticate(user=None)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_응답_최상위_키_구성(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        for key in ('greeting', 'graduation_progress_percent', 'today_schedule',
                    'notices', 'information', 'unread_notification_count'):
            self.assertIn(key, res.data)

    def test_greeting_사용자명_요일_수업수(self):
        res = self.client.get(self.url)
        greeting = res.data['greeting']
        self.assertEqual(greeting['user_name'], '홍길동')
        self.assertEqual(greeting['weekday'], _today_kr())
        self.assertEqual(greeting['today_class_count'], 0)

    # --- today_schedule ---

    def test_today_schedule_오늘_요일만_시간순(self):
        today, other = _today_kr(), _other_kr()
        CurrentCourse.objects.create(
            user=self.user, course_name='자료구조', course_code='C2',
            day_of_week=today, start_time=time(13, 0), end_time=time(14, 30),
        )
        CurrentCourse.objects.create(
            user=self.user, course_name='운영체제', course_code='C1',
            day_of_week=today, start_time=time(9, 0), end_time=time(10, 30),
        )
        # 다른 요일 수업 — 노출되면 안 됨
        CurrentCourse.objects.create(
            user=self.user, course_name='네트워크', course_code='C3',
            day_of_week=other, start_time=time(9, 0), end_time=time(10, 30),
        )
        res = self.client.get(self.url)
        schedule = res.data['today_schedule']
        self.assertEqual([c['course_name'] for c in schedule], ['운영체제', '자료구조'])
        self.assertEqual(res.data['greeting']['today_class_count'], 2)

    # --- notices ---

    def test_notices_맞춤형_우선_부족분은_최신으로_채움(self):
        now = timezone.now()
        # 매칭 안 되는 일반공지 4개 (n0가 가장 최신)
        for i in range(4):
            Notice.objects.create(
                source='general', title=f'일반공지{i}',
                url=f'https://mju.ac.kr/n{i}',
                published_at=now - timedelta(days=10 + i), tags=['기타'],
            )
        # 매칭 공지 1개 — 가장 오래됐지만 점수로 1위여야 함
        matched = Notice.objects.create(
            source='academic', title='맞춤공지', url='https://mju.ac.kr/match',
            published_at=now - timedelta(days=30),
            tags=['데이터테크놀로지전공'],
        )
        res = self.client.get(self.url)
        notices = res.data['notices']
        self.assertEqual(len(notices), 3)
        # 맞춤형이 맨 앞
        self.assertEqual(notices[0]['id'], matched.id)
        self.assertGreaterEqual(notices[0]['match_score'], 1)
        # 부족분은 최신 일반공지로 채워짐 (n0, n1)
        self.assertEqual([n['title'] for n in notices[1:]], ['일반공지0', '일반공지1'])
        self.assertTrue(all(n['match_score'] == 0 for n in notices[1:]))

    def test_notices_매칭_0개면_최신_3개(self):
        now = timezone.now()
        for i in range(5):
            Notice.objects.create(
                source='general', title=f'공지{i}', url=f'https://mju.ac.kr/x{i}',
                published_at=now - timedelta(days=i), tags=[],
            )
        res = self.client.get(self.url)
        notices = res.data['notices']
        self.assertEqual([n['title'] for n in notices], ['공지0', '공지1', '공지2'])
        self.assertTrue(all(n['match_score'] == 0 for n in notices))

    def test_notices_DB에_3개_미만이면_있는만큼만(self):
        Notice.objects.create(
            source='general', title='하나', url='https://mju.ac.kr/only',
            published_at=timezone.now(), tags=[],
        )
        res = self.client.get(self.url)
        self.assertEqual(len(res.data['notices']), 1)

    # --- #153: 학사공지 우선 노출 ---

    def test_학사공지_매칭_0이어도_노출_153(self):
        # 학사공지 1개(매칭 0) + 매칭되는 일반 공지 5개. 학사가 무조건 슬롯 안.
        now = timezone.now()
        academic = Notice.objects.create(
            source='academic', title='수강신청 안내', url='https://mju.ac.kr/reg',
            published_at=now - timedelta(days=5), tags=[],
        )
        for i in range(5):
            Notice.objects.create(
                source='general', title=f'관심사공지{i}',
                url=f'https://mju.ac.kr/match{i}',
                published_at=now - timedelta(days=i),
                tags=['데이터테크놀로지전공'],  # 사용자 전공 매칭
            )
        res = self.client.get(self.url)
        notices = res.data['notices']
        self.assertEqual(len(notices), 3)
        # 학사공지가 첫 슬롯에 무조건 포함 (FEED_SIZE=3 안)
        ids = [n['id'] for n in notices]
        self.assertIn(academic.id, ids)

    def test_학사공지_여러건이면_최신순으로_슬롯_채움_153(self):
        # academic 4건. FEED_SIZE=3이라 최신 3건만 노출.
        now = timezone.now()
        for i in range(4):
            Notice.objects.create(
                source='academic', title=f'학사{i}',
                url=f'https://mju.ac.kr/a{i}',
                published_at=now - timedelta(days=i), tags=[],
            )
        res = self.client.get(self.url)
        notices = res.data['notices']
        # 최신 3건 (학사0, 학사1, 학사2)
        self.assertEqual([n['title'] for n in notices], ['학사0', '학사1', '학사2'])

    def test_학사공지_2건_매칭_3건이면_학사_먼저_그_뒤_매칭_153(self):
        now = timezone.now()
        # 학사 2건 (최신순으로 a0, a1)
        for i in range(2):
            Notice.objects.create(
                source='academic', title=f'학사{i}',
                url=f'https://mju.ac.kr/a{i}',
                published_at=now - timedelta(days=i), tags=[],
            )
        # 매칭 일반 3건
        for i in range(3):
            Notice.objects.create(
                source='general', title=f'매칭{i}',
                url=f'https://mju.ac.kr/m{i}',
                published_at=now - timedelta(days=10 + i),
                tags=['데이터테크놀로지전공'],
            )
        res = self.client.get(self.url)
        notices = res.data['notices']
        self.assertEqual(len(notices), 3)
        # 학사 2건이 먼저 (최신순) + 매칭 1건 (제일 최근 매칭)
        self.assertEqual(notices[0]['title'], '학사0')
        self.assertEqual(notices[1]['title'], '학사1')
        self.assertEqual(notices[2]['title'], '매칭0')

    # --- information ---

    def test_information_d_day_포함_만료_비활성_제외(self):
        today = timezone.localdate()
        Information.objects.create(
            title='진행중공모전', url='https://w.com/1',
            source='wevity', source_id='1',
            end_date=today + timedelta(days=5), categories=['공모전'],
            is_active=True,
        )
        # 어제 마감 — 제외
        Information.objects.create(
            title='만료공모전', url='https://w.com/2',
            source='wevity', source_id='2',
            end_date=today - timedelta(days=1), categories=['공모전'],
            is_active=True,
        )
        # 비활성 — 제외
        Information.objects.create(
            title='비활성공모전', url='https://w.com/3',
            source='wevity', source_id='3',
            end_date=today + timedelta(days=10), categories=['공모전'],
            is_active=False,
        )
        res = self.client.get(self.url)
        info = res.data['information']
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]['title'], '진행중공모전')
        self.assertEqual(info[0]['d_day'], 5)

    def test_information_end_date_없으면_d_day_null(self):
        Information.objects.create(
            title='상시모집', url='https://w.com/9',
            source='wevity', source_id='9',
            end_date=None, categories=['대외활동'], is_active=True,
        )
        res = self.client.get(self.url)
        info = res.data['information']
        self.assertEqual(len(info), 1)
        self.assertIsNone(info[0]['d_day'])

    def test_information_맞춤형_우선(self):
        today = timezone.localdate()
        InterestArea.objects.create(
            user=self.user, category='IT/개발', custom_text='장학',
        )
        # 매칭 정보 — 마감은 더 멀지만 점수로 1위여야
        Information.objects.create(
            title='맞춤정보', url='https://w.com/m',
            source='wevity', source_id='m',
            end_date=today + timedelta(days=20), categories=['장학'],
            is_active=True,
        )
        # 비매칭 정보 — 마감 임박
        Information.objects.create(
            title='일반정보', url='https://w.com/g',
            source='wevity', source_id='g',
            end_date=today + timedelta(days=1), categories=['기타'],
            is_active=True,
        )
        res = self.client.get(self.url)
        info = res.data['information']
        self.assertEqual(info[0]['title'], '맞춤정보')
        self.assertGreaterEqual(info[0]['match_score'], 1)

    # --- unread_notification_count ---

    def test_unread_notification_count(self):
        Notification.objects.create(
            user=self.user, title='a', message='m',
            notification_type=Notification.TYPE_NOTICE, is_read=False,
        )
        Notification.objects.create(
            user=self.user, title='b', message='m',
            notification_type=Notification.TYPE_NOTICE, is_read=True,
        )
        Notification.objects.create(
            user=self.user, title='c', message='m',
            notification_type=Notification.TYPE_INFORMATION, is_read=False,
        )
        # 다른 사용자 알림은 카운트에서 제외
        other = _make_user(email='other@mju.ac.kr')
        Notification.objects.create(
            user=other, title='d', message='m',
            notification_type=Notification.TYPE_NOTICE, is_read=False,
        )
        res = self.client.get(self.url)
        self.assertEqual(res.data['unread_notification_count'], 2)


class DashboardGraduationProgressTests(APITestCase):
    """졸업 진척도(%) — courses/tests.py에서 이전 (spec 6.10 단독 엔드포인트 제거).

    graduation_progress_percent는 이제 dashboard 응답으로만 노출된다.
    """
    url = '/api/v1/dashboard/'

    def _progress(self, user):
        self.client.force_authenticate(user=user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        return res.data['graduation_progress_percent']

    def test_사용자_입력_졸업일로_진척도_계산(self):
        # admission=2023 (시작 2023-03-01), graduation 2027.2 폴백 2027-02-10
        user = _make_user(
            email='a@x.com',
            admission_year=2023, grade=4, semester=1,
            graduation_year=2027, graduation_month=2,
        )
        pct = self._progress(user)
        self.assertGreater(pct, 50)
        self.assertLess(pct, 100)

    def test_자동_추정_4_1_봄시즌은_다음해_2월(self):
        # graduation_year/month 미입력 → grade=4-1 봄 → 추정
        user = _make_user(
            email='b@x.com',
            admission_year=2023, grade=4, semester=1,
            graduation_year=None, graduation_month=None,
        )
        pct = self._progress(user)
        self.assertGreater(pct, 0)
        self.assertLess(pct, 100)

    def test_엇학기_4_2_봄시즌은_8월_하계로_추정(self):
        # 4-2 봄 → 추정 (현재년, 8) → 폴백 8/20 → 진척도 거의 100 근처
        user = _make_user(
            email='c@x.com',
            admission_year=2022, grade=4, semester=2,
            graduation_year=None, graduation_month=None,
        )
        pct = self._progress(user)
        self.assertGreater(pct, 80)
        self.assertLessEqual(pct, 100)

    def test_admission_year_없으면_0(self):
        user = _make_user(
            email='d@x.com', admission_year=None, grade=4, semester=1,
        )
        self.assertEqual(self._progress(user), 0)

    def test_졸업일_이미_지났으면_100(self):
        user = _make_user(
            email='e@x.com',
            admission_year=2019, graduation_year=2023, graduation_month=2,
        )
        self.assertEqual(self._progress(user), 100)

    def test_graduation_month_잘못된_값이면_자동_추정으로_폴백(self):
        # graduation_month=5 (잘못된 값) → 자동 추정 사용
        user = _make_user(
            email='f@x.com',
            admission_year=2023, grade=4, semester=1,
            graduation_year=2027, graduation_month=5,
        )
        self.assertGreater(self._progress(user), 0)

    def test_grade_semester_없고_졸업희망도_없으면_0(self):
        # 자동 추정조차 불가능 → graduation_date 결정 불가 → 0
        user = _make_user(
            email='g@x.com',
            admission_year=2023, grade=None, semester=None,
            graduation_year=None, graduation_month=None,
        )
        self.assertEqual(self._progress(user), 0)
