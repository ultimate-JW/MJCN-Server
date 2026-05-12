"""공지 조회 API 테스트 (spec 6.7)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from notices.models import Notice, NoticeAIResult

User = get_user_model()


def make_notice(**overrides):
    defaults = {
        'source': 'academic',
        'title': '기본 공지',
        'content': '본문 내용',
        'url': 'https://www.mju.ac.kr/notice/1',
        'published_at': timezone.now(),
        'end_date': None,
        'tags': [],
    }
    defaults.update(overrides)
    return Notice.objects.create(**defaults)


class NoticeListAPITests(TestCase):
    """GET /api/v1/notices/"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='u@mju.ac.kr', password='pw1234abc',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = '/api/v1/notices/'

        now = timezone.now()
        self.n1 = make_notice(
            title='수강신청 안내', source='academic', url='https://x/1',
            published_at=now - timedelta(days=1),
        )
        self.n2 = make_notice(
            title='장학금 모집', source='scholarship', url='https://x/2',
            content='장학금 신청 안내', published_at=now,
        )
        self.n3 = make_notice(
            title='취업 박람회', source='career', url='https://x/3',
            published_at=now - timedelta(days=2),
        )
        # n2에만 AI 결과 부여
        NoticeAIResult.objects.create(
            notice=self.n2, summary='장학금 모집 안내임.',
            notice_type='정보형', status='success',
        )

    def test_인증_필요(self):
        anon = APIClient()
        res = anon.get(self.url)
        self.assertEqual(res.status_code, 401)

    def test_목록_조회(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 3)

    def test_최신순_정렬(self):
        res = self.client.get(self.url)
        ids = [item['id'] for item in res.data['results']]
        self.assertEqual(ids, [self.n2.id, self.n1.id, self.n3.id])

    def test_summary는_AI_결과에서(self):
        res = self.client.get(self.url)
        by_id = {item['id']: item for item in res.data['results']}
        self.assertEqual(by_id[self.n2.id]['summary'], '장학금 모집 안내임.')
        self.assertEqual(by_id[self.n1.id]['summary'], '')  # AI 결과 없음

    def test_q_검색_제목(self):
        res = self.client.get(self.url, {'q': '수강'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], self.n1.id)

    def test_q_검색_본문(self):
        # 본문에만 존재하고 제목엔 없는 키워드
        n_body_only = make_notice(
            title='관련 없는 제목', source='general',
            url='https://x/body-only',
            content='본문전용유일키워드 포함된 안내',
            published_at=timezone.now(),
        )
        res = self.client.get(self.url, {'q': '본문전용유일키워드'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], n_body_only.id)

    def test_source_단일_필터(self):
        res = self.client.get(self.url, {'source': 'academic'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], self.n1.id)

    def test_source_복수_필터(self):
        res = self.client.get(self.url, {'source': 'academic,career'})
        self.assertEqual(res.data['count'], 2)

    def test_source_label_포함(self):
        res = self.client.get(self.url, {'source': 'academic'})
        item = res.data['results'][0]
        self.assertEqual(item['source'], 'academic')
        self.assertEqual(item['source_label'], '학사공지')


class NoticeDetailAPITests(TestCase):
    """GET /api/v1/notices/<id>/"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='u@mju.ac.kr', password='pw1234abc',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.notice = make_notice(
            title='상세 테스트', content='본문 전체', tags=['수강신청'],
        )
        NoticeAIResult.objects.create(
            notice=self.notice,
            notice_type='행동형',
            summary='수강신청 정정 필요.',
            status='success',
            cards=[
                {'title': '🚨 지금 해야 할 행동', 'items': ['MSI 접속 필요']},
                {'title': '📌 등록 기간', 'items': ['5/1 ~ 5/10']},
            ],
        )

    def test_인증_필요(self):
        anon = APIClient()
        res = anon.get(f'/api/v1/notices/{self.notice.id}/')
        self.assertEqual(res.status_code, 401)

    def test_상세_조회(self):
        res = self.client.get(f'/api/v1/notices/{self.notice.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['id'], self.notice.id)
        self.assertEqual(res.data['content'], '본문 전체')
        self.assertEqual(res.data['tags'], ['수강신청'])

    def test_상세는_AI_카드_포함(self):
        res = self.client.get(f'/api/v1/notices/{self.notice.id}/')
        ai = res.data['ai_result']
        self.assertEqual(ai['notice_type'], '행동형')
        self.assertEqual(ai['summary'], '수강신청 정정 필요.')
        self.assertEqual(len(ai['cards']), 2)
        self.assertEqual(ai['cards'][0]['title'], '🚨 지금 해야 할 행동')

    def test_존재하지_않는_id는_404(self):
        res = self.client.get('/api/v1/notices/99999/')
        self.assertEqual(res.status_code, 404)
