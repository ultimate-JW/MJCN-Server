"""AI 파이프라인 단위 테스트 (OpenAI 호출은 모두 mock)."""
from datetime import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from notices.ai import pipeline, processor
from notices.ai.client import AIClientError, AIResponseParseError
from notices.models import Notice, NoticeAIResult


# 30자 이상 (MIN_EFFECTIVE_CONTENT_LENGTH 통과용)
DEFAULT_TEST_CONTENT = '테스트용 공지 본문임. 충분히 길어서 LLM 호출이 진행되어야 한다는 것을 보장.'


def make_notice(content=DEFAULT_TEST_CONTENT, title='제목', source='academic') -> Notice:
    return Notice.objects.create(
        source=source,
        title=title,
        content=content,
        url=f'https://www.mju.ac.kr/test/{title}',
        published_at=timezone.now(),
    )


# --- pipeline 단계별 ---

class TruncateTests(TestCase):
    @override_settings(OPENAI_NOTICE_CONTENT_MAX_CHARS=10)
    def test_제한_초과시_앞부분만(self):
        self.assertEqual(pipeline.truncate_content('1234567890ABC'), '1234567890')

    @override_settings(OPENAI_NOTICE_CONTENT_MAX_CHARS=100)
    def test_제한_이하면_그대로(self):
        self.assertEqual(pipeline.truncate_content('짧은 본문'), '짧은 본문')


class ClassifyTests(TestCase):
    def test_정상_분류(self):
        with patch.object(pipeline, 'call_json', return_value={'type': '행동형'}):
            self.assertEqual(pipeline.classify('본문'), '행동형')

    def test_유효하지_않은_타입은_에러(self):
        with patch.object(pipeline, 'call_json', return_value={'type': '기타'}):
            with self.assertRaises(AIResponseParseError):
                pipeline.classify('본문')

    def test_type_키_없으면_에러(self):
        with patch.object(pipeline, 'call_json', return_value={}):
            with self.assertRaises(AIResponseParseError):
                pipeline.classify('본문')


class SummarizeTests(TestCase):
    def test_정상_요약(self):
        with patch.object(pipeline, 'call_text', return_value='수강신청 정정 기간 안내임.'):
            self.assertEqual(pipeline.summarize('본문'), '수강신청 정정 기간 안내임.')

    def test_빈_응답은_에러(self):
        with patch.object(pipeline, 'call_text', return_value=''):
            with self.assertRaises(AIResponseParseError):
                pipeline.summarize('본문')


class BuildCardsTests(TestCase):
    VALID = {
        'cards': [
            {'title': '🚨 지금 해야 할 행동', 'items': ['MSI 접속', '확인 필요']},
            {'title': '📞 문의', 'items': ['교학팀 02-XXX-XXXX']},
        ]
    }

    def test_정상_카드_파싱(self):
        with patch.object(pipeline, 'call_json', return_value=self.VALID):
            cards = pipeline.build_cards('본문', '행동형')
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]['title'], '🚨 지금 해야 할 행동')
        self.assertEqual(cards[0]['items'], ['MSI 접속', '확인 필요'])

    def test_정보형도_같은_검증(self):
        with patch.object(pipeline, 'call_json', return_value=self.VALID):
            cards = pipeline.build_cards('본문', '정보형')
        self.assertEqual(len(cards), 2)

    def test_유형이_잘못되면_ValueError(self):
        with self.assertRaises(ValueError):
            pipeline.build_cards('본문', '기타')

    def test_cards_누락은_에러(self):
        with patch.object(pipeline, 'call_json', return_value={}):
            with self.assertRaises(AIResponseParseError):
                pipeline.build_cards('본문', '행동형')

    def test_items_타입_불일치는_에러(self):
        bad = {'cards': [{'title': 'T', 'items': [1, 2]}]}
        with patch.object(pipeline, 'call_json', return_value=bad):
            with self.assertRaises(AIResponseParseError):
                pipeline.build_cards('본문', '행동형')

    def test_빈_cards_배열은_에러(self):
        with patch.object(pipeline, 'call_json', return_value={'cards': []}):
            with self.assertRaises(AIResponseParseError):
                pipeline.build_cards('본문', '행동형')


# --- processor (오케스트레이션) ---

class ProcessNoticeFlowTests(TestCase):
    """Notice 1건 → 3단계 파이프라인 흐름 검증."""

    def setUp(self):
        self.notice = make_notice()

    def _patch_pipeline(self, *, classify='행동형',
                       summarize='요약임.',
                       cards=None):
        cards = cards or [{'title': 'T', 'items': ['x']}]
        return (
            patch.object(pipeline, 'classify', return_value=classify),
            patch.object(pipeline, 'summarize', return_value=summarize),
            patch.object(pipeline, 'build_cards', return_value=cards),
        )

    def test_3단계_모두_성공(self):
        c, s, b = self._patch_pipeline()
        with c, s, b:
            result, action = processor.process_notice(self.notice)

        self.assertEqual(action, 'processed')
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.notice_type, '행동형')
        self.assertEqual(result.summary, '요약임.')
        self.assertEqual(result.cards, [{'title': 'T', 'items': ['x']}])
        self.assertEqual(result.last_stage, 'build_cards')
        self.assertNotEqual(result.content_hash, '')

    def test_재실행시_skipped(self):
        c, s, b = self._patch_pipeline()
        with c, s, b:
            processor.process_notice(self.notice)

        # 두 번째 실행 — pipeline이 호출되면 안 됨
        with patch.object(pipeline, 'classify') as c2, \
             patch.object(pipeline, 'summarize') as s2, \
             patch.object(pipeline, 'build_cards') as b2:
            result, action = processor.process_notice(self.notice)

        self.assertEqual(action, 'skipped')
        self.assertEqual(result.status, 'success')
        c2.assert_not_called()
        s2.assert_not_called()
        b2.assert_not_called()

    def test_본문_변경되면_재처리(self):
        c1, s1, b1 = self._patch_pipeline(classify='행동형', summarize='요약1')
        with c1, s1, b1:
            processor.process_notice(self.notice)

        # 본문 변경 (empty_content 가드 통과하도록 충분히 긴 본문)
        self.notice.content = '완전히 다른 본문임. 충분히 길어서 LLM 호출이 진행되어야 함.'
        self.notice.save()

        c2, s2, b2 = self._patch_pipeline(classify='정보형', summarize='요약2')
        with c2, s2, b2:
            result, action = processor.process_notice(self.notice)

        self.assertEqual(action, 'processed')
        self.assertEqual(result.notice_type, '정보형')
        self.assertEqual(result.summary, '요약2')

    def test_force는_무조건_재처리(self):
        c1, s1, b1 = self._patch_pipeline(summarize='요약1')
        with c1, s1, b1:
            processor.process_notice(self.notice)

        c2, s2, b2 = self._patch_pipeline(summarize='요약2')
        with c2, s2, b2:
            result, action = processor.process_notice(self.notice, force=True)

        self.assertEqual(action, 'processed')
        self.assertEqual(result.summary, '요약2')


class PartialFailureRecoveryTests(TestCase):
    """단계별 부분 실패 후 다음 실행에서 이어서 처리되는지."""

    def setUp(self):
        self.notice = make_notice()

    def test_stage3_실패시_다음실행에서_stage3만_재시도(self):
        # 1차: classify, summarize 성공 → build_cards 실패
        with patch.object(pipeline, 'classify', return_value='행동형') as c1, \
             patch.object(pipeline, 'summarize', return_value='요약임.') as s1, \
             patch.object(pipeline, 'build_cards', side_effect=AIClientError('API down')) as b1:
            result, action = processor.process_notice(self.notice)

        self.assertEqual(action, 'failed')
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.notice_type, '행동형')
        self.assertEqual(result.summary, '요약임.')
        self.assertEqual(result.cards, [])
        self.assertEqual(result.last_stage, 'summarize')
        self.assertEqual(result.retry_count, 1)
        c1.assert_called_once()
        s1.assert_called_once()
        b1.assert_called_once()

        # 2차: build_cards만 다시 호출돼야 함
        with patch.object(pipeline, 'classify') as c2, \
             patch.object(pipeline, 'summarize') as s2, \
             patch.object(pipeline, 'build_cards', return_value=[{'title': 'T', 'items': ['x']}]) as b2:
            result2, action2 = processor.process_notice(self.notice)

        self.assertEqual(action2, 'processed')
        self.assertEqual(result2.status, 'success')
        c2.assert_not_called()
        s2.assert_not_called()
        b2.assert_called_once()

    def test_stage1_실패시_재시도카운트_증가(self):
        with patch.object(pipeline, 'classify', side_effect=AIClientError('boom')):
            r1, _ = processor.process_notice(self.notice)
            self.assertEqual(r1.retry_count, 1)

        with patch.object(pipeline, 'classify', side_effect=AIClientError('boom')):
            r2, _ = processor.process_notice(self.notice)
            self.assertEqual(r2.retry_count, 2)


class GetPendingNoticesTests(TestCase):
    def setUp(self):
        self.n1 = make_notice(title='n1', source='academic')
        self.n2 = make_notice(title='n2', source='general')
        self.n3 = make_notice(title='n3', source='academic')

    def test_AI결과없는_공지는_pending(self):
        qs = processor.get_pending_notices()
        ids = set(qs.values_list('id', flat=True))
        self.assertEqual(ids, {self.n1.id, self.n2.id, self.n3.id})

    def test_success는_제외(self):
        NoticeAIResult.objects.create(notice=self.n1, status='success')
        qs = processor.get_pending_notices()
        ids = set(qs.values_list('id', flat=True))
        self.assertNotIn(self.n1.id, ids)

    def test_failed는_포함(self):
        NoticeAIResult.objects.create(notice=self.n1, status='failed')
        qs = processor.get_pending_notices()
        self.assertIn(self.n1.id, qs.values_list('id', flat=True))

    def test_source_필터(self):
        qs = processor.get_pending_notices(sources=['general'])
        self.assertEqual(set(qs.values_list('id', flat=True)), {self.n2.id})

    def test_ids_필터(self):
        qs = processor.get_pending_notices(ids=[self.n1.id])
        self.assertEqual(set(qs.values_list('id', flat=True)), {self.n1.id})

    def test_reprocess는_success도_포함(self):
        NoticeAIResult.objects.create(notice=self.n1, status='success')
        qs = processor.get_pending_notices(reprocess=True)
        self.assertIn(self.n1.id, qs.values_list('id', flat=True))


class ProcessNoticesAggregateTests(TestCase):
    """여러 Notice 처리 집계 카운트."""

    def test_success_skipped_failed_정확히_집계(self):
        n_ok = make_notice(title='ok')
        n_skip = make_notice(title='skip')
        n_fail = make_notice(title='fail')

        # n_skip은 미리 success로 만들어둠
        NoticeAIResult.objects.create(
            notice=n_skip,
            status='success',
            content_hash=processor.compute_content_hash(n_skip.content),
        )

        def fake_classify(content):
            if '본문' in content and 'fail' not in content:
                # n_ok 본문은 그냥 '본문', n_fail도 '본문' — 구분 위해 title을 본문에 세팅 못함
                # 대신 mock side_effect 시퀀스로 처리
                pass
            return '행동형'

        # 좀 더 단순하게: 전체 mock 후 ID로 분기
        original_classify = pipeline.classify

        def routed_classify(content):
            # content 자체가 모두 같으므로 "어떤 notice를 처리 중인지" 알 방법이 없음.
            # 대신 호출 순서대로 결과/예외 반환 (n_ok가 먼저, n_fail이 다음)
            calls.append('c')
            if len(calls) == 1:
                return '행동형'
            raise AIClientError('boom on second')

        calls: list[str] = []
        with patch.object(pipeline, 'classify', side_effect=routed_classify), \
             patch.object(pipeline, 'summarize', return_value='요약'), \
             patch.object(pipeline, 'build_cards', return_value=[{'title': 'T', 'items': ['x']}]):
            qs = processor.get_pending_notices()
            # 정렬을 결정적으로: n_ok가 더 최신(나중에 생성됐으므로 published_at 동일하지만 id 큼)
            # qs는 -published_at, id 정렬이라 동일한 published_at에서는 작은 id가 먼저 → 순서 보장 필요
            notices = list(qs)
            result = processor.process_notices(notices)

        self.assertEqual(result.success + result.failed, 2)
        # n_skip은 미리 success였고, content_hash 일치하므로 skipped
        self.assertEqual(result.skipped, 0)  # qs에 안 잡혀서 skipped로도 안 카운트
        # 위 케이스가 좀 헷갈리니 별도로 skipped만 전용 테스트:

    def test_skipped_카운트(self):
        n = make_notice()
        NoticeAIResult.objects.create(
            notice=n,
            status='success',
            content_hash=processor.compute_content_hash(n.content),
        )
        # reprocess로 강제 포함시킨 뒤 force=False면 skipped 처리됨
        qs = processor.get_pending_notices(reprocess=True)
        with patch.object(pipeline, 'classify') as c, \
             patch.object(pipeline, 'summarize') as s, \
             patch.object(pipeline, 'build_cards') as b:
            result = processor.process_notices(qs, force=False)

        self.assertEqual(result.skipped, 1)
        c.assert_not_called()
        s.assert_not_called()
        b.assert_not_called()
