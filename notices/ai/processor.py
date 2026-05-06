"""공지 AI 처리 오케스트레이터 (spec 9.1.1).

Notice 1건을 받아 3단계 파이프라인을 순차 실행한다.
- 각 단계 성공 즉시 NoticeAIResult를 DB에 저장 → 다음 실행 시 이어서 처리
- 본문(content) 변경은 sha256 해시로 감지 → 처음부터 재처리
- 단계별 실패는 즉시 status='failed'로 기록 후 다음 Notice로 진행 (격리)
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Iterable

from django.conf import settings

from notices.models import Notice, NoticeAIResult

from . import pipeline
from .client import AIClientError

logger = logging.getLogger(__name__)


def compute_content_hash(content: str) -> str:
    """파이프라인 입력 본문의 sha256 — 재처리 트리거 비교 키.

    Notice.effective_content (extracted_content 우선, 없으면 content) 기준.
    """
    return hashlib.sha256((content or '').encode('utf-8')).hexdigest()


@dataclass
class ProcessResult:
    """파이프라인 실행 집계."""
    success: int = 0
    failed: int = 0
    skipped: int = 0  # 이미 success + content 변경 없음

    @property
    def total(self) -> int:
        return self.success + self.failed + self.skipped


def process_notice(
    notice: Notice, *, force: bool = False,
) -> tuple[NoticeAIResult, str]:
    """단일 Notice 처리.

    - force=True: 기존 success 결과 무시하고 처음부터 재처리
    - 부분 성공 시 last_stage 이후만 이어서 처리

    반환:
      (result, action) — action은 'processed' / 'skipped' / 'failed'
    """
    result, _ = NoticeAIResult.objects.get_or_create(notice=notice)

    # AI 입력은 extracted_content 우선 (spec 9.1.6 VLM 전처리 결과).
    effective_content = notice.effective_content
    current_hash = compute_content_hash(effective_content)
    needs_reprocess = (
        force
        or result.status != 'success'
        or result.content_hash != current_hash
    )
    if not needs_reprocess:
        return result, 'skipped'

    # 본문이 바뀌었거나 force면 처음부터 다시
    if force or result.content_hash != current_hash:
        result.notice_type = ''
        result.summary = ''
        result.cards = []
        result.last_stage = ''

    result.status = 'processing'
    result.error_message = ''
    result.content_hash = current_hash
    result.model_name = settings.OPENAI_MODEL
    result.save(update_fields=[
        'status', 'error_message', 'content_hash', 'model_name',
        'notice_type', 'summary', 'cards', 'last_stage', 'updated_at',
    ])

    try:
        # Stage 1: 분류 (이미 결과 있으면 skip)
        if not result.notice_type:
            result.notice_type = pipeline.classify(effective_content)
            result.last_stage = 'classify'
            result.save(update_fields=['notice_type', 'last_stage', 'updated_at'])

        # Stage 2: 요약
        if not result.summary:
            result.summary = pipeline.summarize(effective_content)
            result.last_stage = 'summarize'
            result.save(update_fields=['summary', 'last_stage', 'updated_at'])

        # Stage 3: 카드 구조화
        if not result.cards:
            result.cards = pipeline.build_cards(effective_content, result.notice_type)
            result.last_stage = 'build_cards'

        result.status = 'success'
        result.save(update_fields=['cards', 'last_stage', 'status', 'updated_at'])
        logger.info('[AI:%s] 처리 성공 (last_stage=%s)',
                    notice.id, result.last_stage)
        return result, 'processed'

    except (AIClientError, ValueError) as e:
        result.status = 'failed'
        result.error_message = str(e)[:1000]
        result.retry_count += 1
        result.save(update_fields=[
            'status', 'error_message', 'retry_count', 'updated_at',
        ])
        logger.warning(
            '[AI:%s] 처리 실패 (last_stage=%s, err=%s)',
            notice.id, result.last_stage, e,
        )
        return result, 'failed'


def process_notices(
    notices: Iterable[Notice], *, force: bool = False,
) -> ProcessResult:
    """여러 Notice를 순차 처리하면서 집계."""
    summary = ProcessResult()
    for notice in notices:
        try:
            _, action = process_notice(notice, force=force)
            if action == 'processed':
                summary.success += 1
            elif action == 'skipped':
                summary.skipped += 1
            else:
                summary.failed += 1
        except Exception:
            # 오케스트레이터 자체에 예상 못한 버그가 있어도 다음 항목 계속 진행
            logger.exception('[AI:%s] 처리 중 예외', notice.id)
            summary.failed += 1
    return summary


def get_pending_notices(
    *, sources: list[str] | None = None,
    ids: list[int] | None = None,
    limit: int | None = None,
    reprocess: bool = False,
):
    """처리 대상 Notice 쿼리셋 반환.

    기본 동작:
      - status가 success가 아닌 결과를 가진 Notice
      - AI 결과가 없는 Notice (ai_result OneToOne reverse가 없음)
      - 본문이 변경된 Notice (content_hash 불일치)는 캐시되지 않은 채 처리에서 검출됨
        → process_notice가 자체 판단하므로 여기서는 단순 필터만
    """
    qs = Notice.objects.all()

    if ids:
        qs = qs.filter(id__in=ids)
    if sources:
        qs = qs.filter(source__in=sources)

    if reprocess:
        # 모든 Notice 대상
        pass
    else:
        # status='success'가 아니거나 ai_result가 아예 없는 것만
        # (content 변경 감지는 process_notice 안에서 hash 비교로 처리)
        qs = qs.filter(
            ai_result__isnull=True,
        ) | qs.exclude(
            ai_result__status='success',
        )

    qs = qs.distinct().order_by('-published_at', 'id')
    if limit:
        qs = qs[:limit]
    return qs
