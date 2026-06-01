"""GET /api/v1/notices/?view=personalized 통합 테스트 (spec 5.4.2 / 5.10)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import InterestArea
from notices.models import Notice

User = get_user_model()


class PersonalizedNoticeListTests(TestCase):
    URL = '/api/v1/notices/'

    def setUp(self):
        self.user = User.objects.create_user(
            email='u@mju.ac.kr', password='pw1234abc',
            major='컴퓨터공학전공',
        )
        InterestArea.objects.create(user=self.user, category='IT/개발', custom_text='AI, 백엔드')

        self.client = APIClient()
        self.client.force_authenticate(self.user)

        now = timezone.now()
        # 3개 공지: 매칭 점수 다르게
        self.n_high = Notice.objects.create(
            source='general', title='high match', content='c',
            url='https://x/1', published_at=now - timedelta(days=1),
            tags=['IT/개발', 'AI', '백엔드'],  # 3점 매칭
        )
        self.n_mid = Notice.objects.create(
            source='general', title='mid match', content='c',
            url='https://x/2', published_at=now,  # 가장 최근
            tags=['AI'],  # 1점 매칭
        )
        self.n_zero = Notice.objects.create(
            source='general', title='no match', content='c',
            url='https://x/3', published_at=now - timedelta(days=2),
            tags=['음악', '미술'],  # 0점
        )

    def test_personalized는_점수_내림차순_162(self):
        # #162: match_score 내림차순 → 동점 시 최신순
        res = self.client.get(self.URL, {'view': 'personalized'})
        self.assertEqual(res.status_code, 200)
        ids = [r['id'] for r in res.data['results']]
        # 점수: n_high(3) > n_mid(1) > n_zero(0)
        self.assertEqual(ids, [self.n_high.id, self.n_mid.id, self.n_zero.id])

    def test_personalized_가_기본값_162(self):
        # view 파라미터 없으면 personalized — 점수 높은 것이 위
        res = self.client.get(self.URL)
        ids = [r['id'] for r in res.data['results']]
        self.assertEqual(ids[0], self.n_high.id)  # 최고 점수가 위

    def test_all은_최신순(self):
        res = self.client.get(self.URL, {'view': 'all'})
        ids = [r['id'] for r in res.data['results']]
        # published_at: n_mid (now) > n_high (-1d) > n_zero (-2d)
        self.assertEqual(ids, [self.n_mid.id, self.n_high.id, self.n_zero.id])

    def test_match_score_응답에_포함(self):
        res = self.client.get(self.URL, {'view': 'personalized'})
        scores = {r['id']: r['match_score'] for r in res.data['results']}
        self.assertEqual(scores[self.n_high.id], 3)
        self.assertEqual(scores[self.n_mid.id], 1)
        self.assertEqual(scores[self.n_zero.id], 0)

    def test_all은_최신순이고_match_score는_실제계산_162(self):
        # #162: view=all은 최신순 정렬이되 match_score는 0 강제 안 하고 실제 계산
        res = self.client.get(self.URL, {'view': 'all'})
        ids = [r['id'] for r in res.data['results']]
        self.assertEqual(ids, [self.n_mid.id, self.n_high.id, self.n_zero.id])
        scores = {r['id']: r['match_score'] for r in res.data['results']}
        self.assertEqual(scores[self.n_high.id], 3)
        self.assertEqual(scores[self.n_mid.id], 1)
        self.assertEqual(scores[self.n_zero.id], 0)

    def test_관심사_없는_사용자도_0점으로_노출(self):
        # 신규 사용자 — 관심사 미설정, 빈 화면 방지 (spec 정책)
        new_user = User.objects.create_user(
            email='new@mju.ac.kr', password='pw1234abc',
        )
        client = APIClient()
        client.force_authenticate(new_user)
        res = client.get(self.URL, {'view': 'personalized'})
        self.assertEqual(res.data['count'], 3)
        for r in res.data['results']:
            self.assertEqual(r['match_score'], 0)

    def test_동점이면_published_at_최근_위(self):
        # 같은 점수면 최신 공지가 위
        now = timezone.now()
        Notice.objects.all().delete()
        a = Notice.objects.create(source='general', title='a', content='c',
                                   url='https://x/a', published_at=now - timedelta(days=1),
                                   tags=['IT/개발'])
        b = Notice.objects.create(source='general', title='b', content='c',
                                   url='https://x/b', published_at=now,  # 더 최근
                                   tags=['IT/개발'])
        res = self.client.get(self.URL, {'view': 'personalized'})
        ids = [r['id'] for r in res.data['results']]
        self.assertEqual(ids, [b.id, a.id])

    def test_source_필터와_조합(self):
        # 다른 source의 매칭 공지 추가 → source 필터에 걸려 제외
        Notice.objects.create(
            source='academic', title='academic match', content='c',
            url='https://x/aca', published_at=timezone.now(),
            tags=['IT/개발', 'AI', '백엔드'],  # 매칭 점수 높음
        )
        res = self.client.get(self.URL, {'view': 'personalized', 'source': 'general'})
        # general source 3개만
        ids = [r['id'] for r in res.data['results']]
        self.assertNotIn(Notice.objects.get(source='academic').id, ids)
        self.assertEqual(len(ids), 3)


# #155에서 personalized를 최신순으로 통일했으나, #162에서 personalized는
# match_score 내림차순 정렬로 복원(맞춤형 분리). view=all만 최신순 유지.
# 학사공지 누락 방지는 PUSH fanout(#153)이 담당하며, view=all 탭에서 항상 열람 가능.
# 정렬 회귀는 test_personalized는_점수_내림차순_162 / test_all은_최신순에서 검증.
