"""정보 조회 API 테스트 (spec 6.8)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from information.models import Information

User = get_user_model()


def make_info(**overrides):
    defaults = {
        'title': '기본 정보',
        'organizer': '주최',
        'description': '설명',
        'url': 'https://wevity.com/info/1',
        'start_date': None,
        'end_date': None,
        'categories': [],
        'is_active': True,
    }
    defaults.update(overrides)
    return Information.objects.create(**defaults)


class InformationListAPITests(TestCase):
    """GET /api/v1/information/"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='u@mju.ac.kr', password='pw1234abc',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = '/api/v1/information/'

        today = timezone.localdate()
        # 활성 + 마감 임박 (D-3)
        self.active_close = make_info(
            title='해커톤 모집', url='https://x/1',
            end_date=today + timedelta(days=3),
            categories=['공모전', '교육·강의'],
        )
        # 활성 + 마감 멀음 (D-30)
        self.active_far = make_info(
            title='AI 부트캠프', url='https://x/2',
            end_date=today + timedelta(days=30),
            categories=['부트캠프'],
        )
        # 활성 + 마감일 없음
        self.active_nodate = make_info(
            title='상시 모집', url='https://x/3',
            end_date=None,
            categories=['지원사업'],
        )
        # 마감 지난 항목
        self.expired = make_info(
            title='작년 공모전', url='https://x/4',
            end_date=today - timedelta(days=10),
            categories=['공모전'],
        )
        # is_active=False
        self.inactive = make_info(
            title='비활성 항목', url='https://x/5',
            end_date=today + timedelta(days=5),
            is_active=False,
            categories=['대외활동'],
        )

    def test_인증_필요(self):
        anon = APIClient()
        res = anon.get(self.url)
        self.assertEqual(res.status_code, 401)

    def test_기본은_활성_미마감만(self):
        res = self.client.get(self.url)
        ids = [item['id'] for item in res.data['results']]
        self.assertIn(self.active_close.id, ids)
        self.assertIn(self.active_far.id, ids)
        self.assertIn(self.active_nodate.id, ids)
        self.assertNotIn(self.expired.id, ids)
        self.assertNotIn(self.inactive.id, ids)

    def test_마감일_빠른_순_정렬(self):
        res = self.client.get(self.url)
        ids = [item['id'] for item in res.data['results']]
        # close(3일) → far(30일) → no_date(NULL은 마지막)
        self.assertEqual(ids[0], self.active_close.id)
        self.assertEqual(ids[1], self.active_far.id)
        self.assertEqual(ids[2], self.active_nodate.id)

    def test_include_expired_true는_전체_포함(self):
        res = self.client.get(self.url, {'include_expired': 'true'})
        ids = [item['id'] for item in res.data['results']]
        self.assertIn(self.expired.id, ids)
        self.assertIn(self.inactive.id, ids)

    def test_q_검색(self):
        res = self.client.get(self.url, {'q': '해커톤'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], self.active_close.id)

    def test_q_검색은_주최자도_매칭(self):
        info = make_info(
            title='무관', organizer='명지대 산학협력단',
            url='https://x/99', end_date=timezone.localdate() + timedelta(days=10),
        )
        res = self.client.get(self.url, {'q': '명지대 산학'})
        ids = [item['id'] for item in res.data['results']]
        self.assertIn(info.id, ids)

    def test_category_단일_필터(self):
        res = self.client.get(self.url, {'category': '부트캠프'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], self.active_far.id)

    def test_category_복수_OR_매칭(self):
        res = self.client.get(self.url, {'category': '공모전,부트캠프'})
        ids = [item['id'] for item in res.data['results']]
        self.assertIn(self.active_close.id, ids)  # 공모전
        self.assertIn(self.active_far.id, ids)    # 부트캠프
        self.assertNotIn(self.active_nodate.id, ids)  # 지원사업


class InformationDetailAPITests(TestCase):
    """GET /api/v1/information/<id>/"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='u@mju.ac.kr', password='pw1234abc',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.info = make_info(
            title='상세 테스트', description='상세 설명',
            categories=['공모전'],
        )

    def test_인증_필요(self):
        anon = APIClient()
        res = anon.get(f'/api/v1/information/{self.info.id}/')
        self.assertEqual(res.status_code, 401)

    def test_상세_조회(self):
        res = self.client.get(f'/api/v1/information/{self.info.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['id'], self.info.id)
        self.assertEqual(res.data['description'], '상세 설명')
        self.assertEqual(res.data['categories'], ['공모전'])

    def test_마감_지난_정보도_상세는_조회_가능(self):
        # 상세는 include_expired 여부와 무관하게 조회 (북마크된 마감 항목 등 대비)
        today = timezone.localdate()
        expired = make_info(
            title='과거', url='https://x/past',
            end_date=today - timedelta(days=10),
        )
        res = self.client.get(f'/api/v1/information/{expired.id}/')
        self.assertEqual(res.status_code, 200)

    def test_존재하지_않는_id는_404(self):
        res = self.client.get('/api/v1/information/99999/')
        self.assertEqual(res.status_code, 404)
