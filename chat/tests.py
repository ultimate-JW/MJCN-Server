"""chat 앱 테스트 (spec 6.5).

PR 1 범위: 채팅방 CRUD.
PR 2 범위: 메시지 전송 + AI 응답 + 첫 메시지 title/category 자동 생성.
PR 3 범위: 첨부파일 업로드 (multipart) + validation.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from notices.ai.client import AIClientError

from .models import ChatAttachment, ChatMessage, ChatRoom

User = get_user_model()

ROOMS_URL = '/api/v1/chat/rooms/'


def messages_url(room_id: int) -> str:
    return f'{ROOMS_URL}{room_id}/messages/'


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


class SendMessageTests(TestCase):
    """POST /rooms/<id>/messages/ — 메시지 전송 + AI 응답 (spec 5.2.3)."""

    def setUp(self):
        self.user_a = make_user('a@mju.ac.kr')
        self.user_b = make_user('b@mju.ac.kr')
        self.room = ChatRoom.objects.create(user=self.user_a)
        self.client = APIClient()
        self.client.force_authenticate(self.user_a)

    @patch('chat.views.generate_assistant_reply', return_value='AI 응답입니다.')
    @patch('chat.views.classify_and_title', return_value=('수강신청 문의', '수강·졸업'))
    def test_first_message_creates_title_and_category(self, mock_classify, mock_reply):
        res = self.client.post(
            messages_url(self.room.id),
            {'content': '수강신청 언제 시작해?'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        # 응답은 assistant 메시지
        self.assertEqual(res.data['role'], 'assistant')
        self.assertEqual(res.data['content'], 'AI 응답입니다.')

        # 분류 호출 1회, 응답 생성 1회
        mock_classify.assert_called_once_with('수강신청 언제 시작해?')
        self.assertEqual(mock_reply.call_count, 1)

        # ChatRoom 갱신 확인
        self.room.refresh_from_db()
        self.assertEqual(self.room.title, '수강신청 문의')
        self.assertEqual(self.room.category, '수강·졸업')
        self.assertEqual(self.room.last_message_preview, 'AI 응답입니다.')

        # 메시지 2개 (user + assistant)
        msgs = list(self.room.messages.order_by('created_at'))
        self.assertEqual(len(msgs), 2)
        self.assertEqual((msgs[0].role, msgs[0].content), ('user', '수강신청 언제 시작해?'))
        self.assertEqual((msgs[1].role, msgs[1].content), ('assistant', 'AI 응답입니다.'))

    @patch('chat.views.generate_assistant_reply', return_value='두 번째 응답')
    @patch('chat.views.classify_and_title', return_value=('새 제목', '기타'))
    def test_second_message_does_not_overwrite_title(self, mock_classify, mock_reply):
        # 첫 메시지 후 title 이미 채워진 상태
        self.room.title = '이미 정해진 제목'
        self.room.category = '공모전'
        self.room.save(update_fields=['title', 'category'])
        ChatMessage.objects.create(room=self.room, role='user', content='첫 질문')
        ChatMessage.objects.create(room=self.room, role='assistant', content='첫 응답')

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '추가 질문'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        # 두 번째 이후 메시지는 classify 호출 안 함
        mock_classify.assert_not_called()
        self.room.refresh_from_db()
        self.assertEqual(self.room.title, '이미 정해진 제목')
        self.assertEqual(self.room.category, '공모전')

    @patch('chat.views.generate_assistant_reply', return_value='응답')
    @patch('chat.views.classify_and_title', return_value=('t', '기타'))
    @override_settings(OPENAI_CHAT_CONTEXT_MESSAGES=3)
    def test_context_window_passes_only_last_n_messages(self, mock_classify, mock_reply):
        # 기존 메시지 5개 + 새 user 메시지 1개 = 6개. context=3이면 마지막 3개만 전달.
        for i in range(5):
            ChatMessage.objects.create(
                room=self.room, role='user' if i % 2 == 0 else 'assistant', content=f'old-{i}'
            )
        # 첫 메시지가 이미 있으니 classify 호출 안 됨 (room.messages 1개 이상 존재)
        res = self.client.post(messages_url(self.room.id), {'content': '새 질문'}, format='json')
        self.assertEqual(res.status_code, 201)
        mock_classify.assert_not_called()
        # generate_assistant_reply에 전달된 history = 3개 (가장 최근 3개: old-4, old-3, 또는 새 user)
        history_arg = mock_reply.call_args.args[0]
        self.assertEqual(len(history_arg), 3)
        # 가장 마지막 메시지는 방금 INSERT한 user 메시지
        self.assertEqual(history_arg[-1].content, '새 질문')
        self.assertEqual(history_arg[-1].role, 'user')

    @patch('chat.views.classify_and_title', side_effect=AIClientError('boom'))
    def test_ai_failure_rolls_back_and_returns_503(self, mock_classify):
        res = self.client.post(
            messages_url(self.room.id),
            {'content': '첫 질문'},
            format='json',
        )
        self.assertEqual(res.status_code, 503)
        # 트랜잭션 롤백 → user 메시지도 저장 안 됨
        self.assertEqual(self.room.messages.count(), 0)
        self.room.refresh_from_db()
        self.assertEqual(self.room.title, '')

    def test_other_users_room_returns_404(self):
        other = ChatRoom.objects.create(user=self.user_b)
        res = self.client.post(messages_url(other.id), {'content': 'hi'}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(other.messages.count(), 0)

    def test_unauthenticated_returns_401(self):
        anon = APIClient()
        res = anon.post(messages_url(self.room.id), {'content': 'hi'}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_empty_content_returns_400(self):
        res = self.client.post(messages_url(self.room.id), {'content': ''}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.room.messages.count(), 0)


class SendMessageAttachmentTests(TestCase):
    """multipart 첨부파일 업로드 (spec 5.2.4)."""

    def setUp(self):
        self.user = make_user()
        # 첨부파일 테스트는 첫 메시지가 아닌 케이스로 단순화 (분류 mock 안 해도 됨)
        self.room = ChatRoom.objects.create(user=self.user)
        ChatMessage.objects.create(room=self.room, role='user', content='첫 질문')
        ChatMessage.objects.create(room=self.room, role='assistant', content='첫 응답')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch('chat.views.generate_assistant_reply', return_value='첨부 확인했습니다.')
    def test_image_attachment_saved(self, mock_reply):
        image = SimpleUploadedFile('photo.png', b'\x89PNG\r\n\x1a\nfake', content_type='image/png')
        res = self.client.post(
            messages_url(self.room.id),
            {'content': '이 사진 봐줘', 'attachments': [image]},
            format='multipart',
        )
        self.assertEqual(res.status_code, 201)
        user_msg = self.room.messages.filter(role='user').last()
        self.assertEqual(user_msg.attachments.count(), 1)
        attach = user_msg.attachments.first()
        self.assertEqual(attach.file_type, 'image')
        self.assertEqual(attach.original_name, 'photo.png')

    @patch('chat.views.generate_assistant_reply', return_value='OK')
    def test_document_attachment_saved(self, mock_reply):
        pdf = SimpleUploadedFile('report.pdf', b'%PDF-1.4 fake', content_type='application/pdf')
        res = self.client.post(
            messages_url(self.room.id),
            {'content': '이 문서 요약', 'attachments': [pdf]},
            format='multipart',
        )
        self.assertEqual(res.status_code, 201)
        attach = self.room.messages.filter(role='user').last().attachments.first()
        self.assertEqual(attach.file_type, 'document')

    @patch('chat.views.generate_assistant_reply', return_value='OK')
    def test_multiple_attachments(self, mock_reply):
        img = SimpleUploadedFile('a.jpg', b'fake', content_type='image/jpeg')
        pdf = SimpleUploadedFile('b.pdf', b'fake', content_type='application/pdf')
        res = self.client.post(
            messages_url(self.room.id),
            {'content': '둘 다 봐줘', 'attachments': [img, pdf]},
            format='multipart',
        )
        self.assertEqual(res.status_code, 201)
        attachments = self.room.messages.filter(role='user').last().attachments.all()
        self.assertEqual(attachments.count(), 2)
        self.assertEqual(
            {a.file_type for a in attachments},
            {'image', 'document'},
        )

    def test_disallowed_extension_returns_400(self):
        exe = SimpleUploadedFile('bad.exe', b'MZ\x90\x00 fake', content_type='application/x-msdownload')
        res = self.client.post(
            messages_url(self.room.id),
            {'content': 'exe 보냄', 'attachments': [exe]},
            format='multipart',
        )
        self.assertEqual(res.status_code, 400)
        # 메시지·첨부 둘 다 저장 안 됨 (validation은 view 진입 전)
        self.assertEqual(ChatAttachment.objects.count(), 0)
        # 기존 메시지 2건은 그대로
        self.assertEqual(self.room.messages.count(), 2)

    def test_oversize_file_returns_400(self):
        big = SimpleUploadedFile(
            'big.png',
            b'x' * (10 * 1024 * 1024 + 1),
            content_type='image/png',
        )
        res = self.client.post(
            messages_url(self.room.id),
            {'content': '큰 파일', 'attachments': [big]},
            format='multipart',
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(ChatAttachment.objects.count(), 0)

    @patch('chat.views.generate_assistant_reply', return_value='OK')
    def test_no_attachments_field_works_like_json(self, mock_reply):
        # multipart인데 attachments 안 보내도 텍스트 흐름 그대로
        res = self.client.post(
            messages_url(self.room.id),
            {'content': '첨부 없음'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(ChatAttachment.objects.count(), 0)

    @patch('chat.views.generate_assistant_reply', side_effect=AIClientError('boom'))
    def test_ai_failure_rolls_back_attachments(self, mock_reply):
        img = SimpleUploadedFile('a.jpg', b'fake', content_type='image/jpeg')
        before_msgs = self.room.messages.count()
        res = self.client.post(
            messages_url(self.room.id),
            {'content': 'hi', 'attachments': [img]},
            format='multipart',
        )
        self.assertEqual(res.status_code, 503)
        # user 메시지·첨부 모두 롤백
        self.assertEqual(self.room.messages.count(), before_msgs)
        self.assertEqual(ChatAttachment.objects.count(), 0)
