"""GET /api/v1/information/?view=personalized 통합 테스트 (spec 5.5.2 / 5.10)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import InterestArea
from information.models import Information

User = get_user_model()


_info_seq = [0]


def make_info(**overrides):
    _info_seq[0] += 1
    defaults = {
        'title': '테스트',
        'organizer': '주최',
        'description': '',
        'url': f'https://wevity.com/p/{_info_seq[0]}',
        'source': 'wevity',
        'source_id': f'personalized-{_info_seq[0]}',
        'is_active': True,
    }
    defaults.update(overrides)
    return Information.objects.create(**defaults)


class PersonalizedInformationListTests(TestCase):
    URL = '/api/v1/information/'

    def setUp(self):
        self.user = User.objects.create_user(
            email='u@mju.ac.kr', password='pw1234abc', major='컴퓨터공학전공',
        )
        InterestArea.objects.create(user=self.user, category='IT/개발')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        today = timezone.localdate()
        # 매칭 점수 + 마감일 다르게
        self.high_close = make_info(
            title='IT 매칭 임박',
            end_date=today + timedelta(days=3),
            categories=['IT/개발'],
        )
        self.high_far = make_info(
            title='IT 매칭 멀음',
            end_date=today + timedelta(days=30),
            categories=['IT/개발'],
        )
        self.zero_close = make_info(
            title='무매칭 임박',
            end_date=today + timedelta(days=5),
            categories=['음악'],
        )

    def test_personalized_매칭된것만_점수순(self):
        # #174: match_score>=1만 노출, 동점이면 end_date 빠른 순
        res = self.client.get(self.URL, {'view': 'personalized'})
        self.assertEqual(res.status_code, 200)
        ids = [r['id'] for r in res.data['results']]
        # zero_close(0점) 제외 → high_close(3일) > high_far(30일)
        self.assertEqual(ids, [self.high_close.id, self.high_far.id])
        self.assertNotIn(self.zero_close.id, ids)

    def test_all은_end_date_빠른순(self):
        res = self.client.get(self.URL, {'view': 'all'})
        ids = [r['id'] for r in res.data['results']]
        # high_close(3) → zero_close(5) → high_far(30)
        self.assertEqual(ids, [self.high_close.id, self.zero_close.id, self.high_far.id])

    def test_personalized가_기본값(self):
        res = self.client.get(self.URL)
        ids = [r['id'] for r in res.data['results']]
        self.assertEqual(res.data['count'], 2)  # 매칭 0인 zero_close 제외
        self.assertEqual(ids[0], self.high_close.id)

    def test_match_score_응답(self):
        res = self.client.get(self.URL, {'view': 'personalized'})
        scores = {r['id']: r['match_score'] for r in res.data['results']}
        self.assertEqual(scores[self.high_close.id], 1)
        self.assertEqual(scores[self.high_far.id], 1)
        # zero_close(0점)는 personalized에서 제외됨
        self.assertNotIn(self.zero_close.id, scores)

    def test_category_필터와_조합(self):
        # ?view=personalized&category=IT/개발 → IT 매칭만 노출
        # categories에 'IT/개발' 포함된 행만 필터
        res = self.client.get(self.URL, {'view': 'personalized', 'category': 'IT/개발'})
        ids = [r['id'] for r in res.data['results']]
        self.assertIn(self.high_close.id, ids)
        self.assertIn(self.high_far.id, ids)
        self.assertNotIn(self.zero_close.id, ids)

    def test_관심사_없는_사용자는_personalized_빈결과_174(self):
        # #174: 관심사 미설정 → 매칭 0 → personalized 빈 결과 (전체는 view=all)
        new_user = User.objects.create_user(email='new@mju.ac.kr', password='pw1234abc')
        client = APIClient()
        client.force_authenticate(new_user)
        res = client.get(self.URL, {'view': 'personalized'})
        self.assertEqual(res.data['count'], 0)
        res_all = client.get(self.URL, {'view': 'all'})
        self.assertEqual(res_all.data['count'], 3)
