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

    def test_personalized는_매칭된것만_최신순_196(self):
        # #196: match_score>=1만 노출, 정렬은 published_at 최신순(점수순 아님)
        res = self.client.get(self.URL, {'view': 'personalized'})
        self.assertEqual(res.status_code, 200)
        ids = [r['id'] for r in res.data['results']]
        # n_zero(0점) 제외. 최신순 → n_mid(now) > n_high(-1d)
        self.assertEqual(ids, [self.n_mid.id, self.n_high.id])
        self.assertNotIn(self.n_zero.id, ids)

    def test_personalized_가_기본값_196(self):
        # view 파라미터 없으면 personalized — 매칭된 것만, 최신순
        res = self.client.get(self.URL)
        ids = [r['id'] for r in res.data['results']]
        self.assertEqual(res.data['count'], 2)  # 매칭 0인 n_zero 제외
        self.assertEqual(ids[0], self.n_mid.id)  # 최신이 위

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
        # n_zero(0점)는 personalized에서 제외됨
        self.assertNotIn(self.n_zero.id, scores)

    def test_all은_최신순이고_match_score는_실제계산_162(self):
        # #162: view=all은 최신순 정렬이되 match_score는 0 강제 안 하고 실제 계산
        res = self.client.get(self.URL, {'view': 'all'})
        ids = [r['id'] for r in res.data['results']]
        self.assertEqual(ids, [self.n_mid.id, self.n_high.id, self.n_zero.id])
        scores = {r['id']: r['match_score'] for r in res.data['results']}
        self.assertEqual(scores[self.n_high.id], 3)
        self.assertEqual(scores[self.n_mid.id], 1)
        self.assertEqual(scores[self.n_zero.id], 0)

    def test_관심사_없는_사용자는_personalized_빈결과_174(self):
        # #174: 관심사 미설정 → 매칭 0 → personalized는 빈 결과 (전체는 view=all에서)
        new_user = User.objects.create_user(
            email='new@mju.ac.kr', password='pw1234abc',
        )
        client = APIClient()
        client.force_authenticate(new_user)
        res = client.get(self.URL, {'view': 'personalized'})
        self.assertEqual(res.data['count'], 0)
        # 전체는 view=all에서 그대로 열람 가능
        res_all = client.get(self.URL, {'view': 'all'})
        self.assertEqual(res_all.data['count'], 3)

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
        # general source 중 매칭된 것만 (n_zero 0점 제외 → high, mid 2개)
        ids = [r['id'] for r in res.data['results']]
        self.assertNotIn(Notice.objects.get(source='academic').id, ids)
        self.assertNotIn(self.n_zero.id, ids)
        self.assertEqual(len(ids), 2)


# 정렬 정책 이력: #155 최신순 통일 → #162 점수순 → #174 매칭 필터+점수순 →
# #196 매칭 필터는 유지하되 정렬은 최신순(점수는 필터 기준으로만). 즉 personalized·all
# 둘 다 최신순이고, 차이는 personalized가 match_score>=1만 노출하는 필터뿐.
# 학사공지 누락 방지는 PUSH fanout(#153)이 담당하며, view=all 탭에서 항상 열람 가능.
# 정렬 회귀는 test_personalized는_매칭된것만_최신순_196 / test_all은_최신순에서 검증.
