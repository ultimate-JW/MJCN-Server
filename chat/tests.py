"""chat 앱 테스트 (spec 6.5).

PR 1 범위: 채팅방 CRUD.
PR 2 범위: 메시지 전송 + AI 응답 + 첫 메시지 title/category 자동 생성.
PR 3 범위: 첨부파일 업로드 (multipart) + validation.
Step 1 (#98): chat AI에 사용자 프로필 컨텍스트 주입.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import InterestArea
from notices.ai.client import AIClientError

from .models import ChatAttachment, ChatMessage, ChatRoom
from .prompts import build_user_context

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

    @patch('chat.views.generate_assistant_reply', return_value=('AI 응답입니다.', []))
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

    @patch('chat.views.generate_assistant_reply', return_value=('두 번째 응답', []))
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

    @patch('chat.views.generate_assistant_reply', return_value=('응답', []))
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
        # generate_assistant_reply(user, history) — 두 번째 인자가 history
        history_arg = mock_reply.call_args.args[1]
        self.assertEqual(len(history_arg), 3)
        # 가장 마지막 메시지는 방금 INSERT한 user 메시지
        self.assertEqual(history_arg[-1].content, '새 질문')
        self.assertEqual(history_arg[-1].role, 'user')

    @patch('chat.views.generate_assistant_reply', return_value=('AI 응답', []))
    @patch('chat.views.classify_and_title', side_effect=AIClientError('boom'))
    def test_classify_failure_falls_back_and_continues(self, mock_classify, mock_reply):
        # #141 — classify_and_title 실패해도 fallback(title='' / category='기타') 후
        # 응답 생성까지 진행. user 메시지는 살아남음.
        res = self.client.post(
            messages_url(self.room.id),
            {'content': '첫 질문'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        # user + assistant 둘 다 저장됨
        self.assertEqual(self.room.messages.filter(role='user').count(), 1)
        self.assertEqual(self.room.messages.filter(role='assistant').count(), 1)
        self.room.refresh_from_db()
        # title은 빈 채로 (fallback), category는 '기타'로 fallback
        self.assertEqual(self.room.title, '')
        self.assertEqual(self.room.category, '기타')

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

    @patch('chat.views.generate_assistant_reply', return_value=('첨부 확인했습니다.', []))
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

    @patch('chat.views.generate_assistant_reply', return_value=('OK', []))
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

    @patch('chat.views.generate_assistant_reply', return_value=('OK', []))
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

    @patch('chat.views.generate_assistant_reply', return_value=('OK', []))
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
    def test_ai_reply_failure_keeps_user_message_and_attachments(self, mock_reply):
        # #141 — generate_assistant_reply 실패 시 user 메시지·첨부는 살아남음.
        # 503 응답으로 사용자에게 재전송 안내, 단 컨텍스트 일관성 유지.
        img = SimpleUploadedFile('a.jpg', b'fake', content_type='image/jpeg')
        before_msgs = self.room.messages.count()
        res = self.client.post(
            messages_url(self.room.id),
            {'content': 'hi', 'attachments': [img]},
            format='multipart',
        )
        self.assertEqual(res.status_code, 503)
        # user 메시지 + 첨부 살아남음 (setUp 메시지 + 새 user 메시지 = before_msgs + 1)
        self.assertEqual(self.room.messages.count(), before_msgs + 1)
        latest = self.room.messages.order_by('-created_at').first()
        self.assertEqual(latest.role, 'user')
        self.assertEqual(latest.content, 'hi')
        self.assertEqual(latest.attachments.count(), 1)
        # 이번 시도에서 assistant 메시지는 새로 추가되지 않음
        # (setUp에서 만든 assistant 1개는 그대로)


class BuildUserContextTests(TestCase):
    """build_user_context 단위 테스트 (Step 1 / #98)."""

    def test_none_user_returns_empty(self):
        self.assertEqual(build_user_context(None), '')

    def test_blank_user_returns_empty(self):
        user = User.objects.create_user(email='x@mju.ac.kr', password='Test1234@')
        # name/major/grade 모두 비어있는 상태 (온보딩 전)
        self.assertEqual(build_user_context(user), '')

    def test_full_profile(self):
        user = User.objects.create_user(email='y@mju.ac.kr', password='Test1234@')
        user.name = '홍길동'
        user.major = '컴퓨터공학'
        user.grade = 3
        user.semester = 1
        user.admission_year = 2024
        user.save()
        InterestArea.objects.create(user=user, category='IT/개발')
        InterestArea.objects.create(user=user, category='디자인')

        ctx = build_user_context(user)
        self.assertIn('[사용자 정보]', ctx)
        self.assertIn('홍길동', ctx)
        self.assertIn('컴퓨터공학', ctx)
        self.assertIn('3학년 1학기', ctx)
        self.assertIn('2024학번', ctx)
        self.assertIn('IT/개발', ctx)
        self.assertIn('디자인', ctx)

    def test_partial_profile_omits_missing(self):
        user = User.objects.create_user(email='z@mju.ac.kr', password='Test1234@')
        user.major = '컴퓨터공학'
        user.save()
        ctx = build_user_context(user)
        self.assertIn('컴퓨터공학', ctx)
        # name 입력 안 했으므로 "이름:" prefix는 없어야 함
        self.assertNotIn('이름:', ctx)
        self.assertNotIn('학년', ctx)


class SendMessageWithUserContextTests(TestCase):
    """POST messages 시 system prompt에 user prefix가 들어가는지 검증 (Step 1)."""

    def setUp(self):
        self.user = make_user('a@mju.ac.kr')
        self.user.name = '홍길동'
        self.user.major = '컴퓨터공학'
        self.user.grade = 3
        self.user.semester = 1
        self.user.save()
        self.room = ChatRoom.objects.create(user=self.user)
        ChatMessage.objects.create(room=self.room, role='user', content='이전 질문')
        ChatMessage.objects.create(room=self.room, role='assistant', content='이전 응답')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch('chat.services.get_client')
    def test_user_profile_injected_into_system_prompt(self, mock_get_client):
        # OpenAI 호출 자체를 mock — services 레이어까지 통과해 system prompt 확인
        mock_client = mock_get_client.return_value
        mock_response = mock_client.chat.completions.create.return_value
        mock_response.choices[0].message.content = '응답'

        self.client.post(
            messages_url(self.room.id),
            {'content': '시간표 짜줘'},
            format='json',
        )

        # 호출된 messages 인자 검사
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        messages = call_kwargs['messages']
        system_msg = messages[0]
        self.assertEqual(system_msg['role'], 'system')
        # CHAT_SYSTEM + user context가 합쳐져 있어야 함
        self.assertIn('띵똥이', system_msg['content'])  # CHAT_SYSTEM 표식
        self.assertIn('[사용자 정보]', system_msg['content'])
        self.assertIn('홍길동', system_msg['content'])
        self.assertIn('컴퓨터공학', system_msg['content'])
        self.assertIn('3학년 1학기', system_msg['content'])


# ─── Step 2 (#100) — function calling 통합 ─────────────────────────────

from types import SimpleNamespace  # noqa: E402


def _mock_text_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
    )


def _mock_tool_call_response(name: str, arguments: str = '{}', call_id: str = 'call_1'):
    """OpenAI 응답 — tool_calls 1건 포함."""
    fn = SimpleNamespace(arguments=arguments)
    fn.name = name  # SimpleNamespace 생성자에서 name이 Mock의 reserved와 충돌해 직접 set
    call = SimpleNamespace(id=call_id, type='function', function=fn)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='', tool_calls=[call]))]
    )


class ChatToolCallingTests(TestCase):
    """Step 2 — function calling으로 courses 데이터 통합."""

    def setUp(self):
        self.user = make_user('a@mju.ac.kr')
        self.user.major = '컴퓨터공학'
        self.user.save()
        self.room = ChatRoom.objects.create(user=self.user)
        ChatMessage.objects.create(room=self.room, role='user', content='prev')
        ChatMessage.objects.create(room=self.room, role='assistant', content='prev_a')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch('chat.services.get_client')
    def test_normal_question_does_not_call_tools(self, mock_get_client):
        # tool_calls=None 응답 → 일반 자연어 응답만
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = _mock_text_response('일반 답변')

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '잘 지내?'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['content'], '일반 답변')
        # tools 인자는 들어가지만 호출은 1회 (한 번에 답변)
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertIn('tools', call_kwargs)

    @patch('chat.tools.recommend_next_semester_courses', return_value=[])
    @patch('chat.services.get_client')
    def test_tool_call_executes_dispatcher_and_resolves(
        self, mock_get_client, mock_recommend,
    ):
        # 1차 응답: tool_calls (get_next_semester_courses 호출)
        # 2차 응답: tool 결과 받은 후 최종 자연어
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_tool_call_response(
                'get_next_semester_courses',
                arguments='{"target_year": 2026, "target_semester": 1}',
            ),
            _mock_text_response('추천: 운영체제, 데이터통신…'),
        ]

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '2026-1학기 시간표 짜줘'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['content'], '추천: 운영체제, 데이터통신…')

        # dispatcher가 정확한 인자로 호출됐는지
        mock_recommend.assert_called_once()
        call_kwargs = mock_recommend.call_args.kwargs
        self.assertEqual(call_kwargs.get('target_year'), 2026)
        self.assertEqual(call_kwargs.get('target_semester'), 1)

        # OpenAI 호출 2회 (tool call 1 + 최종 응답 1)
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

        # 두 번째 호출 messages에 tool 결과 들어 있어야 함
        second_call = mock_client.chat.completions.create.call_args_list[1]
        msgs = second_call.kwargs['messages']
        roles = [m['role'] for m in msgs]
        self.assertIn('tool', roles)

    @patch('chat.tools.calc_graduation_progress', return_value=42)
    @patch('chat.services.get_client')
    def test_graduation_progress_tool(self, mock_get_client, mock_progress):
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_tool_call_response('get_graduation_progress'),
            _mock_text_response('졸업까지 42% 진행됐어'),
        ]

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '졸업까지 얼마나 남았어?'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        mock_progress.assert_called_once_with(self.user)
        # 두 번째 호출의 tool message 본문에 42가 들어 있어야 함
        second = mock_client.chat.completions.create.call_args_list[1]
        tool_msgs = [m for m in second.kwargs['messages'] if m['role'] == 'tool']
        self.assertTrue(any('42' in m['content'] for m in tool_msgs))

    @patch('chat.tools.recommend_next_semester_courses', return_value=[])
    @patch('chat.services.get_client')
    def test_tool_call_loop_cap(self, mock_get_client, mock_recommend):
        # AI가 계속 tool만 부르는 악성 시나리오 — _MAX_TOOL_ROUNDS 초과 시
        # 마지막에 tools 없는 호출로 강제 종료
        mock_client = mock_get_client.return_value
        loop_resp = _mock_tool_call_response('get_next_semester_courses')
        final_resp = _mock_text_response('강제 종료 응답')
        # 3회 loop + 1회 final = 4회
        mock_client.chat.completions.create.side_effect = [
            loop_resp, loop_resp, loop_resp, final_resp,
        ]

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '시간표 짜줘'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['content'], '강제 종료 응답')
        # 마지막 호출은 tools 인자 없어야 함 (자연어 강제)
        last = mock_client.chat.completions.create.call_args_list[-1]
        self.assertNotIn('tools', last.kwargs)

    @patch('chat.tools.recommend_next_semester_courses',
           side_effect=RuntimeError('boom'))
    @patch('chat.services.get_client')
    def test_dispatcher_error_does_not_crash_chat(self, mock_get_client, mock_recommend):
        # dispatch 내부에서 예외가 나도 chat은 503 대신 정상 응답으로 끝나야 함
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_tool_call_response('get_next_semester_courses'),
            _mock_text_response('현재 추천을 못 줘서 미안'),
        ]

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '시간표 짜줘'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        # tool 결과에 error 키가 들어가 AI가 보고 사용자에게 안내
        second = mock_client.chat.completions.create.call_args_list[1]
        tool_msgs = [m for m in second.kwargs['messages'] if m['role'] == 'tool']
        self.assertTrue(any('error' in m['content'] for m in tool_msgs))


# ─── Step 3 (#102) — Notice / Information 검색 RAG ─────────────────────

from datetime import date as _date  # noqa: E402

from django.utils import timezone as _tz  # noqa: E402

from chat.tools import _tokenize_query, dispatch_tool_call  # noqa: E402
from information.models import Information as _Information  # noqa: E402
from notices.models import Notice as _Notice  # noqa: E402


class TokenizeQueryTests(TestCase):
    def test_basic_split(self):
        self.assertEqual(
            sorted(_tokenize_query('장학금 신청, 일정')),
            sorted(['장학금', '신청', '일정']),
        )

    def test_short_tokens_dropped(self):
        # 1자 토큰은 제외
        self.assertEqual(_tokenize_query('a 장학'), ['장학'])

    def test_empty_query(self):
        self.assertEqual(_tokenize_query(''), [])
        self.assertEqual(_tokenize_query('  ,. '), [])


class SearchNoticesDispatcherTests(TestCase):
    def setUp(self):
        self.now = _tz.now()
        _Notice.objects.create(
            source='scholarship',
            url='https://example.com/n1',
            title='2026 국가장학금 신청 안내',
            content='장학금 신청 일정 안내',
            published_at=self.now,
            tags=['장학금', '국가장학금'],
        )
        _Notice.objects.create(
            source='academic',
            url='https://example.com/n2',
            title='수강신청 일정 안내',
            content='수강신청 일정',
            published_at=self.now,
            tags=['수강신청', '학사'],
        )
        _Notice.objects.create(
            source='general',
            url='https://example.com/n3',
            title='도서관 휴관 안내',
            content='도서관 휴관',
            published_at=self.now,
            tags=['도서관'],
        )

    def test_keyword_matches_title_and_tags(self):
        out = dispatch_tool_call(None, 'search_notices', {'query': '장학금'})
        self.assertEqual(out['count'], 1)
        self.assertEqual(out['results'][0]['title'], '2026 국가장학금 신청 안내')
        self.assertEqual(out['results'][0]['url'], 'https://example.com/n1')

    def test_multi_token_query(self):
        out = dispatch_tool_call(None, 'search_notices', {'query': '수강신청 일정'})
        self.assertGreaterEqual(out['count'], 1)
        self.assertEqual(out['results'][0]['title'], '수강신청 일정 안내')

    def test_empty_query_returns_recent_fallback(self):
        # #131 — 빈 query는 의도(intent) 질문으로 간주, 최신 N건 fallback (이전: 0건)
        out = dispatch_tool_call(None, 'search_notices', {'query': ''})
        self.assertGreater(out['count'], 0)
        self.assertIn('의도 질문', out.get('note', ''))

    def test_no_match_returns_empty(self):
        out = dispatch_tool_call(None, 'search_notices', {'query': '존재하지않는키워드ABC'})
        self.assertEqual(out['count'], 0)


class SearchInformationDispatcherTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        today = _date.today()
        _Information.objects.create(
            source='wevity',
            source_id='test-i1',
            url='https://example.com/i1',
            title='2026 디자인 공모전',
            categories=['공모전', '디자인'],
            end_date=today + timedelta(days=10),
        )
        _Information.objects.create(
            source='wevity',
            source_id='test-i2',
            url='https://example.com/i2',
            title='지나간 공모전',
            categories=['공모전'],
            end_date=today - timedelta(days=5),
        )
        _Information.objects.create(
            source='wevity',
            source_id='test-i3',
            url='https://example.com/i3',
            title='개발 부트캠프 안내',
            categories=['부트캠프', 'IT'],
            end_date=None,
        )

    def test_keyword_matches_categories(self):
        out = dispatch_tool_call(None, 'search_information', {'query': '디자인 공모전'})
        titles = [r['title'] for r in out['results']]
        self.assertIn('2026 디자인 공모전', titles)
        self.assertNotIn('지나간 공모전', titles)

    def test_expired_excluded(self):
        out = dispatch_tool_call(None, 'search_information', {'query': '공모전'})
        titles = [r['title'] for r in out['results']]
        self.assertNotIn('지나간 공모전', titles)

    def test_no_end_date_included(self):
        out = dispatch_tool_call(None, 'search_information', {'query': '부트캠프'})
        self.assertEqual(out['count'], 1)
        self.assertEqual(out['results'][0]['title'], '개발 부트캠프 안내')


class ChatSearchToolIntegrationTests(TestCase):
    """search_notices tool이 chat 흐름에서 정상 호출되는지 통합."""

    def setUp(self):
        self.user = make_user('a@mju.ac.kr')
        self.room = ChatRoom.objects.create(user=self.user)
        ChatMessage.objects.create(room=self.room, role='user', content='prev')
        ChatMessage.objects.create(room=self.room, role='assistant', content='prev_a')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch('chat.tools._search_notices', return_value={
        'count': 1,
        'query': '장학금',
        'note': 'mock',
        'results': [{'title': '국가장학금 신청', 'url': 'x', 'source': 'scholarship'}],
    })
    @patch('chat.services.get_client')
    def test_chat_invokes_search_notices(self, mock_get_client, mock_search):
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_tool_call_response(
                'search_notices', arguments='{"query": "장학금"}',
            ),
            _mock_text_response('국가장학금 안내드릴게요'),
        ]

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '장학금 신청 시기 알려줘'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        mock_search.assert_called_once()
        second = mock_client.chat.completions.create.call_args_list[1]
        tool_msgs = [m for m in second.kwargs['messages'] if m['role'] == 'tool']
        self.assertTrue(any('국가장학금' in m['content'] for m in tool_msgs))


# ─── #145: 응답 포맷 규칙 (markdown 권장) ─────────────────────────────

class ResponseFormatGuideTests(TestCase):
    """CHAT_SYSTEM에 응답 포맷 규칙이 들어 있어야 함."""

    def test_system_prompt_allows_markdown(self):
        """#145 — markdown 금지에서 markdown 권장으로 반전."""
        from chat.prompts import CHAT_SYSTEM
        self.assertIn('markdown', CHAT_SYSTEM)
        self.assertIn('권장', CHAT_SYSTEM)
        # 권장 markdown 문법 표식 (굵게 + 링크)
        self.assertIn('**', CHAT_SYSTEM, 'CHAT_SYSTEM에 굵게 markdown 권장 표식 누락')
        self.assertIn('[제목](URL)', CHAT_SYSTEM, 'CHAT_SYSTEM에 링크 markdown 권장 표식 누락')

    def test_system_prompt_includes_notices_card_style(self):
        """notices/ai BUILD_CARDS_SYSTEM 톤앤매너 차용 검증."""
        from chat.prompts import CHAT_SYSTEM
        # 이모지·명사형 제목 가이드 포함 (음슴체는 #106에서 존댓말로 교체)
        for marker in ['이모지', '명사형']:
            self.assertIn(marker, CHAT_SYSTEM, f'CHAT_SYSTEM에 "{marker}" 가이드 누락')

    def test_system_prompt_enforces_polite_tone(self):
        """#106 — 사용자에게 말하는 문장은 무조건 존댓말."""
        from chat.prompts import CHAT_SYSTEM
        self.assertIn('존댓말', CHAT_SYSTEM)
        self.assertIn('반말', CHAT_SYSTEM)  # 반말 금지 명시 표식
        # 예시 블록에 존댓말 표지가 있어야 함 (반말 종결어미가 아닌 ~요/~습니다 형태)
        self.assertIn('정리해드릴게요', CHAT_SYSTEM)
        # 예시에서 반말 표현이 사라졌는지 spot check
        self.assertNotIn('정리해줬어', CHAT_SYSTEM)
        self.assertNotIn('찾아볼까?', CHAT_SYSTEM)

    @patch('chat.services.get_client')
    def test_format_guide_reaches_openai(self, mock_get_client):
        # 실 메시지 전송 시 system 메시지에 markdown 권장 가이드가 그대로 전달되는지
        user = make_user('format@mju.ac.kr')
        room = ChatRoom.objects.create(user=user)
        ChatMessage.objects.create(room=room, role='user', content='prev')
        ChatMessage.objects.create(room=room, role='assistant', content='prev_a')

        client = APIClient()
        client.force_authenticate(user)

        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = _mock_text_response('응답')

        res = client.post(
            messages_url(room.id),
            {'content': '뭐 좋은 거 있어?'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)

        sys_msg = mock_client.chat.completions.create.call_args.kwargs['messages'][0]
        self.assertEqual(sys_msg['role'], 'system')
        self.assertIn('markdown', sys_msg['content'])
        self.assertIn('권장', sys_msg['content'])
        self.assertIn('**', sys_msg['content'])


# ─── #108: 검색 tool 의도 분류 (교내 vs 교외) ──────────────────────────

class SearchToolIntentRoutingTests(TestCase):
    """tool description + CHAT_SYSTEM에 교내/교외 의도 분류 가이드 검증."""

    def test_tool_descriptions_disambiguate_intent(self):
        from chat.tools import TOOLS_SCHEMA
        by_name = {t['function']['name']: t['function'] for t in TOOLS_SCHEMA}

        notices_desc = by_name['search_notices']['description']
        info_desc = by_name['search_information']['description']

        # search_notices: 교내·명지대 자체임을 명시
        self.assertIn('교내', notices_desc)
        # search_information: 교외/외부 + 교내 의도 시 search_notices 사용 명시
        self.assertIn('교외', info_desc)
        self.assertIn('search_notices', info_desc)

    def test_system_prompt_includes_intent_guide(self):
        from chat.prompts import CHAT_SYSTEM
        for marker in ['교내', '교외', 'search_notices', 'search_information']:
            self.assertIn(marker, CHAT_SYSTEM, f'CHAT_SYSTEM에 "{marker}" 가이드 누락')


# ─── #131: 의도 질문(키워드 없는 최신 공지) fallback ─────────────────

class IntentQueryFallbackTests(TestCase):
    """stopword만 들어와 토큰이 비는 의도 질문 — 0건 대신 최신 N건 fallback (#131).

    "새로 뜬 공지 있어?" 같은 질문이 운영에서 0건으로 떨어지던 결함 해소.
    """

    def setUp(self):
        from datetime import date, timedelta
        from django.utils import timezone
        from notices.models import Notice
        from information.models import Information

        # 최근 공지 3개 (title에 '공지' 단어 없음 — 학교 게시판 실제 패턴 흉내)
        now = timezone.now()
        Notice.objects.create(
            source='academic',
            url='https://www.mju.ac.kr/bbs/notice/1',
            title='[학사] 2026학년도 1학기 종강일 안내',
            content='...',
            published_at=now - timedelta(days=1),
        )
        Notice.objects.create(
            source='scholarship',
            url='https://www.mju.ac.kr/bbs/notice/2',
            title='[장학] 2026 국가장학금 신청 안내',
            content='장학금 신청 기간 안내',
            published_at=now - timedelta(days=2),
        )
        Notice.objects.create(
            source='event',
            url='https://www.mju.ac.kr/bbs/notice/3',
            title='[행사] 봄 축제 개최 안내',
            content='...',
            published_at=now - timedelta(days=3),
        )

        # 마감 미경과 정보 2건
        today = date.today()
        Information.objects.create(
            source='wevity', source_id='1001',
            url='https://wevity.com/info/1',
            title='UX 디자인 공모전',
            description='...',
            categories=['공모전'],
            end_date=today + timedelta(days=10),
            is_active=True,
        )
        Information.objects.create(
            source='wevity', source_id='1002',
            url='https://wevity.com/info/2',
            title='청년 창업 지원사업',
            description='...',
            categories=['지원사업'],
            end_date=today + timedelta(days=5),
            is_active=True,
        )

    def test_tokenize_의도_질문은_빈_리스트(self):
        from chat.tools import _tokenize_query
        # "새로 뜬 공지 있어?" — 모든 토큰이 stopword
        self.assertEqual(_tokenize_query('새로 뜬 공지 있어?'), [])
        self.assertEqual(_tokenize_query('최근 공지 보여줘'), [])
        self.assertEqual(_tokenize_query('오늘 정보 있어'), [])

    def test_tokenize_의미있는_키워드는_보존(self):
        from chat.tools import _tokenize_query
        # stopword 섞여 있어도 의미 있는 단어는 살아남음
        self.assertEqual(_tokenize_query('장학금 알려줘'), ['장학금'])
        self.assertIn('수강신청', _tokenize_query('새로 뜬 수강신청 공지'))
        self.assertEqual(_tokenize_query('장학금'), ['장학금'])

    def test_search_notices_의도_질문이면_최신_N건(self):
        from chat.tools import _search_notices
        result = _search_notices({'query': '새로 뜬 공지 있어?'})
        self.assertEqual(result['count'], 3)
        # 게시일 내림차순 — 가장 최근 종강일 안내가 첫 결과
        self.assertEqual(result['results'][0]['title'], '[학사] 2026학년도 1학기 종강일 안내')
        # note에 fallback 명시 — AI가 이걸 보고 응답 톤을 조정 가능
        self.assertIn('의도 질문', result.get('note', ''))

    def test_search_notices_키워드_있으면_기존_검색_동작(self):
        from chat.tools import _search_notices
        result = _search_notices({'query': '장학금'})
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['results'][0]['title'], '[장학] 2026 국가장학금 신청 안내')

    def test_search_information_의도_질문이면_임박_N건(self):
        from chat.tools import _search_information
        result = _search_information({'query': '뭐 있어?'})
        self.assertEqual(result['count'], 2)
        # 마감 임박순 — 5일 남은 지원사업이 먼저
        self.assertEqual(result['results'][0]['title'], '청년 창업 지원사업')
        self.assertIn('의도 질문', result.get('note', ''))

    def test_search_information_키워드_있으면_기존_검색_동작(self):
        from chat.tools import _search_information
        result = _search_information({'query': '디자인'})
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['results'][0]['title'], 'UX 디자인 공모전')


# ─── #139: retrieve N+1 prefetch ─────────────────────────────────────

class ChatRoomRetrieveQueriesTests(TestCase):
    """ChatRoom retrieve 응답에 메시지·첨부 N+1 회귀 방어 (#139).

    메시지 수가 늘어도 쿼리 수가 일정함을 검증. get_queryset()의 retrieve 분기에서
    Prefetch('messages', ChatMessage.prefetch_related('attachments')) 적용으로
    쿼리 수가 1+1+1로 고정.
    """

    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.room = ChatRoom.objects.create(
            user=self.user, title='t', category='일반질문',
        )
        self.url = f'/api/v1/chat/rooms/{self.room.pk}/'

    def _add_messages_with_attachments(self, n):
        for i in range(n):
            m = ChatMessage.objects.create(
                room=self.room,
                role=ChatMessage.ROLE_USER,
                content=f'msg{i}',
            )
            ChatAttachment.objects.create(
                message=m,
                file=SimpleUploadedFile(f'f{i}.png', b'x', content_type='image/png'),
                file_type=ChatAttachment.FILE_TYPE_IMAGE,
                original_name=f'f{i}.png',
            )

    def test_retrieve_쿼리_수_메시지_수에_무관(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        # 메시지 3개 시나리오
        self._add_messages_with_attachments(3)
        with CaptureQueriesContext(connection) as ctx_small:
            res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        n_small = len(ctx_small.captured_queries)

        # 메시지 17개 추가 (총 20개)
        self._add_messages_with_attachments(17)
        with CaptureQueriesContext(connection) as ctx_large:
            res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        n_large = len(ctx_large.captured_queries)

        self.assertEqual(
            n_small, n_large,
            msg=f'N+1 회귀 — 메시지 3개 시 {n_small} 쿼리 vs 20개 시 {n_large} 쿼리',
        )

    def test_retrieve_응답_모양_변경_없음(self):
        """prefetch 적용 후에도 메시지·첨부 응답 모양이 동일해야 함"""
        self._add_messages_with_attachments(2)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        messages = res.data['messages']
        self.assertEqual(len(messages), 2)
        # 메시지 정렬: created_at 오름차순 (먼저 만든 게 먼저)
        self.assertEqual(messages[0]['content'], 'msg0')
        self.assertEqual(messages[1]['content'], 'msg1')
        # 각 메시지에 attachments nested
        for m in messages:
            self.assertEqual(len(m['attachments']), 1)
            for key in ('id', 'file', 'file_type', 'original_name', 'created_at'):
                self.assertIn(key, m['attachments'][0])


# ─── #147: ChatMessage.referenced_items snapshot ─────────────────────

def _mock_tool_call_response_multi(calls: list[tuple[str, str, str]]):
    """OpenAI 응답 — tool_calls 여러 건 한꺼번에 포함 (#147).

    calls: [(name, arguments_json, call_id), ...]
    """
    fns = []
    for name, args, cid in calls:
        fn = SimpleNamespace(arguments=args)
        fn.name = name
        fns.append(SimpleNamespace(id=cid, type='function', function=fn))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='', tool_calls=fns))]
    )


class AccumulateRefsUnitTests(TestCase):
    """services._accumulate_refs 단위 — name 매핑·dedup·error 응답 가드."""

    def test_search_notices는_type_notice로_적재(self):
        from chat.services import _accumulate_refs
        refs, seen = [], set()
        _accumulate_refs(
            'search_notices',
            {'results': [
                {'title': '국가장학금', 'url': 'https://mju.ac.kr/a'},
                {'title': '근로장학금', 'url': 'https://mju.ac.kr/b'},
            ]},
            refs, seen,
        )
        self.assertEqual(refs, [
            {'type': 'notice', 'title': '국가장학금', 'url': 'https://mju.ac.kr/a'},
            {'type': 'notice', 'title': '근로장학금', 'url': 'https://mju.ac.kr/b'},
        ])

    def test_search_information은_type_information으로_적재(self):
        from chat.services import _accumulate_refs
        refs, seen = [], set()
        _accumulate_refs(
            'search_information',
            {'results': [{'title': 'UX 공모전', 'url': 'https://wevity.com/x'}]},
            refs, seen,
        )
        self.assertEqual(refs, [
            {'type': 'information', 'title': 'UX 공모전', 'url': 'https://wevity.com/x'},
        ])

    def test_지원안하는_tool은_적재_안함(self):
        from chat.services import _accumulate_refs
        refs, seen = [], set()
        _accumulate_refs(
            'get_graduation_progress',
            {'progress_percent': 42},
            refs, seen,
        )
        self.assertEqual(refs, [])

    def test_같은_type_url_dedup(self):
        from chat.services import _accumulate_refs
        refs, seen = [], set()
        _accumulate_refs(
            'search_notices',
            {'results': [{'title': '제목', 'url': 'https://mju.ac.kr/x'}]},
            refs, seen,
        )
        # 두 번째 호출 — 같은 (type, url) → skip
        _accumulate_refs(
            'search_notices',
            {'results': [{'title': '제목 다른 케이스', 'url': 'https://mju.ac.kr/x'}]},
            refs, seen,
        )
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['title'], '제목')  # 첫 번째 것 유지

    def test_dispatcher_error_응답은_results_없어_자연스럽게_skip(self):
        from chat.services import _accumulate_refs
        refs, seen = [], set()
        _accumulate_refs(
            'search_notices',
            {'error': '내부 호출 실패: TypeError'},
            refs, seen,
        )
        self.assertEqual(refs, [])

    def test_title이나_url_빈값이면_skip(self):
        from chat.services import _accumulate_refs
        refs, seen = [], set()
        _accumulate_refs(
            'search_notices',
            {'results': [
                {'title': '', 'url': 'https://mju.ac.kr/a'},
                {'title': '정상', 'url': ''},
                {'title': '   ', 'url': 'https://mju.ac.kr/b'},
                {'title': '정상2', 'url': 'https://mju.ac.kr/c'},
            ]},
            refs, seen,
        )
        self.assertEqual(refs, [
            {'type': 'notice', 'title': '정상2', 'url': 'https://mju.ac.kr/c'},
        ])


class ChatMessageReferencedItemsTests(TestCase):
    """send_message 흐름에서 ChatMessage.referenced_items 적재 검증 (#147)."""

    def setUp(self):
        self.user = make_user('refs@mju.ac.kr')
        self.room = ChatRoom.objects.create(user=self.user)
        # 첫 메시지가 아니어서 classify_and_title 안 거치도록 prev 메시지 1쌍 깔아둠
        ChatMessage.objects.create(room=self.room, role='user', content='prev')
        ChatMessage.objects.create(room=self.room, role='assistant', content='prev_a')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch('chat.tools._search_notices', return_value={
        'count': 2,
        'results': [
            {'title': '국가장학금', 'url': 'https://mju.ac.kr/a'},
            {'title': '근로장학금', 'url': 'https://mju.ac.kr/b'},
        ],
    })
    @patch('chat.services.get_client')
    def test_search_notices_결과가_referenced_items로_저장됨(
        self, mock_get_client, _mock_search,
    ):
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_tool_call_response('search_notices', arguments='{"query": "장학금"}'),
            _mock_text_response('장학금 정리해드릴게요'),
        ]

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '장학금 알려줘'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['referenced_items'], [
            {'type': 'notice', 'title': '국가장학금', 'url': 'https://mju.ac.kr/a'},
            {'type': 'notice', 'title': '근로장학금', 'url': 'https://mju.ac.kr/b'},
        ])
        # DB에도 동일하게 저장
        assistant_msg = ChatMessage.objects.filter(
            room=self.room, role=ChatMessage.ROLE_ASSISTANT,
        ).order_by('-id').first()
        self.assertEqual(len(assistant_msg.referenced_items), 2)

    @patch('chat.tools._search_information', return_value={
        'count': 1,
        'results': [{'title': 'UX 공모전', 'url': 'https://wevity.com/x'}],
    })
    @patch('chat.services.get_client')
    def test_search_information_결과는_type_information으로_저장(
        self, mock_get_client, _mock_search,
    ):
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_tool_call_response('search_information', arguments='{"query": "공모전"}'),
            _mock_text_response('공모전 정리'),
        ]

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '공모전 알려줘'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['referenced_items'], [
            {'type': 'information', 'title': 'UX 공모전', 'url': 'https://wevity.com/x'},
        ])

    @patch('chat.tools._search_information', return_value={
        'count': 1,
        'results': [{'title': '외부 공모전', 'url': 'https://wevity.com/y'}],
    })
    @patch('chat.tools._search_notices', return_value={
        'count': 1,
        'results': [{'title': '교내 행사', 'url': 'https://mju.ac.kr/c'}],
    })
    @patch('chat.services.get_client')
    def test_두_tool_모두_호출시_두_type_섞여_저장(
        self, mock_get_client, _m_notices, _m_info,
    ):
        # 한 응답에서 tool_calls 두 건 동시 호출 케이스
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_tool_call_response_multi([
                ('search_notices', '{"query": "공모전"}', 'c1'),
                ('search_information', '{"query": "공모전"}', 'c2'),
            ]),
            _mock_text_response('교내·교외 정리'),
        ]

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '공모전 알려줘'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        items = res.data['referenced_items']
        types = {it['type'] for it in items}
        self.assertEqual(types, {'notice', 'information'})
        self.assertEqual(len(items), 2)

    @patch('chat.services.get_client')
    def test_tool_호출_없는_일반_응답은_빈_배열(self, mock_get_client):
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = _mock_text_response('안녕하세요')

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '안녕'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['referenced_items'], [])

    def test_user_메시지는_항상_빈_배열(self):
        # send_message가 만들어주는 user_msg
        with patch('chat.services.get_client') as mock_get_client:
            mock_client = mock_get_client.return_value
            mock_client.chat.completions.create.return_value = _mock_text_response('응답')
            res = self.client.post(
                messages_url(self.room.id),
                {'content': '안녕'},
                format='json',
            )
            self.assertEqual(res.status_code, 201)

        user_msg = ChatMessage.objects.filter(
            room=self.room, role=ChatMessage.ROLE_USER, content='안녕',
        ).first()
        self.assertEqual(user_msg.referenced_items, [])

    @patch('chat.tools._search_notices', return_value={
        'count': 1,
        'results': [{'title': '제목', 'url': 'https://mju.ac.kr/dedup'}],
    })
    @patch('chat.services.get_client')
    def test_같은_url_두번_나오면_dedup(self, mock_get_client, _mock_search):
        # 같은 tool을 같은 query로 두 round 호출 → 두 round 결과 동일 → dedup으로 1건
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_tool_call_response('search_notices', arguments='{"query": "x"}', call_id='c1'),
            _mock_tool_call_response('search_notices', arguments='{"query": "x"}', call_id='c2'),
            _mock_text_response('완료'),
        ]

        res = self.client.post(
            messages_url(self.room.id),
            {'content': '뭐 있어'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(len(res.data['referenced_items']), 1)

    @patch('chat.tools._search_notices', return_value={
        'count': 1,
        'results': [{'title': 'GET 검증', 'url': 'https://mju.ac.kr/get-check'}],
    })
    @patch('chat.services.get_client')
    def test_GET_room_상세에서_referenced_items_노출(
        self, mock_get_client, _mock_search,
    ):
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_tool_call_response('search_notices', arguments='{"query": "x"}'),
            _mock_text_response('OK'),
        ]
        self.client.post(
            messages_url(self.room.id),
            {'content': '확인'},
            format='json',
        )

        res = self.client.get(f'/api/v1/chat/rooms/{self.room.id}/')
        self.assertEqual(res.status_code, 200)
        for m in res.data['messages']:
            self.assertIn('referenced_items', m)
        # 마지막 assistant 메시지에 검색 결과 들어있어야 함
        last_assistant = [m for m in res.data['messages'] if m['role'] == 'assistant'][-1]
        self.assertEqual(last_assistant['referenced_items'], [
            {'type': 'notice', 'title': 'GET 검증', 'url': 'https://mju.ac.kr/get-check'},
        ])


# ─── #193: 챗 다음학기 추천 — 개설학기 fallback ──────────────────────
from courses.models import (  # noqa: E402
    Course as _Course,
    CourseOffering as _CourseOffering,
)


class NextSemesterFallbackDispatcherTests(TestCase):
    """챗 get_next_semester_courses — 요청 학기 개설 데이터 없으면 작년 같은 학기로
    fallback해서 실제 과목을 주고, 응답에 기준 학기 + fallback 플래그를 싣는다 (#193).

    그동안 챗 경로는 fallback이 없어 빈 추천만 반환했음. (섹션 경로엔 이미 있었음)
    """

    def setUp(self):
        self.user = make_user('fb@mju.ac.kr')
        self.user.major = '컴퓨터공학'
        self.user.grade = 2
        self.user.semester = 1
        self.user.admission_year = 2024
        self.user.save()
        # 2025-2 학기에만 개설된 과목 — 2026-2 요청 시 fallback 대상
        self.course = _Course.objects.create(
            course_code='CSE2050', name='자료구조', college='ICT융합대학',
            department='융합소프트웨어학부', major='컴퓨터공학',
            category='전공선택', credits=3, year_open=2, semester_open=2,
        )
        _CourseOffering.objects.create(
            course=self.course, year=2025, semester=2,
            section_no='01', professor='김교수',
        )

    def test_요청학기_데이터없으면_작년학기로_fallback하고_과목제공(self):
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 2},  # 데이터 없는 학기
        )
        # 작년 같은 학기(2025-2)로 치환된 학기가 응답에 명시됨
        self.assertEqual(out['target_year'], 2025)
        self.assertEqual(out['target_semester'], 2)
        self.assertTrue(out['fallback_term'])
        # 빈 추천이 아니라 실제 과목이 나와야 함 (버그 핵심)
        self.assertGreaterEqual(out['count'], 1)
        codes = [c['course_code'] for c in out['courses']]
        self.assertIn('CSE2050', codes)
        # AI가 사용자에게 안내하도록 note에 기준 학기 명시
        self.assertIn('2025-2학기', out['note'])

    def test_fallback시_다음학기는_요청학기로_보존된다(self):
        # #205: fallback 학기(2025-2)를 '다음 학기'로 잘못 안내하던 버그 회귀 방지.
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 2},  # 데이터 없는 학기
        )
        # 사용자 관점의 다음 학기(2026-2)는 fallback에 덮이지 않고 별도 보존됨
        self.assertEqual((out['requested_year'], out['requested_semester']), (2026, 2))
        # 추천 데이터 출처는 작년 같은 학기(2025-2)
        self.assertEqual((out['target_year'], out['target_semester']), (2025, 2))
        # note는 두 학기를 모두 담고, fallback 학기를 '다음 학기'라 부르지 말라고 지시
        self.assertIn('2026-2학기', out['note'])
        self.assertIn('2025-2학기', out['note'])
        self.assertIn('"다음 학기"라고 부르지 말 것', out['note'])

    def test_요청학기_데이터있으면_fallback안함(self):
        _CourseOffering.objects.create(
            course=self.course, year=2026, semester=2,
            section_no='02', professor='이교수',
        )
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 2},
        )
        self.assertEqual((out['target_year'], out['target_semester']), (2026, 2))
        # fallback 아니면 requested == target
        self.assertEqual((out['requested_year'], out['requested_semester']), (2026, 2))
        self.assertFalse(out['fallback_term'])
        self.assertNotIn('기준으로 추천한 것이다', out['note'])


# ─── #199: 챗 다음학기 추천 — 분반(Offering) 단위 시간표 그룹화 ──────────
from courses.models import CourseSchedule as _CourseSchedule  # noqa: E402


class NextSemesterOfferingGroupingTests(TestCase):
    """챗 get_next_semester_courses — 한 과목의 여러 분반 시간이 course 단위로
    평탄화되지 않고 분반(Offering)별로 그룹화돼 나와야 한다 (#199).

    버그 전: c.schedules.all()로 전체 분반 schedule을 한 배열에 합쳐, 서로 다른
    분반의 시간이 한 과목으로 섞임(예: 0693 조세형 화요일 + 0694 현상원 월/수요일)
    → 실재할 수 없는 시간표. 분반 경계를 유지해 AI가 분반 하나를 통째로 고르게 한다.
    """

    def setUp(self):
        from datetime import time
        self.user = make_user('og@mju.ac.kr')
        self.user.major = '컴퓨터공학'
        self.user.grade = 2
        self.user.semester = 1
        self.user.admission_year = 2024
        self.user.save()
        self.course = _Course.objects.create(
            course_code='CSE2100', name='객체지향프로그래밍1', college='ICT융합대학',
            department='융합소프트웨어학부', major='컴퓨터공학',
            category='전공선택', credits=3, year_open=2, semester_open=1,
        )
        # 분반 A(0693 조세형) — 화 09:00~11:50
        off_a = _CourseOffering.objects.create(
            course=self.course, year=2026, semester=1,
            section_no='0693', professor='조세형',
        )
        _CourseSchedule.objects.create(
            course=self.course, offering=off_a,
            day_of_week='화', start_time=time(9, 0), end_time=time(11, 50),
        )
        # 분반 B(0694 현상원) — 월 11:00~11:50, 수 13:00~14:50
        off_b = _CourseOffering.objects.create(
            course=self.course, year=2026, semester=1,
            section_no='0694', professor='현상원',
        )
        _CourseSchedule.objects.create(
            course=self.course, offering=off_b,
            day_of_week='월', start_time=time(11, 0), end_time=time(11, 50),
        )
        _CourseSchedule.objects.create(
            course=self.course, offering=off_b,
            day_of_week='수', start_time=time(13, 0), end_time=time(14, 50),
        )

    def _find_course(self, out):
        for c in out['courses']:
            if c['course_code'] == 'CSE2100':
                return c
        self.fail('CSE2100 추천에 없음')

    def test_분반별로_offerings_그룹화되고_시간이_안섞임(self):
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 1},
        )
        c = self._find_course(out)
        # course 단위 평탄화 키는 더 이상 없음 (분반 경계 깨짐 방지)
        self.assertNotIn('schedules', c)
        self.assertNotIn('professor', c)
        # 분반 2개가 각각 분리돼 나옴
        self.assertEqual(len(c['offerings']), 2)
        by_sec = {o['section_no']: o for o in c['offerings']}
        self.assertEqual(set(by_sec), {'0693', '0694'})
        # 분반 A: 교수·시간이 자기 것(화요일)만
        a = by_sec['0693']
        self.assertEqual(a['professor'], '조세형')
        self.assertEqual({s['day_of_week'] for s in a['schedules']}, {'화'})
        # 분반 B: 월/수만, 분반 A의 화요일이 섞이지 않음
        b = by_sec['0694']
        self.assertEqual(b['professor'], '현상원')
        self.assertEqual({s['day_of_week'] for s in b['schedules']}, {'월', '수'})

    def test_note에_분반_하나만_고르라는_지침_포함(self):
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 1},
        )
        # AI가 분반을 섞지 않도록 안내 문구가 note에 있어야 함
        self.assertIn('분반', out['note'])


# ─── #202: 챗 과목 추천 응답에 추천 이유(reason) 코드 노출 ──────────────
class NextSemesterReasonsDispatcherTests(TestCase):
    """챗 get_next_semester_courses 응답의 각 과목에 추천 이유 코드(reasons)가
    실려야 한다 (#202). 점수·순위는 안 바꾸고 "왜 추천됐는지"만 노출.
    """

    def setUp(self):
        self.user = make_user('reason@mju.ac.kr')
        self.user.major = '컴퓨터공학'
        self.user.grade = 2
        self.user.semester = 1
        self.user.admission_year = 2024
        self.user.save()
        # 전공필수 과목 — major_required 이유가 걸려야 함
        self.course = _Course.objects.create(
            course_code='CSE2200', name='자료구조', college='ICT융합대학',
            department='융합소프트웨어학부', major='컴퓨터공학',
            category='전공필수', credits=3, year_open=2, semester_open=1,
        )
        _CourseOffering.objects.create(
            course=self.course, year=2026, semester=1,
            section_no='01', professor='김교수',
        )

    def test_각_과목에_reasons_키_존재(self):
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 1},
        )
        self.assertGreaterEqual(out['count'], 1)
        for c in out['courses']:
            self.assertIn('reasons', c)
            self.assertIsInstance(c['reasons'], list)

    def test_전공필수는_major_required_이유(self):
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 1},
        )
        target = next(c for c in out['courses'] if c['course_code'] == 'CSE2200')
        self.assertIn('major_required', target['reasons'])

    def test_note에_reason_코드_해설_포함(self):
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 1},
        )
        # AI가 코드 의미를 알도록 note에 reasons 해설이 있어야 함
        self.assertIn('reasons', out['note'])
        self.assertIn('major_required', out['note'])


# ─── 챗 추천 응답에 현재 상황 브리핑(이수현황 status) 동봉 ──────────────────
from courses.models import GraduationRequirement as _GraduationRequirement  # noqa: E402
from accounts.models import CourseHistory as _CourseHistory  # noqa: E402


class NextSemesterStatusBriefingTests(TestCase):
    """챗 get_next_semester_courses 응답에 사용자의 졸업요건 이수현황(status)이 실려야 한다.

    그동안 챗 추천은 과목만 나열하고 "현재 상황"(전공 몇 학점·필수 잔여 등)을 브리핑할
    데이터가 없었음. status를 동봉해 AI가 추천 전 브리핑할 수 있게 한다.
    """

    def setUp(self):
        self.user = make_user('briefing@mju.ac.kr')
        self.user.major = '컴퓨터공학'
        self.user.grade = 2
        self.user.semester = 1
        self.user.admission_year = 2024
        self.user.chapel_count = 1
        self.user.save()
        # 졸업요건 — 전공필수 42 / 전공선택 24
        _GraduationRequirement.objects.create(
            department='컴퓨터공학', admission_year=2024,
            category='전공필수', required_credits=42, total_required=130,
        )
        _GraduationRequirement.objects.create(
            department='컴퓨터공학', admission_year=2024,
            category='전공선택', required_credits=24, total_required=130,
        )
        # 이수이력 — 전공필수 3학점
        _CourseHistory.objects.create(
            user=self.user, course_name='프로그래밍기초', course_code='CSE1001',
            year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
        )
        # 추천 풀에 들어갈 과목 1개
        self.course = _Course.objects.create(
            course_code='CSE2300', name='자료구조', college='ICT융합대학',
            department='융합소프트웨어학부', major='컴퓨터공학',
            category='전공필수', credits=3, year_open=2, semester_open=1,
        )
        _CourseOffering.objects.create(
            course=self.course, year=2026, semester=1,
            section_no='01', professor='김교수',
        )

    def test_추천_응답에_status_블록_존재(self):
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 1},
        )
        self.assertIn('status', out)
        status_blk = out['status']
        # 7분류 카테고리 + 총계 + 채플
        self.assertEqual(len(status_blk['categories']), 7)
        self.assertEqual(status_blk['total_required'], 130)
        self.assertEqual(status_blk['chapel']['required'], 4)
        self.assertEqual(status_blk['chapel']['completed'], 1)

    def test_status_카테고리에_잔여학점_반영(self):
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 1},
        )
        major = next(
            c for c in out['status']['categories'] if c['category'] == '전공필수'
        )
        self.assertEqual(major['completed'], 3)
        self.assertEqual(major['remaining'], 39)  # 42 - 3

    def test_compact_status는_areas_상세_제외(self):
        # 추천 응답의 status는 토큰 가드용 슬림 버전 — 영역/필수과목 상세는 빼고 학점만
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 1},
        )
        for c in out['status']['categories']:
            self.assertNotIn('areas', c)
            self.assertNotIn('required_courses', c)

    def test_note에_브리핑_지침_포함(self):
        out = dispatch_tool_call(
            self.user, 'get_next_semester_courses',
            {'target_year': 2026, 'target_semester': 1},
        )
        # AI가 추천 전 status로 브리핑하도록 note에 지침이 있어야 함
        self.assertIn('status', out['note'])
        self.assertIn('브리핑', out['note'])


class CompletionStatusDispatcherTests(TestCase):
    """챗 독립 tool get_completion_status — 학점 이수현황 full 반환."""

    def setUp(self):
        self.user = make_user('status@mju.ac.kr')
        self.user.major = '컴퓨터공학'
        self.user.admission_year = 2024
        self.user.save()
        _GraduationRequirement.objects.create(
            department='컴퓨터공학', admission_year=2024,
            category='전공필수', required_credits=42, total_required=130,
        )
        _CourseHistory.objects.create(
            user=self.user, course_name='프로그래밍기초', course_code='CSE1001',
            year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
        )

    def test_이수현황_dispatch_반환(self):
        out = dispatch_tool_call(self.user, 'get_completion_status', {})
        self.assertIn('categories', out)
        self.assertIn('chapel', out)
        self.assertEqual(out['total_required'], 130)
        self.assertEqual(out['total_completed'], 3)
        self.assertIn('note', out)
        major = next(c for c in out['categories'] if c['category'] == '전공필수')
        self.assertEqual(major['remaining'], 39)

    def test_full_status는_areas_required_courses_키_포함(self):
        # 독립 tool은 슬림 아님 — 영역/필수과목 분해 키가 있어야 함 (None이어도 키 존재)
        out = dispatch_tool_call(self.user, 'get_completion_status', {})
        for c in out['categories']:
            self.assertIn('areas', c)
            self.assertIn('required_courses', c)


class CourseHistoryDispatcherTests(TestCase):
    """챗 get_course_history — 사용자가 들은(이수 완료) 과목 목록 반환 (#211).

    "내가 들은 과목" 질문에 추천/현재 과목이 아니라 실제 수강이력을 줘야 한다.
    """

    def setUp(self):
        self.user = make_user('history@mju.ac.kr')
        self.user.major = '컴퓨터공학'
        self.user.admission_year = 2024
        self.user.save()
        # 일부러 역순 입력 — 응답은 연도·학기 오름차순으로 정렬돼야 함
        _CourseHistory.objects.create(
            user=self.user, course_name='자료구조', course_code='CSE2001',
            year=2025, semester=1, grade_received='B+', category='전공필수', credits=3,
        )
        _CourseHistory.objects.create(
            user=self.user, course_name='프로그래밍기초', course_code='CSE1001',
            year=2024, semester=1, grade_received='A', category='전공필수', credits=3,
        )

    def test_들은_과목_목록_반환(self):
        out = dispatch_tool_call(self.user, 'get_course_history', {})
        self.assertEqual(out['count'], 2)
        self.assertIn('note', out)
        first = out['courses'][0]
        for key in (
            'course_name', 'course_code', 'year', 'semester',
            'category', 'credits', 'grade_received',
        ):
            self.assertIn(key, first)

    def test_연도_학기_오름차순_정렬(self):
        out = dispatch_tool_call(self.user, 'get_course_history', {})
        keys = [(c['year'], c['semester'], c['course_code']) for c in out['courses']]
        self.assertEqual(keys, sorted(keys))
        # 2024-1 프로그래밍기초가 2025-1 자료구조보다 앞
        self.assertEqual(out['courses'][0]['course_name'], '프로그래밍기초')

    def test_수강이력_없으면_빈_목록(self):
        empty_user = make_user('empty@mju.ac.kr')
        out = dispatch_tool_call(empty_user, 'get_course_history', {})
        self.assertEqual(out['count'], 0)
        self.assertEqual(out['courses'], [])

    def test_다른_사용자_이력은_안_섞임(self):
        other = make_user('other@mju.ac.kr')
        _CourseHistory.objects.create(
            user=other, course_name='타인과목', course_code='XXX9999',
            year=2024, semester=1, grade_received='A', category='전공선택', credits=3,
        )
        out = dispatch_tool_call(self.user, 'get_course_history', {})
        names = [c['course_name'] for c in out['courses']]
        self.assertNotIn('타인과목', names)
        self.assertEqual(out['count'], 2)
