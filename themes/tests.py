from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Theme, ThemeItem


User = get_user_model()


def _make_user(email='tester@mju.ac.kr', password='Pa$$w0rd!'):
    return User.objects.create_user(
        email=email, password=password, is_email_verified=True,
    )


def _auth_header(user):
    refresh = RefreshToken.for_user(user)
    return {'HTTP_AUTHORIZATION': f'Bearer {refresh.access_token}'}


class ThemeListTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user()
        # order 3 / 1 / 2 — 정렬 검증용
        cls.theme_b = Theme.objects.create(
            title='B (career order=3)', category=Theme.CATEGORY_CAREER, order=3,
        )
        cls.theme_a = Theme.objects.create(
            title='A (course order=1)',
            category=Theme.CATEGORY_COURSE_REGISTRATION, order=1,
        )
        cls.theme_c = Theme.objects.create(
            title='C (exchange order=2)', category=Theme.CATEGORY_EXCHANGE, order=2,
        )
        # 비활성 테마 — 목록에 노출되면 안 됨
        cls.theme_hidden = Theme.objects.create(
            title='Hidden', category=Theme.CATEGORY_GRANT, order=0, is_active=False,
        )

    def test_unauthenticated_returns_401(self):
        resp = self.client.get(reverse('themes:theme-list'))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_excludes_inactive_and_sorts_by_order(self):
        resp = self.client.get(reverse('themes:theme-list'), **_auth_header(self.user))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data['results']
        # 활성 3개만 노출
        self.assertEqual(len(results), 3)
        titles = [r['title'] for r in results]
        self.assertEqual(titles, ['A (course order=1)', 'C (exchange order=2)', 'B (career order=3)'])
        # Hidden은 미노출
        for r in results:
            self.assertNotEqual(r['title'], 'Hidden')

    def test_list_pagination_envelope(self):
        resp = self.client.get(reverse('themes:theme-list'), **_auth_header(self.user))
        # StandardPagination — count/next/previous/results
        self.assertIn('count', resp.data)
        self.assertIn('results', resp.data)
        self.assertEqual(resp.data['count'], 3)

    def test_category_filter(self):
        resp = self.client.get(
            reverse('themes:theme-list') + '?category=career',
            **_auth_header(self.user),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['category'], 'career')

    def test_category_filter_invalid_returns_empty(self):
        resp = self.client.get(
            reverse('themes:theme-list') + '?category=nonexistent',
            **_auth_header(self.user),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['results'], [])


class ThemeDetailTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user()
        cls.theme = Theme.objects.create(
            title='교환학생 신청 안내',
            category=Theme.CATEGORY_EXCHANGE,
            description='교환학생 절차',
            order=10,
        )
        # order 2 → 1 → 3 입력, 응답은 1/2/3 정렬되어야 함
        cls.item_b = ThemeItem.objects.create(
            theme=cls.theme, title='B', item_type=ThemeItem.ITEM_TYPE_LINK,
            external_url='https://example.com', order=2,
        )
        cls.item_a = ThemeItem.objects.create(
            theme=cls.theme, title='A', item_type=ThemeItem.ITEM_TYPE_GUIDE,
            content='A 본문', order=1,
        )
        cls.item_c = ThemeItem.objects.create(
            theme=cls.theme, title='C', item_type=ThemeItem.ITEM_TYPE_CHECKLIST,
            order=3,
        )
        cls.theme_hidden = Theme.objects.create(
            title='Hidden', category=Theme.CATEGORY_GRANT, is_active=False,
        )

    def test_unauthenticated_returns_401(self):
        resp = self.client.get(
            reverse('themes:theme-detail', args=[self.theme.id]),
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_detail_returns_items_in_order(self):
        resp = self.client.get(
            reverse('themes:theme-detail', args=[self.theme.id]),
            **_auth_header(self.user),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], '교환학생 신청 안내')
        self.assertEqual(resp.data['category'], 'exchange')
        item_titles = [i['title'] for i in resp.data['items']]
        self.assertEqual(item_titles, ['A', 'B', 'C'])
        # ThemeItem 필드 노출 확인
        first = resp.data['items'][0]
        self.assertEqual(set(first.keys()),
                         {'id', 'title', 'content', 'external_url', 'item_type', 'order'})

    def test_inactive_theme_returns_404(self):
        resp = self.client.get(
            reverse('themes:theme-detail', args=[self.theme_hidden.id]),
            **_auth_header(self.user),
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class CollegeLifeGuideDetailTests(APITestCase):
    """career 테마(id=2) 상세가 학년별 대학생활 가이드로 치환되는지 (시연용 분기)."""

    @classmethod
    def setUpTestData(cls):
        cls.career = Theme.objects.create(
            title='졸업 후 진로 로드맵',
            category=Theme.CATEGORY_CAREER,
            description='정적 설명 (치환되어야 함)',
            order=20,
        )
        # 정적 item — 치환되어 응답에 안 나와야 함
        ThemeItem.objects.create(
            theme=cls.career, title='정적 항목', item_type=ThemeItem.ITEM_TYPE_LINK,
            external_url='https://example.com', order=10,
        )
        # career 아닌 테마 — 치환 안 됨(정적 그대로) 확인용
        cls.exchange = Theme.objects.create(
            title='교환학생 신청 안내', category=Theme.CATEGORY_EXCHANGE,
            description='교환학생 절차', order=30,
        )
        ThemeItem.objects.create(
            theme=cls.exchange, title='지원 자격', item_type=ThemeItem.ITEM_TYPE_GUIDE,
            content='요건', order=10,
        )

    def _user(self, grade):
        u = _make_user(email=f'g{grade}@mju.ac.kr')
        u.grade = grade
        u.save(update_fields=['grade'])
        return u

    def _get_career(self, grade):
        return self.client.get(
            reverse('themes:theme-detail', args=[self.career.id]),
            **_auth_header(self._user(grade)),
        )

    def test_grade1_returns_freshman_guide(self):
        resp = self._get_career(1)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], '1학년 대학생활 가이드')
        self.assertIn('처음부터 완벽하게', resp.data['description'])
        titles = [i['title'] for i in resp.data['items']]
        # 섹션 헤더 2개 + 카드 5개 = 7항목, 헤더가 카드 앞에
        self.assertEqual(titles[0], '대학생활 적응')
        self.assertIn('🏫 학교 제도 익히기', titles)
        self.assertIn('앞으로 준비하면 좋은 것', titles)
        self.assertEqual(len(resp.data['items']), 7)
        # 정적 item은 치환되어 사라져야 함
        self.assertNotIn('정적 항목', titles)

    def test_grade4_returns_senior_guide(self):
        resp = self._get_career(4)
        self.assertEqual(resp.data['title'], '4학년 대학생활 가이드')
        titles = [i['title'] for i in resp.data['items']]
        self.assertEqual(titles[0], '졸업 준비')
        self.assertIn('마지막 체크포인트', titles)
        self.assertIn('✅ 졸업요건 점검', titles)

    def test_grade_none_defaults_to_freshman(self):
        resp = self._get_career(None)
        self.assertEqual(resp.data['title'], '1학년 대학생활 가이드')

    def test_grade_over_range_clamps_to_senior(self):
        resp = self._get_career(7)
        self.assertEqual(resp.data['title'], '4학년 대학생활 가이드')

    def test_item_schema_unchanged(self):
        # 응답 스키마(키 집합)는 기존 ThemeItem과 동일해야 함 (API 변경 없음)
        resp = self._get_career(2)
        first = resp.data['items'][0]
        self.assertEqual(set(first.keys()),
                         {'id', 'title', 'content', 'external_url', 'item_type', 'order'})

    def test_non_career_theme_not_replaced(self):
        resp = self.client.get(
            reverse('themes:theme-detail', args=[self.exchange.id]),
            **_auth_header(self._user(1)),
        )
        self.assertEqual(resp.data['title'], '교환학생 신청 안내')
        self.assertEqual([i['title'] for i in resp.data['items']], ['지원 자격'])

    def test_career_returns_grade_quick_questions(self):
        resp = self._get_career(2)
        chips = resp.data['quick_questions']
        self.assertEqual(len(chips), 3)
        # {label, prompt} 형태 + 2학년 칩 내용
        self.assertEqual(set(chips[0].keys()), {'label', 'prompt'})
        self.assertEqual(chips[0]['label'], chips[0]['prompt'])  # 표시=전송 텍스트
        labels = [c['label'] for c in chips]
        self.assertIn('프로젝트는 왜 중요한가요?', labels)

    def test_quick_questions_differ_by_grade(self):
        g1 = [c['prompt'] for c in self._get_career(1).data['quick_questions']]
        g4 = [c['prompt'] for c in self._get_career(4).data['quick_questions']]
        self.assertIn('비교과 프로그램은 왜 참여하는 거야?', g1)
        self.assertIn('취업 준비는 어디서부터 시작해야 해?', g4)
        self.assertNotEqual(g1, g4)

    def test_non_career_quick_questions_empty(self):
        resp = self.client.get(
            reverse('themes:theme-detail', args=[self.exchange.id]),
            **_auth_header(self._user(1)),
        )
        # 다른 테마는 기본 빈 배열 (키는 항상 존재 — 균일 계약)
        self.assertEqual(resp.data['quick_questions'], [])


class SeedThemesCommandTests(APITestCase):
    def test_seed_command_creates_and_is_idempotent(self):
        from django.core.management import call_command

        call_command('seed_themes')
        first_themes = Theme.objects.count()
        first_items = ThemeItem.objects.count()
        self.assertEqual(first_themes, 5)
        self.assertGreaterEqual(first_items, 5 * 2)

        # 두 번째 호출 — row 수가 늘지 않아야 함
        call_command('seed_themes')
        self.assertEqual(Theme.objects.count(), first_themes)
        self.assertEqual(ThemeItem.objects.count(), first_items)
