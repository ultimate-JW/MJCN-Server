"""매칭 로직 단위 테스트 (spec 5.10)."""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from common.matching import extract_user_keywords, score_match, sort_by_match

User = get_user_model()


class ExtractUserKeywordsTests(TestCase):
    """spec 5.10.1 — 사용자 키워드 집합 추출."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='u@mju.ac.kr',
            password='pw1234abc',
            major='컴퓨터공학전공',
        )

    def test_major_포함(self):
        kw = extract_user_keywords(self.user)
        self.assertIn('컴퓨터공학전공', kw)

    def test_major_빈값은_제외(self):
        self.user.major = ''
        self.user.save()
        kw = extract_user_keywords(self.user)
        self.assertEqual(kw, set())

    def test_interest_area_category_추가(self):
        from accounts.models import InterestArea
        InterestArea.objects.create(user=self.user, category='IT/개발')
        InterestArea.objects.create(user=self.user, category='AI')
        kw = extract_user_keywords(self.user)
        self.assertIn('it/개발', kw)  # lowercased
        self.assertIn('ai', kw)
        self.assertIn('컴퓨터공학전공', kw)

    def test_custom_text_콤마_분리(self):
        from accounts.models import InterestArea
        InterestArea.objects.create(
            user=self.user, category='IT/개발',
            custom_text='머신러닝, 백엔드, 데이터분석',
        )
        kw = extract_user_keywords(self.user)
        self.assertIn('머신러닝', kw)
        self.assertIn('백엔드', kw)
        self.assertIn('데이터분석', kw)

    def test_custom_text_공백_분리(self):
        from accounts.models import InterestArea
        InterestArea.objects.create(
            user=self.user, category='IT/개발',
            custom_text='Python Django Docker',
        )
        kw = extract_user_keywords(self.user)
        self.assertIn('python', kw)
        self.assertIn('django', kw)
        self.assertIn('docker', kw)

    def test_빈_custom_text는_안전(self):
        from accounts.models import InterestArea
        InterestArea.objects.create(user=self.user, category='IT', custom_text='')
        kw = extract_user_keywords(self.user)
        self.assertIn('it', kw)

    def test_중복_키워드_set으로_제거(self):
        from accounts.models import InterestArea
        InterestArea.objects.create(user=self.user, category='IT', custom_text='IT, AI')
        InterestArea.objects.create(user=self.user, category='AI')
        kw = extract_user_keywords(self.user)
        # 'it' / 'ai' 한 번씩만
        self.assertEqual(sum(1 for k in kw if k == 'it'), 1)
        self.assertEqual(sum(1 for k in kw if k == 'ai'), 1)


class ScoreMatchTests(TestCase):
    """spec 5.10.3 — 점수 산출."""

    def test_교집합_크기(self):
        user_kw = {'it', 'ai', '백엔드'}
        tags = ['IT', '데이터', 'AI']  # 정규화 후 it, 데이터, ai → 2개 매칭
        self.assertEqual(score_match(user_kw, tags), 2)

    def test_매칭_0(self):
        self.assertEqual(score_match({'it'}, ['음악', '미술']), 0)

    def test_빈_user_keywords(self):
        self.assertEqual(score_match(set(), ['it', 'ai']), 0)

    def test_빈_content_tags(self):
        self.assertEqual(score_match({'it'}, []), 0)

    def test_None_안전(self):
        # tags에 None 섞여있어도 안전
        self.assertEqual(score_match({'it'}, ['IT', None, '']), 1)

    def test_대소문자_무관(self):
        self.assertEqual(score_match({'it/개발'}, ['IT/개발']), 1)


class SortByMatchTests(TestCase):
    """sort_by_match — 점수 부여 + 정렬."""

    def _item(self, tid, tags, **kw):
        return SimpleNamespace(id=tid, tags=tags, **kw)

    def test_점수_부여(self):
        items = [
            self._item(1, ['IT', 'AI']),
            self._item(2, ['음악']),
            self._item(3, ['it']),
        ]
        sorted_items = sort_by_match(items, {'it', 'ai'}, tags_attr='tags')
        # ID 1 (점수 2) → ID 3 (점수 1) → ID 2 (점수 0)
        self.assertEqual([x.id for x in sorted_items], [1, 3, 2])
        self.assertEqual(sorted_items[0].match_score, 2)
        self.assertEqual(sorted_items[1].match_score, 1)
        self.assertEqual(sorted_items[2].match_score, 0)

    def test_secondary_key_동점_처리(self):
        items = [
            self._item(1, ['IT'], priority=2),
            self._item(2, ['IT'], priority=1),
            self._item(3, ['IT'], priority=3),
        ]
        # 점수 동일 (1점) → priority 오름차순으로 정렬
        sorted_items = sort_by_match(
            items, {'it'}, tags_attr='tags',
            secondary_key=lambda x: x.priority,
        )
        self.assertEqual([x.id for x in sorted_items], [2, 1, 3])

    def test_점수_0도_포함(self):
        # spec 5.10 — 점수 0 항목도 정렬 최하위로 포함
        items = [self._item(1, []), self._item(2, ['IT'])]
        sorted_items = sort_by_match(items, {'it'}, tags_attr='tags')
        self.assertEqual(len(sorted_items), 2)
        self.assertEqual(sorted_items[0].id, 2)
        self.assertEqual(sorted_items[1].id, 1)
        self.assertEqual(sorted_items[1].match_score, 0)

    def test_빈_user_keywords면_모두_0점(self):
        items = [self._item(1, ['IT']), self._item(2, ['AI'])]
        sorted_items = sort_by_match(items, set(), tags_attr='tags')
        for item in sorted_items:
            self.assertEqual(item.match_score, 0)

    def test_categories_필드도_매칭(self):
        # Information.categories처럼 다른 필드명 지정 가능
        items = [
            SimpleNamespace(id=1, categories=['공모전', 'IT']),
            SimpleNamespace(id=2, categories=['교육']),
        ]
        sorted_items = sort_by_match(items, {'it'}, tags_attr='categories')
        self.assertEqual(sorted_items[0].id, 1)
        self.assertEqual(sorted_items[0].match_score, 1)
