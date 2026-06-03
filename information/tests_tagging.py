"""정보 AI 태깅 단위 테스트 (spec 9.1.7). extract_tags는 mock."""
from unittest.mock import patch

from django.test import TestCase

from information import tagging
from information.models import Information

_seq = [0]


def make_info(**overrides):
    _seq[0] += 1
    defaults = dict(
        title='2026 AI 콘텐츠 공모전', organizer='OO재단', description='',
        url=f'https://wevity.com/t/{_seq[0]}', source='wevity',
        source_id=f'tag-{_seq[0]}', is_active=True,
        categories=['공모전'], tags=[],
    )
    defaults.update(overrides)
    return Information.objects.create(**defaults)


class BuildTaggingTextTests(TestCase):

    def test_메타데이터만_사용_상세본문_없음(self):
        info = make_info(title='해커톤', organizer='멋사', categories=['공모전', '대외활동'])
        text = tagging.build_tagging_text(info)
        self.assertIn('해커톤', text)
        self.assertIn('멋사', text)
        self.assertIn('공모전', text)
        self.assertIn('대외활동', text)

    def test_organizer_categories_없어도_안전(self):
        info = make_info(title='제목만', organizer='', categories=[])
        self.assertEqual(tagging.build_tagging_text(info), '제목만')


class TagOneTests(TestCase):

    def test_정상_태깅_저장(self):
        info = make_info(tags=[])
        with patch.object(tagging, 'extract_tags', return_value=['IT/개발', 'AI', '공모전']):
            action = tagging.tag_one(info)
        self.assertEqual(action, 'success')
        info.refresh_from_db()
        self.assertEqual(info.tags, ['IT/개발', 'AI', '공모전'])

    def test_이미_tags_있으면_skipped(self):
        info = make_info(tags=['기존'])
        with patch.object(tagging, 'extract_tags') as m:
            action = tagging.tag_one(info)
        self.assertEqual(action, 'skipped')
        m.assert_not_called()

    def test_force면_재태깅(self):
        info = make_info(tags=['기존'])
        with patch.object(tagging, 'extract_tags', return_value=['새것']):
            action = tagging.tag_one(info, force=True)
        self.assertEqual(action, 'success')
        info.refresh_from_db()
        self.assertEqual(info.tags, ['새것'])

    def test_실패시_failed_저장안함(self):
        from notices.ai.client import AIClientError
        info = make_info(tags=[])
        with patch.object(tagging, 'extract_tags', side_effect=AIClientError('boom')):
            action = tagging.tag_one(info)
        self.assertEqual(action, 'failed')
        info.refresh_from_db()
        self.assertEqual(info.tags, [])


class TagInformationAggregateTests(TestCase):

    def test_success_skipped_failed_집계(self):
        from notices.ai.client import AIClientError
        n_ok = make_info(tags=[])
        n_skip = make_info(tags=['이미'])
        n_fail = make_info(tags=[])
        calls = {'n': 0}

        def routed(text):
            calls['n'] += 1
            if calls['n'] == 1:
                return ['태그']
            raise AIClientError('boom')

        with patch.object(tagging, 'extract_tags', side_effect=routed):
            result = tagging.tag_information([n_ok, n_skip, n_fail])
        self.assertEqual(result.success, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.failed, 1)


class GetTaggingTargetsTests(TestCase):

    def test_기본은_tags_빈것만(self):
        a = make_info(tags=[])
        make_info(tags=['있음'])
        ids = list(tagging.get_tagging_targets().values_list('id', flat=True))
        self.assertIn(a.id, ids)
        self.assertEqual(len(ids), 1)

    def test_reprocess는_전부(self):
        make_info(tags=[])
        make_info(tags=['있음'])
        self.assertEqual(tagging.get_tagging_targets(reprocess=True).count(), 2)
