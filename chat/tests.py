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
        # generate_assistant_reply(user, history) — 두 번째 인자가 history
        history_arg = mock_reply.call_args.args[1]
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

    def test_empty_query_graceful(self):
        out = dispatch_tool_call(None, 'search_notices', {'query': ''})
        self.assertEqual(out['count'], 0)
        self.assertEqual(out['results'], [])

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


# ─── #104: 응답 포맷 규칙 (markdown 제거) ─────────────────────────────

class ResponseFormatGuideTests(TestCase):
    """CHAT_SYSTEM에 응답 포맷 규칙이 들어 있어야 함."""

    def test_system_prompt_includes_markdown_ban(self):
        from chat.prompts import CHAT_SYSTEM
        self.assertIn('마크다운', CHAT_SYSTEM)
        # 핵심 금지 사항 명시 표식
        for marker in ['markdown', '평문', 'URL']:
            self.assertIn(marker, CHAT_SYSTEM, f'CHAT_SYSTEM에 "{marker}" 표식 누락')

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
        # 실 메시지 전송 시 system 메시지에 포맷 가이드가 그대로 전달되는지
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
        self.assertIn('마크다운', sys_msg['content'])
        self.assertIn('금지', sys_msg['content'])


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
