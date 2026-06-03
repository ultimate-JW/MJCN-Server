"""공모전(Information) 샘플 시드 — 개발/데모용 (#222).

dev DB에 공모전 데이터가 없어 챗 공모전 검색(#6 동의어 매칭)·공모전 테마를 실스모크할 수
없는 문제 해소. 라이브 크롤러(crawl_information) 대신 미래 마감일·다양한 분야의 대표 공모전을
넣어 오프라인·결정적으로 검증 가능하게 한다.

idempotent — (source, source_id) 기준 update_or_create. source='sample'로 묶어 실크롤
데이터(source='wevity' 등)와 구분.
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from information.models import Information

SOURCE = 'sample'

# (source_id, 제목, 주최, [categories], 마감 D+N) — 동의어 매칭(#6) 다양성 위해 분야 분산.
# title에 분야 키워드를 직접 넣어 SYNONYM_MAP(ai→인공지능, 보안→정보보호 등) 매칭 확인 가능.
SAMPLE_CONTESTS = [
    ('sample-ai-1', '전국 대학생 인공지능 아이디어 경진대회', '과학기술정보통신부', ['공모전'], 30),
    ('sample-sec-1', '정보보호 해커톤 챌린지', '한국인터넷진흥원', ['공모전'], 20),
    ('sample-game-1', '인디 게임 개발 공모전', '한국콘텐츠진흥원', ['공모전'], 25),
    ('sample-data-1', '빅데이터 분석 경진대회', '통계청', ['공모전'], 15),
    ('sample-cloud-1', '클라우드 아키텍처 설계 공모전', '네이버클라우드', ['공모전'], 40),
    ('sample-design-1', 'UX 디자인 공모전', '디자인진흥원', ['공모전', '디자인'], 10),
    ('sample-dev-1', '오픈소스 SW 개발 공모전', '정보통신산업진흥원', ['공모전'], 35),
]


class Command(BaseCommand):
    help = '공모전(Information) 샘플 데이터 시드 (개발/데모용, source=sample)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear', action='store_true',
            help='기존 source=sample 데이터 삭제 후 재시드',
        )

    def handle(self, *args, **opts):
        if opts['clear']:
            n, _ = Information.objects.filter(source=SOURCE).delete()
            self.stdout.write(f'기존 sample {n}건 삭제')

        today = date.today()
        created = updated = 0
        for sid, title, organizer, cats, dday in SAMPLE_CONTESTS:
            _, was_created = Information.objects.update_or_create(
                source=SOURCE, source_id=sid,
                defaults={
                    'title': title,
                    'organizer': organizer,
                    'url': f'https://example.com/contest/{sid}',
                    'categories': cats,
                    'end_date': today + timedelta(days=dday),
                    'is_active': True,
                },
            )
            created += was_created
            updated += not was_created

        self.stdout.write(self.style.SUCCESS(
            f'공모전 샘플 시드 완료 — 신규 {created} / 갱신 {updated} (총 {len(SAMPLE_CONTESTS)})'
        ))
