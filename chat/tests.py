"""chat 앱 — 채팅방 CRUD 테스트 (spec 6.5).

PR 1 범위: 채팅방 생성/목록/상세/삭제. 메시지·AI·첨부는 후속 PR.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import ChatAttachment, ChatMessage, ChatRoom

User = get_user_model()

ROOMS_URL = '/api/v1/chat/rooms/'


def make_user(email='a@mju.ac.kr'):
    user = User.objects.create_user(email=email, password='Test1234@')
    user.is_email_verified = True
    user.save(update_fields=['is_email_verified'])
    return user


class ChatRoomCreateTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_empty_room(self):
        res = self.client.post(ROOMS_URL, {}, format='json')
        self.assertEqual(res.status_code, 201)
        room = ChatRoom.objects.get(id=res.data['id'])
        self.assertEqual(room.user, self.user)
        self.assertEqual(room.title, '')
        self.assertEqual(room.category, '기타')
        self.assertEqual(room.last_message_preview, '')

    def test_create_unauthenticated_returns_401(self):
        anon = APIClient()
        res = anon.post(ROOMS_URL, {}, format='json')
        self.assertEqual(res.status_code, 401)


class ChatRoomListTests(TestCase):
    def setUp(self):
        self.user_a = make_user('a@mju.ac.kr')
        self.user_b = make_user('b@mju.ac.kr')
        # user_a의 채팅방 3개 (카테고리 섞어서)
        self.room_etc = ChatRoom.objects.create(user=self.user_a, category='기타')
        self.room_contest = ChatRoom.objects.create(user=self.user_a, category='공모전')
        self.room_general = ChatRoom.objects.create(user=self.user_a, category='일반질문')
        # user_b 채팅방 1개 (응답에 안 나와야 함)
        ChatRoom.objects.create(user=self.user_b, category='기타')

        self.client = APIClient()
        self.client.force_authenticate(self.user_a)

    def test_list_returns_only_own_rooms(self):
        res = self.client.get(ROOMS_URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 3)
        ids = [r['id'] for r in res.data['results']]
        # user_b 채팅방은 빠져 있어야 함
        self.assertEqual(set(ids), {self.room_etc.id, self.room_contest.id, self.room_general.id})

    def test_list_orders_by_updated_at_desc(self):
        # room_general에 메시지를 넣어 updated_at을 최신으로 올림
        self.room_general.save()  # auto_now 트리거
        res = self.client.get(ROOMS_URL)
        ids = [r['id'] for r in res.data['results']]
        self.assertEqual(ids[0], self.room_general.id)

    def test_category_filter(self):
        res = self.client.get(ROOMS_URL, {'category': '공모전'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], self.room_contest.id)


class ChatRoomDetailTests(TestCase):
    def setUp(self):
        self.user_a = make_user('a@mju.ac.kr')
        self.user_b = make_user('b@mju.ac.kr')
        self.room = ChatRoom.objects.create(user=self.user_a, title='지원사업 문의')
        self.client = APIClient()
        self.client.force_authenticate(self.user_a)

    def test_detail_returns_room_with_empty_messages(self):
        res = self.client.get(f'{ROOMS_URL}{self.room.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['id'], self.room.id)
        self.assertEqual(res.data['title'], '지원사업 문의')
        self.assertEqual(res.data['messages'], [])

    def test_other_users_room_returns_404(self):
        # user_a가 user_b의 채팅방에 접근 — enumeration 방어로 404
        other = ChatRoom.objects.create(user=self.user_b)
        res = self.client.get(f'{ROOMS_URL}{other.id}/')
        self.assertEqual(res.status_code, 404)


class ChatRoomDestroyTests(TestCase):
    def setUp(self):
        self.user_a = make_user('a@mju.ac.kr')
        self.user_b = make_user('b@mju.ac.kr')
        self.room = ChatRoom.objects.create(user=self.user_a)
        # 메시지·첨부 CASCADE 동작 검증용
        msg = ChatMessage.objects.create(room=self.room, role='user', content='hi')
        ChatAttachment.objects.create(
            message=msg,
            file='chat/2026/05/dummy.txt',
            file_type='document',
            original_name='dummy.txt',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user_a)

    def test_destroy_cascades_to_messages_and_attachments(self):
        res = self.client.delete(f'{ROOMS_URL}{self.room.id}/')
        self.assertEqual(res.status_code, 204)
        self.assertFalse(ChatRoom.objects.filter(id=self.room.id).exists())
        self.assertEqual(ChatMessage.objects.count(), 0)
        self.assertEqual(ChatAttachment.objects.count(), 0)

    def test_destroy_others_room_returns_404(self):
        other = ChatRoom.objects.create(user=self.user_b)
        res = self.client.delete(f'{ROOMS_URL}{other.id}/')
        self.assertEqual(res.status_code, 404)
        self.assertTrue(ChatRoom.objects.filter(id=other.id).exists())
