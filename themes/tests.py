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
