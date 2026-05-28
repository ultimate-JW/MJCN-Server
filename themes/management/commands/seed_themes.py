"""themes 앱 초기 데이터 시드 명령 (spec §4.7).

사용 예:
    python manage.py seed_themes

5개 카테고리 각각 1개 Theme + 3-5개 ThemeItem을 멱등 시딩 (update_or_create).
운영 콘텐츠는 admin에서 별도 보강한다.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from themes.models import Theme, ThemeItem


# (theme_defaults, [item_defaults, ...]) 튜플 리스트.
# Theme key: (category, title) — 두 필드 unique 가정으로 update_or_create.
# ThemeItem key: (theme, title) — 같은 테마 안에서 title unique 가정.
SEED_DATA = [
    (
        {
            'category': Theme.CATEGORY_COURSE_REGISTRATION,
            'title': '2026-1 수강신청 가이드',
            'description': '수강신청 일정·전략·실수 회피 팁을 한곳에 모은 안내.',
            'order': 10,
        },
        [
            {
                'item_type': ThemeItem.ITEM_TYPE_GUIDE,
                'title': '수강신청 일정',
                'content': '본수강 / 정정기간 / 폐강 공지 일정과 시간대 정리.',
                'order': 10,
            },
            {
                'item_type': ThemeItem.ITEM_TYPE_LINK,
                'title': '수강편람 다운로드',
                'external_url': 'https://www.mju.ac.kr/',
                'order': 20,
            },
            {
                'item_type': ThemeItem.ITEM_TYPE_CHECKLIST,
                'title': '장바구니 활용 체크리스트',
                'content': '필수 전공·핵심교양·시간 충돌 확인.',
                'order': 30,
            },
        ],
    ),
    (
        {
            'category': Theme.CATEGORY_CAREER,
            'title': '졸업 후 진로 로드맵',
            'description': '학년별 취업 준비 단계와 학교 지원 제도.',
            'order': 20,
        },
        [
            {
                'item_type': ThemeItem.ITEM_TYPE_LINK,
                'title': '취업지원처 상담 예약',
                'external_url': 'https://www.mju.ac.kr/',
                'order': 10,
            },
            {
                'item_type': ThemeItem.ITEM_TYPE_GUIDE,
                'title': '이력서·자소서 작성 가이드',
                'content': '학과별 추천 양식과 작성 팁.',
                'order': 20,
            },
        ],
    ),
    (
        {
            'category': Theme.CATEGORY_EXCHANGE,
            'title': '교환학생 신청 안내',
            'description': '국가별 파견 프로그램 개요와 신청 절차.',
            'order': 30,
        },
        [
            {
                'item_type': ThemeItem.ITEM_TYPE_GUIDE,
                'title': '지원 자격',
                'content': '학점·어학 성적 요건과 추천서 안내.',
                'order': 10,
            },
            {
                'item_type': ThemeItem.ITEM_TYPE_CHECKLIST,
                'title': '지원 절차 체크리스트',
                'content': '서류 준비 → 면접 → 비자 → 출국 절차.',
                'order': 20,
            },
            {
                'item_type': ThemeItem.ITEM_TYPE_LINK,
                'title': '국제처 안내',
                'external_url': 'https://www.mju.ac.kr/',
                'order': 30,
            },
        ],
    ),
    (
        {
            'category': Theme.CATEGORY_GRANT,
            'title': '학생 지원사업 둘러보기',
            'description': '교내·교외 장학금 / 학생활동 지원금 안내.',
            'order': 40,
        },
        [
            {
                'item_type': ThemeItem.ITEM_TYPE_LINK,
                'title': '장학금 신청',
                'external_url': 'https://www.mju.ac.kr/',
                'order': 10,
            },
            {
                'item_type': ThemeItem.ITEM_TYPE_GUIDE,
                'title': '근로장학생 안내',
                'content': '신청 시기·근무 조건·시급 정보.',
                'order': 20,
            },
        ],
    ),
    (
        {
            'category': Theme.CATEGORY_ACADEMIC,
            'title': '학사 관리 팁',
            'description': '학기 중 챙겨야 할 학사 행정 절차 모음.',
            'order': 50,
        },
        [
            {
                'item_type': ThemeItem.ITEM_TYPE_GUIDE,
                'title': '전공·부전공·복수전공 신청',
                'content': '신청 시기·자격·절차.',
                'order': 10,
            },
            {
                'item_type': ThemeItem.ITEM_TYPE_CHECKLIST,
                'title': '성적 정정 체크리스트',
                'content': '정정 기간·문의 채널·증빙 자료.',
                'order': 20,
            },
            {
                'item_type': ThemeItem.ITEM_TYPE_LINK,
                'title': '졸업요건 확인',
                'external_url': 'https://www.mju.ac.kr/',
                'order': 30,
            },
        ],
    ),
]


class Command(BaseCommand):
    help = '5개 카테고리 placeholder 테마와 항목을 멱등 시딩 (운영 콘텐츠는 admin에서 보강).'

    @transaction.atomic
    def handle(self, *args, **options):
        theme_count = 0
        item_count = 0

        for theme_defaults, item_specs in SEED_DATA:
            theme, _ = Theme.objects.update_or_create(
                category=theme_defaults['category'],
                title=theme_defaults['title'],
                defaults={
                    'description': theme_defaults.get('description', ''),
                    'order': theme_defaults.get('order', 0),
                    'is_active': True,
                },
            )
            theme_count += 1

            for item_defaults in item_specs:
                ThemeItem.objects.update_or_create(
                    theme=theme,
                    title=item_defaults['title'],
                    defaults={
                        'item_type': item_defaults['item_type'],
                        'content': item_defaults.get('content', ''),
                        'external_url': item_defaults.get('external_url', ''),
                        'order': item_defaults.get('order', 0),
                    },
                )
                item_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'시드 완료 — Theme {theme_count}개 / ThemeItem {item_count}개 (멱등).'
            )
        )
