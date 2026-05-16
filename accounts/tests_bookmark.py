"""북마크 API 테스트 (spec 6.11).

결정 사안:
- POST 멱등 (이미 있으면 200)
- 없는 object_id POST → 404
- 다른 사용자 북마크 DELETE → 404 (enumeration 방어)
- list 응답 target nested
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Bookmark
from notices.models import Notice
from information.models import Information

User = get_user_model()


def make_user(email='u@mju.ac.kr'):
    return User.objects.create_user(email=email, password='pw1234abc')


def make_notice(**overrides):
    defaults = {
        'source': 'general',
        'title': '테스트 공지',
        'content': '본문',
        'url': f'https://x/{timezone.now().timestamp()}',
        'published_at': timezone.now(),
    }
    defaults.update(overrides)
    return Notice.objects.create(**defaults)


_info_seq = [0]


def make_info(**overrides):
    _info_seq[0] += 1
    defaults = {
        'title': '테스트 정보',
        'organizer': '주최',
        'description': '',
        'url': f'https://wevity.com/info/{_info_seq[0]}',
        'source': 'wevity',
        'source_id': f'test-{_info_seq[0]}',
        'is_active': True,
    }
    defaults.update(overrides)
    return Information.objects.create(**defaults)


# ─────────────────────────────────────────────────────────────────────
# 모델 / 제약조건
# ─────────────────────────────────────────────────────────────────────

class BookmarkModelTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.notice = make_notice()

    def test_unique_constraint(self):
        Bookmark.objects.create(user=self.user, content_type='notice', object_id=self.notice.id)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Bookmark.objects.create(user=self.user, content_type='notice', object_id=self.notice.id)

    def test_user_cascade_delete(self):
        Bookmark.objects.create(user=self.user, content_type='notice', object_id=self.notice.id)
        self.assertEqual(Bookmark.objects.count(), 1)
        self.user.delete()
        self.assertEqual(Bookmark.objects.count(), 0)


# ─────────────────────────────────────────────────────────────────────
# POST /api/v1/bookmarks/ — 추가
# ─────────────────────────────────────────────────────────────────────

class BookmarkCreateTests(TestCase):
    URL = '/api/v1/bookmarks/'

    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.notice = make_notice()

    def test_정상_추가_201(self):
        res = self.client.post(self.URL, {'content_type': 'notice', 'object_id': self.notice.id}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Bookmark.objects.count(), 1)

    def test_같은_항목_재POST는_200_멱등(self):
        res1 = self.client.post(self.URL, {'content_type': 'notice', 'object_id': self.notice.id}, format='json')
        res2 = self.client.post(self.URL, {'content_type': 'notice', 'object_id': self.notice.id}, format='json')
        self.assertEqual(res1.status_code, 201)
        self.assertEqual(res2.status_code, 200)
        # DB에는 1행만
        self.assertEqual(Bookmark.objects.count(), 1)
        # 같은 id 반환
        self.assertEqual(res1.data['id'], res2.data['id'])

    def test_없는_object_id_POST는_404(self):
        res = self.client.post(self.URL, {'content_type': 'notice', 'object_id': 999999}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(Bookmark.objects.count(), 0)

    def test_없는_information_id_POST는_404(self):
        res = self.client.post(self.URL, {'content_type': 'information', 'object_id': 999999}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_잘못된_content_type은_400(self):
        res = self.client.post(self.URL, {'content_type': 'wrong', 'object_id': self.notice.id}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_object_id_누락은_400(self):
        res = self.client.post(self.URL, {'content_type': 'notice'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_비인증은_401(self):
        anon = APIClient()
        res = anon.post(self.URL, {'content_type': 'notice', 'object_id': self.notice.id}, format='json')
        self.assertEqual(res.status_code, 401)


# ─────────────────────────────────────────────────────────────────────
# DELETE /api/v1/bookmarks/<id>/ — 삭제
# ─────────────────────────────────────────────────────────────────────

class BookmarkDeleteTests(TestCase):

    def setUp(self):
        self.user_a = make_user(email='a@mju.ac.kr')
        self.user_b = make_user(email='b@mju.ac.kr')
        self.client = APIClient()
        self.client.force_authenticate(self.user_a)
        self.notice = make_notice()

        # user_a의 북마크
        self.bookmark_a = Bookmark.objects.create(
            user=self.user_a, content_type='notice', object_id=self.notice.id,
        )
        # user_b의 북마크
        notice_b = make_notice()
        self.bookmark_b = Bookmark.objects.create(
            user=self.user_b, content_type='notice', object_id=notice_b.id,
        )

    def test_정상_삭제_204(self):
        res = self.client.delete(f'/api/v1/bookmarks/{self.bookmark_a.id}/')
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Bookmark.objects.filter(id=self.bookmark_a.id).exists())

    def test_다른_사용자_북마크_삭제_시도는_404(self):
        # enumeration 방어 — 403이 아니라 404
        res = self.client.delete(f'/api/v1/bookmarks/{self.bookmark_b.id}/')
        self.assertEqual(res.status_code, 404)
        # user_b 북마크 그대로
        self.assertTrue(Bookmark.objects.filter(id=self.bookmark_b.id).exists())

    def test_없는_id는_404(self):
        res = self.client.delete('/api/v1/bookmarks/999999/')
        self.assertEqual(res.status_code, 404)

    def test_비인증은_401(self):
        anon = APIClient()
        res = anon.delete(f'/api/v1/bookmarks/{self.bookmark_a.id}/')
        self.assertEqual(res.status_code, 401)


# ─────────────────────────────────────────────────────────────────────
# GET /api/v1/bookmarks/ — 목록 + 필터
# ─────────────────────────────────────────────────────────────────────

class BookmarkListTests(TestCase):
    URL = '/api/v1/bookmarks/'

    def setUp(self):
        self.user_a = make_user(email='a@mju.ac.kr')
        self.user_b = make_user(email='b@mju.ac.kr')
        self.client = APIClient()
        self.client.force_authenticate(self.user_a)

        # 공지 2건 (학사/일반)
        self.notice_academic = make_notice(source='academic', title='학사 공지')
        self.notice_general = make_notice(source='general', title='일반 공지')
        # 정보 2건 (공모전/대외활동)
        self.info_contest = make_info(categories=['공모전'])
        self.info_external = make_info(categories=['대외활동'])

        # user_a 북마크: 학사 공지 + 공모전 정보
        Bookmark.objects.create(user=self.user_a, content_type='notice', object_id=self.notice_academic.id)
        Bookmark.objects.create(user=self.user_a, content_type='notice', object_id=self.notice_general.id)
        Bookmark.objects.create(user=self.user_a, content_type='information', object_id=self.info_contest.id)
        Bookmark.objects.create(user=self.user_a, content_type='information', object_id=self.info_external.id)

        # user_b 북마크: 다른 사용자 것 (응답에 안 나와야 함)
        Bookmark.objects.create(user=self.user_b, content_type='notice', object_id=self.notice_academic.id)

    def test_type_notice_필터(self):
        res = self.client.get(self.URL, {'type': 'notice'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 2)
        # target nested 구조 검증
        first = res.data['results'][0]
        self.assertIn('target', first)
        self.assertIn('title', first['target'])
        self.assertIn('source_label', first['target'])

    def test_type_information_필터(self):
        res = self.client.get(self.URL, {'type': 'information'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 2)

    def test_본인_북마크만_노출(self):
        res = self.client.get(self.URL, {'type': 'notice'})
        # user_b의 북마크는 응답에서 제외
        ids = [r['id'] for r in res.data['results']]
        self.assertNotIn(Bookmark.objects.filter(user=self.user_b).first().id, ids)

    def test_type_notice_source_academic_필터(self):
        res = self.client.get(self.URL, {'type': 'notice', 'source': 'academic'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['target']['source'], 'academic')

    def test_type_information_category_공모전_필터(self):
        res = self.client.get(self.URL, {'type': 'information', 'category': '공모전'})
        self.assertEqual(res.data['count'], 1)
        self.assertIn('공모전', res.data['results'][0]['target']['categories'])

    def test_dangling_bookmark는_target_None_처리(self):
        # 북마크해뒀던 공지를 삭제 (Model.delete()는 instance.id를 None으로 만드므로 미리 저장)
        academic_id = self.notice_academic.id
        general_id = self.notice_general.id
        self.notice_academic.delete()

        res = self.client.get(self.URL, {'type': 'notice'})
        # 응답: target=None인 항목은 그대로 들어감 (dangling은 명시 제외 X, target만 None)
        self.assertEqual(res.data['count'], 2)
        # 살아있는 일반 공지의 target은 정상
        general_item = [r for r in res.data['results'] if r['object_id'] == general_id][0]
        self.assertIsNotNone(general_item['target'])
        # 삭제된 학사 공지의 target은 None
        academic_item = [r for r in res.data['results'] if r['object_id'] == academic_id][0]
        self.assertIsNone(academic_item['target'])

    def test_비인증은_401(self):
        anon = APIClient()
        res = anon.get(self.URL, {'type': 'notice'})
        self.assertEqual(res.status_code, 401)

    def test_type_파라미터_누락은_400(self):
        # spec 6.11 — type=notice 또는 type=information 명시 필수
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, 400)

    def test_잘못된_type_값은_400(self):
        res = self.client.get(self.URL, {'type': 'invalid'})
        self.assertEqual(res.status_code, 400)


class BookmarkCreateInformationTests(TestCase):
    """POST information 타입 검증 (notice와 대칭)."""
    URL = '/api/v1/bookmarks/'

    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.info = make_info()

    def test_information_타입_정상_추가(self):
        res = self.client.post(
            self.URL,
            {'content_type': 'information', 'object_id': self.info.id},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        bookmark = Bookmark.objects.get()
        self.assertEqual(bookmark.content_type, 'information')
        self.assertEqual(bookmark.object_id, self.info.id)

    def test_information_재POST는_200_멱등(self):
        self.client.post(
            self.URL,
            {'content_type': 'information', 'object_id': self.info.id},
            format='json',
        )
        res = self.client.post(
            self.URL,
            {'content_type': 'information', 'object_id': self.info.id},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Bookmark.objects.count(), 1)
