"""정보(Information) 키워드 태깅 (spec 9.1.7).

공지 Stage 4(`notices.ai.pipeline.extract_tags`)와 동일한 프롬프트·함수를 재사용해
`Information.tags`를 채운다. 맞춤형 정보 매칭(spec 5.5.2)에 사용.

**위비티 제약**: 상세 페이지 본문은 크롤링하지 않는다. 태깅 입력은 이미 저장된
메타데이터(title + categories + organizer)만 — 추가 네트워크 요청 없음.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from notices.ai.client import AIClientError
from notices.ai.pipeline import extract_tags

from .models import Information

logger = logging.getLogger(__name__)


@dataclass
class TagResult:
    """태깅 집계."""
    success: int = 0
    failed: int = 0
    skipped: int = 0  # 이미 tags 있음 (force=False)

    @property
    def total(self) -> int:
        return self.success + self.failed + self.skipped


def build_tagging_text(info: Information) -> str:
    """태깅 입력 텍스트 — 저장된 메타데이터만 사용 (상세 본문 크롤링 안 함)."""
    parts = [info.title]
    if info.organizer:
        parts.append(f'주최: {info.organizer}')
    if info.categories:
        parts.append('분류: ' + ', '.join(info.categories))
    return '\n'.join(p for p in parts if p)


def tag_one(info: Information, *, force: bool = False) -> str:
    """단일 Information 태깅. 반환: 'success' / 'skipped' / 'failed'."""
    if info.tags and not force:
        return 'skipped'

    text = build_tagging_text(info)
    if not text.strip():
        return 'skipped'

    try:
        tags = extract_tags(text)
    except (AIClientError, ValueError) as e:
        logger.warning('[InfoTag:%s] 태깅 실패: %s', info.id, e)
        return 'failed'

    info.tags = tags
    info.save(update_fields=['tags'])
    logger.info('[InfoTag:%s] 태깅 성공 (%d개)', info.id, len(tags))
    return 'success'


def tag_information(items: Iterable[Information], *, force: bool = False) -> TagResult:
    """여러 Information을 순차 태깅하며 집계."""
    summary = TagResult()
    for info in items:
        try:
            action = tag_one(info, force=force)
        except Exception:
            logger.exception('[InfoTag:%s] 처리 중 예외', info.id)
            summary.failed += 1
            continue
        if action == 'success':
            summary.success += 1
        elif action == 'skipped':
            summary.skipped += 1
        else:
            summary.failed += 1
    return summary


def get_tagging_targets(
    *, ids: list[int] | None = None,
    limit: int | None = None,
    reprocess: bool = False,
):
    """태깅 대상 Information 쿼리셋. 기본: tags가 비어 있는 행."""
    qs = Information.objects.all()
    if ids:
        qs = qs.filter(id__in=ids)
    if not reprocess:
        qs = qs.filter(tags=[])
    qs = qs.order_by('-created_at')
    if limit:
        qs = qs[:limit]
    return qs
