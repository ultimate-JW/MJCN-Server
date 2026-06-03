"""공모전 테마 상세 (contest-guide) 테스트 — 이슈 #184 / spec 5.5 · 5.10.4.

build_contest_guide 단위 + GET /api/v1/information/contest-guide/ 통합.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import InterestArea
from information.models import Information
from information.services import build_contest_guide

User = get_user_model()

_seq = [0]


def make_info(**overrides):
    _seq[0] += 1
    defaults = {
        'title': '테스트 공모전',
        'organizer': '주최사',
        'description': '',
        'url': f'https://wevity.com/p/{_seq[0]}',
        'source': 'wevity',
        'source_id': f'contest-{_seq[0]}',
        'is_active': True,
        'categories': ['공모전'],   # Wevity 기본
    }
    defaults.update(overrides)
    return Information.objects.create(**defaults)


def make_user(email, interests=()):
    u = User.objects.create_user(email=email, password='pw1234abc', major='컴퓨터공학전공')
    for c in interests:
        InterestArea.objects.create(user=u, category='기타', custom_text=c)
    return u


class BuildContestGuideTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()

    def _d(self, days):
        return self.today + timedelta(days=days)

    # ── state 3단계 ────────────────────────────────────────────────
    def test_matched_매칭_3건이상(self):
        u = make_user('a@mju.ac.kr', interests=['게임'])
        make_info(title='게임 공모전', end_date=self._d(10))
        make_info(title='게임 해커톤', end_date=self._d(5))
        make_info(title='인디게임 대회', end_date=self._d(20))
        guide = build_contest_guide(u)
        self.assertEqual(guide['state'], 'matched')
        self.assertEqual(len(guide['cards']), 3)
        self.assertIsNone(guide['note'])

    def test_partial_match_1_2건_backfill(self):
        u = make_user('b@mju.ac.kr', interests=['게임'])
        make_info(title='게임 공모전', end_date=self._d(10))   # 매칭 1
        # 일반 공모전 4개 (무매칭)
        for i in range(4):
            make_info(title=f'요리 경연 {i}', end_date=self._d(3 + i))
        guide = build_contest_guide(u)
        self.assertEqual(guide['state'], 'partial_match')
        # 매칭 1 + 일반 backfill → 상한 5
        self.assertEqual(len(guide['cards']), 5)
        # 매칭分이 맨 앞
        self.assertEqual(guide['cards'][0].title, '게임 공모전')

    def test_no_match_0건_마감일순(self):
        u = make_user('c@mju.ac.kr', interests=['금융'])   # 매칭 안 됨
        far = make_info(title='게임 공모전', end_date=self._d(30))
        near = make_info(title='AI 해커톤', end_date=self._d(2))
        guide = build_contest_guide(u)
        self.assertEqual(guide['state'], 'no_match')
        self.assertEqual(guide['note'], 'no_match')
        # 마감 임박순 (near 먼저)
        self.assertEqual([c.id for c in guide['cards']], [near.id, far.id])

    # ── 동의어 매칭 ────────────────────────────────────────────────
    def test_동의어_확장_AI(self):
        u = make_user('d@mju.ac.kr', interests=['ai'])
        make_info(title='2026 인공지능 챌린지', end_date=self._d(10))  # 'ai'→'인공지능'
        guide = build_contest_guide(u)
        self.assertEqual(guide['cards'][0].matched_interests, ['ai'])

    def test_organizer도_매칭_대상(self):
        u = make_user('e@mju.ac.kr', interests=['게임'])
        make_info(title='2026 창작 공모전', organizer='게임산업협회', end_date=self._d(10))
        guide = build_contest_guide(u)
        self.assertEqual(guide['state'], 'partial_match')  # 매칭 1

    # ── 노출 경계 / 필터 ───────────────────────────────────────────
    def test_최대_5개_상한(self):
        u = make_user('f@mju.ac.kr', interests=['게임'])
        for i in range(7):
            make_info(title=f'게임 공모전 {i}', end_date=self._d(i + 1))
        guide = build_contest_guide(u)
        self.assertEqual(len(guide['cards']), 5)

    def test_공모전_아닌_categories_제외(self):
        u = make_user('g@mju.ac.kr', interests=['게임'])
        make_info(title='게임 강의', categories=['교육'], end_date=self._d(5))  # 공모전 아님
        guide = build_contest_guide(u)
        self.assertEqual(len(guide['cards']), 0)

    def test_마감지난_항목_제외(self):
        u = make_user('h@mju.ac.kr', interests=['게임'])
        make_info(title='게임 지난 공모전', end_date=self._d(-1))
        guide = build_contest_guide(u)
        self.assertEqual(len(guide['cards']), 0)

    # ── dday / 우선순위 / 배지 ─────────────────────────────────────
    def test_dday_서버계산(self):
        u = make_user('i@mju.ac.kr', interests=['게임'])
        make_info(title='게임 공모전', end_date=self._d(12))
        guide = build_contest_guide(u)
        self.assertEqual(guide['cards'][0].dday, 12)

    def test_priority_card_id_참조_및_배지(self):
        u = make_user('j@mju.ac.kr', interests=['게임'])
        c = make_info(title='게임 공모전', end_date=self._d(3))  # 임박(<=7)
        guide = build_contest_guide(u)
        p = guide['priority'][0]
        self.assertEqual(p['card_id'], c.id)
        self.assertNotIn('title', p)   # 카드 정보 중복 X
        codes = {r['code'] for r in p['reasons']}
        self.assertIn('interest_match', codes)
        self.assertIn('deadline_soon', codes)
        # 배지 meta
        im = next(r for r in p['reasons'] if r['code'] == 'interest_match')
        self.assertEqual(im['meta']['interest'], '게임')
        ds = next(r for r in p['reasons'] if r['code'] == 'deadline_soon')
        self.assertEqual(ds['meta']['dday'], 3)

    def test_관심사당_1점_캡_categories_title_중복(self):
        # 'ai'가 categories(['공모전','AI'])에도 제목('AI 해커톤')에도 걸려도 1점
        u = make_user('k@mju.ac.kr', interests=['ai'])
        make_info(title='AI 해커톤', categories=['공모전', 'AI'], end_date=self._d(5))
        guide = build_contest_guide(u)
        self.assertEqual(guide['cards'][0].match_score, 1)

    # ── 조언 문구 ──────────────────────────────────────────────────
    def test_advice_매칭있음_문구(self):
        u = make_user('l@mju.ac.kr', interests=['게임'])
        make_info(title='게임 공모전', end_date=self._d(8))
        guide = build_contest_guide(u)
        self.assertIn('게임', guide['advice']['line1'])
        self.assertIn('가장 먼저 마감', guide['advice']['line2'])

    def test_advice_매칭없음_문구(self):
        u = make_user('m@mju.ac.kr', interests=['금융'])
        make_info(title='게임 공모전', end_date=self._d(8))
        guide = build_contest_guide(u)
        self.assertIn('관심사와 직접 일치하는 공모전은 없어요', guide['advice']['line1'])


class ContestGuideAPITests(TestCase):
    URL = '/api/v1/information/contest-guide/'

    def setUp(self):
        self.user = make_user('api@mju.ac.kr', interests=['게임'])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        today = timezone.localdate()
        make_info(title='게임 공모전', end_date=today + timedelta(days=4))

    def test_GET_200_스키마(self):
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, 200)
        for key in ('state', 'advice', 'cards', 'priority', 'quick_questions', 'note'):
            self.assertIn(key, res.data)
        self.assertIn('line1', res.data['advice'])
        card = res.data['cards'][0]
        for key in ('id', 'title', 'organizer', 'categories', 'end_date', 'dday', 'url'):
            self.assertIn(key, card)

    def test_quick_questions_칩_3개_label_prompt(self):
        res = self.client.get(self.URL)
        chips = res.data['quick_questions']
        self.assertEqual(len(chips), 3)
        for chip in chips:
            self.assertIn('label', chip)
            self.assertIn('prompt', chip)

    def test_quick_questions_관심분야_치환(self):
        # 관심분야 '게임' → 1번 칩에 키워드 치환
        res = self.client.get(self.URL)
        first = res.data['quick_questions'][0]
        self.assertIn('게임', first['label'])
        self.assertIn('게임', first['prompt'])

    def test_quick_questions_관심사_없으면_fallback(self):
        u = make_user('noint@mju.ac.kr')
        client = APIClient()
        client.force_authenticate(u)
        res = client.get(self.URL)
        first = res.data['quick_questions'][0]
        # 키워드 없으면 일반 문구 (특정 관심사 미치환)
        self.assertEqual(first['label'], '관심분야 공모전 더 추천')
