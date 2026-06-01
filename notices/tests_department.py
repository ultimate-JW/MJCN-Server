"""Notice.department 추출 + 응답 fallback 테스트 (spec 4.4 / 6.7)."""
from datetime import datetime, timezone as dt_tz

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from notices.crawlers.base import CrawledNotice, extract_department
from notices.models import Notice

User = get_user_model()


def make_notice(**overrides):
    defaults = {
        'source': 'general',
        'title': '기본 제목',
        'content': '본문',
        'url': 'https://x/1',
        'published_at': timezone.now(),
    }
    defaults.update(overrides)
    return Notice.objects.create(**defaults)


# ─────────────────────────────────────────────────────────────────────
# 추출 정규식
# ─────────────────────────────────────────────────────────────────────

class ExtractDepartmentTests(TestCase):
    """spec 4.4 — 첫 [...] 추출, 휴리스틱 화이트리스트 없음."""

    def test_정상_부서명_추출(self):
        self.assertEqual(
            extract_department('[원격교육센터] 카피킬러 도입 안내'),
            '원격교육센터',
        )

    def test_공백_포함_부서명_trim(self):
        self.assertEqual(
            extract_department('[  학생지원팀  ]  공지'),
            '학생지원팀',
        )

    def test_접두사_없으면_빈_문자열(self):
        self.assertEqual(extract_department('수강신청 안내'), '')

    def test_빈_제목은_빈_문자열(self):
        self.assertEqual(extract_department(''), '')
        self.assertEqual(extract_department(None), '')

    def test_첫_대괄호만_추출(self):
        # 두 번째 [xxx]는 무시
        self.assertEqual(
            extract_department('[학사지원팀] [긴급] 등록금 안내'),
            '학사지원팀',
        )

    def test_숫자_라벨도_추출_되는_단순_정책(self):
        # spec 정책: 휴리스틱 없음 → [2026-1] 도 부서명으로 추출됨
        self.assertEqual(
            extract_department('[2026-1] 학부 안내'),
            '2026-1',
        )

    def test_중첩_대괄호_없는_케이스(self):
        # [...] 내부에 [, ]가 또 있으면 매칭 안 됨 (단순화)
        self.assertEqual(
            extract_department('[학과[보건]] 안내'),
            '',
        )


# ─────────────────────────────────────────────────────────────────────
# CrawledNotice 자동 추출
# ─────────────────────────────────────────────────────────────────────

class CrawledNoticeAutoExtractTests(TestCase):
    """CrawledNotice 생성 시 department 미지정이면 title에서 자동 추출."""

    def _make(self, **overrides):
        defaults = {
            'source': 'general',
            'title': '[학생지원팀] 안내',
            'url': 'https://x/1',
            'published_at': datetime(2026, 5, 1, tzinfo=dt_tz.utc),
        }
        defaults.update(overrides)
        return CrawledNotice(**defaults)

    def test_department_미지정시_자동_추출(self):
        n = self._make()
        self.assertEqual(n.department, '학생지원팀')

    def test_department_명시시_그대로_사용(self):
        n = self._make(department='수동지정')
        self.assertEqual(n.department, '수동지정')

    def test_접두사_없으면_빈_문자열(self):
        n = self._make(title='수강신청 안내')
        self.assertEqual(n.department, '')

    def test_to_dict에_department_포함(self):
        n = self._make()
        self.assertEqual(n.to_dict()['department'], '학생지원팀')


# ─────────────────────────────────────────────────────────────────────
# Serializer (department + department_display)
# ─────────────────────────────────────────────────────────────────────

class NoticeAPIResponseTests(TestCase):
    """GET /api/v1/notices/ — department / department_display fallback."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='u@mju.ac.kr', password='pw1234abc',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_department_있는_경우_그대로_노출(self):
        n = make_notice(
            source='general',
            title='[원격교육센터] 카피킬러 도입',
            department='원격교육센터',
            url='https://x/dept-1',
        )
        res = self.client.get(f'/api/v1/notices/{n.id}/')
        self.assertEqual(res.data['department'], '원격교육센터')
        self.assertEqual(res.data['department_display'], '원격교육센터')

    def test_department_없으면_source_label로_fallback(self):
        n = make_notice(
            source='academic',
            title='수강신청 안내',
            department='',
            url='https://x/no-dept',
        )
        res = self.client.get(f'/api/v1/notices/{n.id}/')
        self.assertEqual(res.data['department'], '')
        # source='academic' → source_label='학사공지' 로 fallback
        self.assertEqual(res.data['department_display'], '학사공지')

    def test_목록_응답에도_department_필드_포함(self):
        make_notice(
            source='general',
            title='[학생지원팀] 안내',
            department='학생지원팀',
            url='https://x/list-1',
        )
        res = self.client.get('/api/v1/notices/', {'view': 'all'})
        self.assertEqual(len(res.data['results']), 1)
        item = res.data['results'][0]
        self.assertEqual(item['department'], '학생지원팀')
        self.assertEqual(item['department_display'], '학생지원팀')

    def test_원본_title은_접두사_포함_그대로(self):
        n = make_notice(
            source='general',
            title='[원격교육센터] 카피킬러 도입',
            department='원격교육센터',
            url='https://x/title-original',
        )
        res = self.client.get(f'/api/v1/notices/{n.id}/')
        # 원본 title 보존 (검색 호환성)
        self.assertEqual(res.data['title'], '[원격교육센터] 카피킬러 도입')

    def test_title_without_dept_접두사_제거(self):
        n = make_notice(
            source='general',
            title='[원격교육센터] 명지대학교 카피킬러 도입 안내',
            department='원격교육센터',
            url='https://x/title-clean',
        )
        res = self.client.get(f'/api/v1/notices/{n.id}/')
        self.assertEqual(
            res.data['title_without_dept'],
            '명지대학교 카피킬러 도입 안내',
        )

    def test_title_without_dept_부서명_없으면_원본_그대로(self):
        n = make_notice(
            source='general',
            title='수강신청 안내',
            department='',
            url='https://x/title-no-dept',
        )
        res = self.client.get(f'/api/v1/notices/{n.id}/')
        self.assertEqual(res.data['title_without_dept'], '수강신청 안내')

    def test_title_without_dept_첫_대괄호만_제거(self):
        # [A] [B] xxx → [B] xxx (첫 [A]만 제거)
        n = make_notice(
            source='general',
            title='[학사지원팀] [긴급] 등록금 안내',
            department='학사지원팀',
            url='https://x/title-multi-bracket',
        )
        res = self.client.get(f'/api/v1/notices/{n.id}/')
        self.assertEqual(
            res.data['title_without_dept'],
            '[긴급] 등록금 안내',
        )

    def test_title_without_dept_목록에도_포함(self):
        make_notice(
            source='general',
            title='[학생지원팀] 안내',
            department='학생지원팀',
            url='https://x/list-clean',
        )
        res = self.client.get('/api/v1/notices/', {'view': 'all'})
        self.assertEqual(
            res.data['results'][0]['title_without_dept'],
            '안내',
        )


# ─────────────────────────────────────────────────────────────────────
# Crawler 통합 — 저장 시 department 자동 채움
# ─────────────────────────────────────────────────────────────────────

class CrawlerSaveDepartmentTests(TestCase):
    """BaseNoticeCrawler.save가 department를 DB에 저장하는지 검증."""

    def setUp(self):
        from notices.crawlers.base import BaseNoticeCrawler

        class _Dummy(BaseNoticeCrawler):
            SOURCE = 'general'
            LIST_URL = 'https://example.test/list'
            def parse_list(self, html):
                return []

        self.crawler = _Dummy()

    def _make(self, **overrides):
        defaults = {
            'source': 'general',
            'title': '[학생지원팀] 안내',
            'url': 'https://x/save-1',
            'published_at': timezone.now(),
        }
        defaults.update(overrides)
        return CrawledNotice(**defaults)

    def test_저장_시_department_자동_채움(self):
        self.crawler.save([self._make()])
        notice = Notice.objects.get(url='https://x/save-1')
        self.assertEqual(notice.department, '학생지원팀')

    def test_업데이트시_department_갱신(self):
        # 1차: 부서명 A
        self.crawler.save([self._make(title='[A팀] 안내')])
        # 2차: 같은 url, 부서명 B로 변경
        self.crawler.save([self._make(title='[B팀] 안내')])
        notice = Notice.objects.get(url='https://x/save-1')
        self.assertEqual(notice.department, 'B팀')
        self.assertEqual(notice.title, '[B팀] 안내')

    def test_접두사_없는_제목은_빈_department(self):
        self.crawler.save([self._make(title='접두사 없음')])
        notice = Notice.objects.get(url='https://x/save-1')
        self.assertEqual(notice.department, '')
