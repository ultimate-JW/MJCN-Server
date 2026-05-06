"""VLM 전처리 단위 테스트 (OpenAI 호출은 모두 mock).

spec 9.1.6 — 이미지 → 텍스트 추출 + 후속 텍스트 파이프라인 자동 재처리.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone
from requests.exceptions import ConnectionError as requests_ConnectionError

from notices.ai import pipeline, processor, vlm
from notices.ai.client import AIClientError
from notices.models import Notice, NoticeAIResult


DEFAULT_LONG_CONTENT = '테스트용 공지 본문임. 충분히 길어서 LLM 호출이 진행되어야 한다는 것을 보장.'


def make_notice(*, content='', image_urls=None, title='이미지공지'):
    return Notice.objects.create(
        source='academic',
        title=title,
        content=content,
        image_urls=image_urls or [],
        url=f'https://www.mju.ac.kr/test/{title}',
        published_at=timezone.now(),
    )


def fake_chat_response(text: str):
    """openai SDK chat.completions.create 응답 mock."""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


# --- extract_text_from_images ---

class ExtractTextFromImagesTests(TestCase):

    def test_정상_추출(self):
        client = MagicMock()
        client.chat.completions.create.return_value = fake_chat_response('포스터 안내문 내용')
        with patch.object(vlm, 'get_client', return_value=client), \
             patch.object(vlm, '_fetch_image_as_data_url',
                          return_value='data:image/png;base64,xxx'):
            result = vlm.extract_text_from_images(['https://x/1.png'])
        self.assertEqual(result, '포스터 안내문 내용')

    def test_빈_image_urls는_ValueError(self):
        with self.assertRaises(ValueError):
            vlm.extract_text_from_images([])

    @override_settings(OPENAI_VLM_MAX_IMAGES=2)
    def test_이미지_개수_한도_적용(self):
        client = MagicMock()
        client.chat.completions.create.return_value = fake_chat_response('ok')
        with patch.object(vlm, 'get_client', return_value=client), \
             patch.object(vlm, '_fetch_image_as_data_url',
                          return_value='data:image/png;base64,xxx'):
            vlm.extract_text_from_images(['u1', 'u2', 'u3', 'u4'])

        # user 메시지 콘텐츠에 image_url이 2개만 들어가야 함 (text 1 + image 2)
        call_args = client.chat.completions.create.call_args
        messages = call_args.kwargs['messages']
        user_content = messages[1]['content']
        image_blocks = [b for b in user_content if b['type'] == 'image_url']
        self.assertEqual(len(image_blocks), 2)

    def test_API_실패시_AIClientError(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError('boom')
        with patch.object(vlm, 'get_client', return_value=client), \
             patch.object(vlm, '_fetch_image_as_data_url',
                          return_value='data:image/png;base64,xxx'):
            with self.assertRaises(AIClientError):
                vlm.extract_text_from_images(['u1'])

    def test_빈_응답은_에러(self):
        client = MagicMock()
        client.chat.completions.create.return_value = fake_chat_response('')
        with patch.object(vlm, 'get_client', return_value=client), \
             patch.object(vlm, '_fetch_image_as_data_url',
                          return_value='data:image/png;base64,xxx'):
            with self.assertRaises(AIClientError):
                vlm.extract_text_from_images(['u1'])

    def test_이미지_다운로드_실패시_AIClientError(self):
        client = MagicMock()
        with patch.object(vlm, 'get_client', return_value=client), \
             patch.object(vlm, '_fetch_image_as_data_url',
                          side_effect=requests_ConnectionError('cannot fetch')):
            with self.assertRaises(AIClientError) as ctx:
                vlm.extract_text_from_images(['https://x/1.png'])
            self.assertIn('이미지 다운로드 실패', str(ctx.exception))


# --- process_notice_image ---

class ProcessNoticeImageTests(TestCase):

    def test_image_urls_없으면_skipped(self):
        n = make_notice(content='본문', image_urls=[])
        action = vlm.process_notice_image(n)
        self.assertEqual(action, 'skipped')

    def test_이미_extracted_있으면_skipped(self):
        n = make_notice(content='', image_urls=['u1'])
        n.extracted_content = '이미 추출됨'
        n.save()
        action = vlm.process_notice_image(n)
        self.assertEqual(action, 'skipped')

    def test_force면_재추출(self):
        n = make_notice(content='', image_urls=['u1'])
        n.extracted_content = '예전 추출'
        n.save()
        with patch.object(vlm, 'extract_text_from_images', return_value='새로 추출'):
            action = vlm.process_notice_image(n, force=True)
        self.assertEqual(action, 'success')
        n.refresh_from_db()
        self.assertEqual(n.extracted_content, '새로 추출')

    def test_정상_추출_저장(self):
        n = make_notice(content='', image_urls=['u1', 'u2'])
        with patch.object(vlm, 'extract_text_from_images', return_value='추출 텍스트'):
            action = vlm.process_notice_image(n)
        self.assertEqual(action, 'success')
        n.refresh_from_db()
        self.assertEqual(n.extracted_content, '추출 텍스트')

    def test_추출_실패시_failed_저장은_안함(self):
        n = make_notice(content='', image_urls=['u1'])
        with patch.object(vlm, 'extract_text_from_images',
                          side_effect=AIClientError('API down')):
            action = vlm.process_notice_image(n)
        self.assertEqual(action, 'failed')
        n.refresh_from_db()
        self.assertEqual(n.extracted_content, '')


# --- get_vlm_targets ---

class GetVLMTargetsTests(TestCase):

    def setUp(self):
        # n1: 추출 대상 (content 비고 image 있음)
        self.n1 = make_notice(content='', image_urls=['u1'], title='n1')
        # n2: 이미 추출됨
        self.n2 = make_notice(content='', image_urls=['u2'], title='n2')
        self.n2.extracted_content = '이미 추출'
        self.n2.save()
        # n3: 이미지 없음 → 대상 아님
        self.n3 = make_notice(content='텍스트있음', image_urls=[], title='n3')

    def test_기본은_미추출만(self):
        qs = vlm.get_vlm_targets()
        self.assertEqual(set(qs.values_list('id', flat=True)), {self.n1.id})

    def test_reprocess는_이미_추출된것도_포함(self):
        qs = vlm.get_vlm_targets(reprocess=True)
        ids = set(qs.values_list('id', flat=True))
        self.assertIn(self.n1.id, ids)
        self.assertIn(self.n2.id, ids)
        self.assertNotIn(self.n3.id, ids)  # image_urls 없으면 reprocess여도 제외

    def test_source_필터(self):
        # 모두 academic이라 두 개 다 포함
        qs = vlm.get_vlm_targets(sources=['academic'])
        self.assertEqual(qs.count(), 1)

    def test_ids_필터(self):
        qs = vlm.get_vlm_targets(ids=[self.n2.id], reprocess=True)
        self.assertEqual(set(qs.values_list('id', flat=True)), {self.n2.id})


# --- 텍스트 파이프라인이 extracted_content 우선 사용하는지 ---

class EffectiveContentRoutingTests(TestCase):
    """spec 9.1.1: extracted_content 있으면 그것을, 없으면 content를 파이프라인에 입력."""

    def test_extracted_content가_있으면_그것을_파이프라인에_전달(self):
        extracted = '추출된 본문임. 충분히 길어서 LLM 호출이 실제로 진행되도록 함.'
        n = make_notice(content='', image_urls=['u1'])
        n.extracted_content = extracted
        n.save()

        with patch.object(pipeline, 'classify', return_value='행동형') as cls, \
             patch.object(pipeline, 'summarize', return_value='요약'), \
             patch.object(pipeline, 'build_cards', return_value=[{'title': 'T', 'items': ['x']}]):
            processor.process_notice(n)

        cls.assert_called_once_with(extracted)

    def test_extracted_없으면_content_사용(self):
        original = '원본 공지 본문임. 충분히 길어서 파이프라인이 진행되도록 보장.'
        n = make_notice(content=original, image_urls=[])

        with patch.object(pipeline, 'classify', return_value='정보형') as cls, \
             patch.object(pipeline, 'summarize', return_value='요약'), \
             patch.object(pipeline, 'build_cards', return_value=[{'title': 'T', 'items': ['x']}]):
            processor.process_notice(n)

        cls.assert_called_once_with(original)

    def test_VLM_추출_후_content_hash_변경되어_자동_재처리(self):
        # 1차: content 비어있는 상태에서 처리 시도 → empty_content로 마킹됨 (LLM 호출 안 함)
        n = make_notice(content='', image_urls=['u1'])
        _, action1 = processor.process_notice(n)
        self.assertEqual(action1, 'skipped')
        n.refresh_from_db()
        self.assertEqual(n.ai_result.status, 'empty_content')

        # VLM이 extracted_content를 채움 → content_hash 변경
        n.extracted_content = '이미지에서 추출한 풍부한 본문임. LLM이 의미있는 처리를 하기에 충분한 길이.'
        n.save()

        # 다시 process_notice 호출 → 본문 변경 감지로 재처리
        with patch.object(pipeline, 'classify', return_value='행동형'), \
             patch.object(pipeline, 'summarize', return_value='재처리된 요약'), \
             patch.object(pipeline, 'build_cards', return_value=[{'title': 'T', 'items': ['x']}]):
            _, action = processor.process_notice(n)

        n.refresh_from_db()
        self.assertEqual(action, 'processed')
        self.assertEqual(n.ai_result.status, 'success')
        self.assertEqual(n.ai_result.summary, '재처리된 요약')
        self.assertEqual(n.ai_result.notice_type, '행동형')


# --- effective_content 프로퍼티 ---

class EffectiveContentPropertyTests(TestCase):

    def test_extracted있으면_그것_반환(self):
        n = make_notice(content='c', image_urls=['u'])
        n.extracted_content = 'e'
        self.assertEqual(n.effective_content, 'e')

    def test_extracted_빈문자열이면_content_반환(self):
        n = make_notice(content='c')
        self.assertEqual(n.effective_content, 'c')

    def test_둘_다_비면_빈문자열(self):
        n = make_notice(content='')
        self.assertEqual(n.effective_content, '')


class EmptyContentGuardTests(TestCase):
    """본문이 너무 짧으면 LLM 호출 없이 'empty_content' 상태로 마킹."""

    def test_빈_본문은_LLM_호출_없이_empty_content(self):
        n = make_notice(content='', image_urls=[])
        with patch.object(pipeline, 'classify') as cls, \
             patch.object(pipeline, 'summarize') as smr, \
             patch.object(pipeline, 'build_cards') as bld:
            _, action = processor.process_notice(n)

        self.assertEqual(action, 'skipped')
        n.refresh_from_db()
        self.assertEqual(n.ai_result.status, 'empty_content')
        cls.assert_not_called()
        smr.assert_not_called()
        bld.assert_not_called()

    def test_매우_짧은_본문도_empty_content(self):
        n = make_notice(content='짧음.', image_urls=[])
        _, action = processor.process_notice(n)
        self.assertEqual(action, 'skipped')
        n.refresh_from_db()
        self.assertEqual(n.ai_result.status, 'empty_content')

    def test_empty_content_이후_재실행은_skipped(self):
        n = make_notice(content='', image_urls=[])
        processor.process_notice(n)
        # 재실행: 본문 그대로면 다시 LLM 호출 안 함
        with patch.object(pipeline, 'classify') as cls:
            _, action = processor.process_notice(n)
        self.assertEqual(action, 'skipped')
        cls.assert_not_called()

    def test_get_pending_notices에서_empty_content_제외(self):
        n_empty = make_notice(content='', image_urls=[], title='empty')
        processor.process_notice(n_empty)  # → empty_content 마킹
        n_normal = make_notice(title='normal')

        qs = processor.get_pending_notices()
        ids = set(qs.values_list('id', flat=True))
        self.assertIn(n_normal.id, ids)
        self.assertNotIn(n_empty.id, ids)


# --- process_notice_images 집계 ---

class ProcessNoticeImagesAggregateTests(TestCase):

    def test_success_skipped_failed_정확히_집계(self):
        n_ok = make_notice(content='', image_urls=['u1'], title='ok')
        n_skip = make_notice(content='', image_urls=[], title='skip')
        n_fail = make_notice(content='', image_urls=['u2'], title='fail')

        call_count = {'n': 0}

        def routed(image_urls):
            call_count['n'] += 1
            if call_count['n'] == 1:
                return '추출됨'
            raise AIClientError('boom')

        with patch.object(vlm, 'extract_text_from_images', side_effect=routed):
            result = vlm.process_notice_images([n_ok, n_skip, n_fail])

        self.assertEqual(result.success, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.failed, 1)
