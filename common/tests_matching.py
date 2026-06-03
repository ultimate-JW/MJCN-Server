"""매칭 로직 단위 테스트 (spec 5.10)."""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from common.matching import (
    extract_user_keywords,
    matched_keywords,
    score_combined,
    score_match,
    sort_by_match,
)

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

    def test_복합라벨_토큰분해_매칭(self):
        # 관심사 복합 라벨('공기업/공공기관')이 LLM 단일어 태그('공기업')와 매칭
        self.assertEqual(score_match({'공기업/공공기관'}, ['공기업', '채용']), 1)

    def test_전공_통짜문자열_분해_매칭(self):
        # major 통짜 문자열도 '·'·공백으로 쪼개져 태그와 토큰 매칭
        kw = {'반도체·ICT대학 · 컴퓨터정보통신공학부 · 컴퓨터공학전공'}
        self.assertEqual(score_match(kw, ['컴퓨터공학전공', '세미나']), 1)

    def test_한_관심사는_최대_1점(self):
        # 복합 라벨이 토큰 2개로 쪼개지고 둘 다 태그에 있어도 점수는 1 (인플레 방지)
        self.assertEqual(score_match({'스포츠/예술'}, ['스포츠', '예술']), 1)

    def test_토큰_부분문자열은_매칭안됨(self):
        # 토큰 단위 완전 일치만 — '공기업'은 '공기업체관리'에 매칭 안 됨 (오탐 방지)
        self.assertEqual(score_match({'공기업'}, ['공기업체관리']), 0)

    def test_IT개발_관심사가_AI태그_매칭_207(self):
        # #207: 카테고리 동의어 확장 — 'it/개발' → AI 도메인으로 확장
        self.assertEqual(score_match({'it/개발'}, ['AI']), 1)
        self.assertEqual(score_match({'it/개발'}, ['인공지능', '공모전']), 1)

    def test_카테고리_동의어_확장_전반_207(self):
        # 12개 카테고리 전반 — IT만이 아님
        self.assertEqual(score_match({'디자인'}, ['UX', '포스터']), 1)
        self.assertEqual(score_match({'금융/회계'}, ['핀테크']), 1)
        self.assertEqual(score_match({'미디어/콘텐츠'}, ['유튜브', '크리에이터']), 1)

    def test_비카테고리_키워드는_확장안됨_207(self):
        # 전공명 등 자유 텍스트는 카테고리가 아니라 확장 X (정확 토큰만)
        self.assertEqual(score_match({'컴퓨터공학전공'}, ['ai']), 0)

    def test_카테고리_동의어도_관심사당_최대_1점_207(self):
        # 'it/개발' 하나가 여러 동의어 태그에 걸려도 1점 (인플레 방지)
        self.assertEqual(score_match({'it/개발'}, ['ai', 'sw', '개발']), 1)


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


class MatchedKeywordsTests(TestCase):
    """spec 5.10.4 — 제목 동의어 부분일치 + categories 결합 (공모전용, #184)."""

    def test_동의어_확장_개발(self):
        # '개발' 관심사 → 제목엔 '코딩'으로 노출돼도 매칭
        m = matched_keywords({'개발'}, ['공모전'], '2026 전국 코딩 경진대회')
        self.assertEqual(m, ['개발'])

    def test_동의어_확장_AI(self):
        # 'ai' 관심사 → 제목 '인공지능'과 매칭
        self.assertEqual(matched_keywords({'ai'}, [], '인공지능 챌린지'), ['ai'])

    def test_영문_한글_인접_통과(self):
        # 'ai'가 한글에 붙어도 매칭 ('제조ai' — 앞 글자가 라틴이 아니므로)
        self.assertEqual(matched_keywords({'ai'}, [], '2026 제조AI 경진대회'), ['ai'])

    def test_짧은_영문토큰_오매칭_가드(self):
        # 'it'이 'digital' 안에 박혀도 매칭 안 됨 (앞뒤가 영문 알파벳)
        self.assertEqual(matched_keywords({'it'}, [], 'digital design contest'), [])

    def test_한글_부분일치_유지(self):
        # '게임' → '인디게임' 부분일치 (한글 substring 허용)
        self.assertEqual(matched_keywords({'게임'}, [], '대한민국 인디게임 축제'), ['게임'])

    def test_categories_경로_매칭(self):
        # 제목 매칭이 없어도 categories 토큰 완전일치로 매칭
        self.assertEqual(matched_keywords({'대외활동'}, ['공모전', '대외활동'], '요리대회'), ['대외활동'])

    def test_관심사당_1점_캡_categories_title_중복(self):
        # 'ai'가 categories('AI')에도, 제목('AI 해커톤')에도 걸려도 한 번만
        m = matched_keywords({'ai'}, ['AI'], 'AI 해커톤')
        self.assertEqual(m, ['ai'])
        self.assertEqual(score_combined({'ai'}, ['AI'], 'AI 해커톤'), 1)

    def test_여러_관심사_합산(self):
        self.assertEqual(score_combined({'게임', '개발'}, [], '게임 개발 공모전'), 2)

    def test_매칭_없음(self):
        self.assertEqual(matched_keywords({'금융'}, ['공모전'], 'AI 게임 해커톤'), [])
        self.assertEqual(score_combined({'금융'}, ['공모전'], 'AI 게임 해커톤'), 0)

    def test_빈_입력_안전(self):
        self.assertEqual(matched_keywords(set(), ['공모전'], '코딩 대회'), [])
        self.assertEqual(matched_keywords({'개발'}, [], ''), [])
        self.assertEqual(matched_keywords({'개발'}, None, None), [])


class SortByMatchTextAttrTests(TestCase):
    """sort_by_match text_attr — 태그 + 제목 동의어 결합 점수 (#184)."""

    def test_title_점수로_정렬(self):
        # categories는 '공모전'뿐(0점) → 제목 동의어 매칭으로 점수 생성
        items = [
            SimpleNamespace(id=1, categories=['공모전'], title='AI 해커톤'),
            SimpleNamespace(id=2, categories=['공모전'], title='요리 경연대회'),
        ]
        sorted_items = sort_by_match(
            items, {'ai'}, tags_attr='categories', text_attr='title',
        )
        self.assertEqual(sorted_items[0].id, 1)
        self.assertEqual(sorted_items[0].match_score, 1)
        self.assertEqual(sorted_items[1].match_score, 0)

    def test_text_attr_미지정시_제목_무시(self):
        # text_attr 없으면 기존대로 태그만 — 공지 경로 무영향 확인
        items = [SimpleNamespace(id=1, categories=['공모전'], title='AI 해커톤')]
        sorted_items = sort_by_match(items, {'ai'}, tags_attr='categories')
        self.assertEqual(sorted_items[0].match_score, 0)
