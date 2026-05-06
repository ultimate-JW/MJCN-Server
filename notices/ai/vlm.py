"""VLM(Vision Language Model) 전처리 (spec 9.1.6).

이미지로만 구성된 공지의 텍스트를 gpt-4o-mini Vision 입력으로 추출.
추출 결과를 Notice.extracted_content에 저장하면 다음 텍스트 파이프라인이
content_hash 변경 감지로 자동 재처리한다.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from dataclasses import dataclass
from typing import Iterable

import requests
from django.conf import settings

from notices.models import Notice

from .client import AIClientError, get_client
from .prompts import VLM_EXTRACT_SYSTEM

logger = logging.getLogger(__name__)


@dataclass
class VLMResult:
    """VLM 처리 집계."""
    success: int = 0
    failed: int = 0
    skipped: int = 0  # image_urls 비어있거나 이미 extracted_content 있음

    @property
    def total(self) -> int:
        return self.success + self.failed + self.skipped


def _fetch_image_as_data_url(url: str) -> str:
    """이미지를 직접 다운로드해 data URL(base64)로 변환.

    OpenAI가 외부 이미지 URL을 fetch하는 데 실패하는 경우(학교 사이트가
    외부 IP를 throttle하거나 큰 이미지에서 timeout 발생)가 있어,
    서버에서 직접 받아 inline으로 전송하는 게 안정적.
    """
    response = requests.get(
        url,
        headers={'User-Agent': 'Mozilla/5.0 (compatible; MJCN-Crawler/1.0)'},
        timeout=settings.OPENAI_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    content_type = (
        response.headers.get('Content-Type', '').split(';')[0].strip()
        or mimetypes.guess_type(url)[0]
        or 'image/png'
    )
    encoded = base64.b64encode(response.content).decode('ascii')
    return f'data:{content_type};base64,{encoded}'


def extract_text_from_images(image_urls: list[str]) -> str:
    """이미지 URL 리스트 → 추출된 한국어 텍스트.

    한 번의 multimodal 호출로 여러 이미지를 처리한다 (이미지 간 컨텍스트 공유).
    이미지 개수 한도는 settings.OPENAI_VLM_MAX_IMAGES.

    이미지는 서버에서 직접 다운로드해 base64 inline으로 전송 (외부 fetch 우회).
    """
    if not image_urls:
        raise ValueError('image_urls가 비어있음')

    max_images = getattr(settings, 'OPENAI_VLM_MAX_IMAGES', 5)
    urls = list(image_urls)[:max_images]

    client = get_client()
    user_content: list[dict] = [{
        'type': 'text',
        'text': '아래 이미지들에서 한국어 텍스트를 모두 추출해.',
    }]
    for url in urls:
        try:
            data_url = _fetch_image_as_data_url(url)
        except Exception as e:
            raise AIClientError(f'이미지 다운로드 실패 ({url}): {e}') from e
        user_content.append({
            'type': 'image_url',
            'image_url': {'url': data_url},
        })

    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {'role': 'system', 'content': VLM_EXTRACT_SYSTEM},
                {'role': 'user', 'content': user_content},
            ],
        )
    except Exception as e:
        raise AIClientError(f'VLM 호출 실패: {e}') from e

    text = (response.choices[0].message.content or '').strip()
    if not text:
        raise AIClientError('VLM 응답이 비어있음')
    return text


def process_notice_image(notice: Notice, *, force: bool = False) -> str:
    """단일 Notice에 대해 VLM 추출 실행.

    반환값: 'success' / 'skipped' / 'failed'
    """
    if not notice.image_urls:
        return 'skipped'

    if notice.extracted_content and not force:
        return 'skipped'

    try:
        extracted = extract_text_from_images(notice.image_urls)
    except (AIClientError, ValueError) as e:
        logger.warning('[VLM:%s] 추출 실패: %s', notice.id, e)
        return 'failed'

    notice.extracted_content = extracted
    notice.save(update_fields=['extracted_content'])
    logger.info(
        '[VLM:%s] 추출 성공 (%d장 → %d자)',
        notice.id, len(notice.image_urls), len(extracted),
    )
    return 'success'


def process_notice_images(
    notices: Iterable[Notice], *, force: bool = False,
) -> VLMResult:
    """여러 Notice를 순차 처리하면서 집계."""
    summary = VLMResult()
    for notice in notices:
        try:
            action = process_notice_image(notice, force=force)
            if action == 'success':
                summary.success += 1
            elif action == 'skipped':
                summary.skipped += 1
            else:
                summary.failed += 1
        except Exception:
            logger.exception('[VLM:%s] 처리 중 예외', notice.id)
            summary.failed += 1
    return summary


# 텍스트 본문이 이 길이 미만이면 "이미지 전용 공지"로 간주하고 VLM 대상.
# 충분한 텍스트가 이미 있으면 굳이 VLM에 비용 들일 필요 없음.
EMPTY_CONTENT_THRESHOLD = 30


def get_vlm_targets(
    *, sources: list[str] | None = None,
    ids: list[int] | None = None,
    limit: int | None = None,
    reprocess: bool = False,
):
    """VLM 처리 대상 Notice 쿼리셋.

    기본: image_urls가 1개 이상이고, 본문이 짧고(=이미지 전용 추정),
    extracted_content가 비어있는 Notice.
    reprocess=True면 extracted_content/본문 길이 무시하고 image_urls 있으면 모두.
    """
    from django.db.models.functions import Length

    qs = Notice.objects.exclude(image_urls=[])

    if ids:
        qs = qs.filter(id__in=ids)
    if sources:
        qs = qs.filter(source__in=sources)

    if not reprocess:
        qs = (
            qs.annotate(content_len=Length('content'))
              .filter(content_len__lt=EMPTY_CONTENT_THRESHOLD)
              .filter(extracted_content='')
        )

    qs = qs.order_by('-published_at', 'id')
    if limit:
        qs = qs[:limit]
    return qs
