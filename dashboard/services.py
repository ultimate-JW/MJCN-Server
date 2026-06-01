"""대시보드 메인화면 데이터 집계 (spec 5.8 / 6.10).

새 모델 없이 기존 앱(courses / notices / information / notifications / accounts)
데이터를 읽어 단일 응답으로 조합하는 읽기 전용 집계 로직.
"""
from __future__ import annotations

from datetime import date

from django.db.models import Max, Q
from django.utils import timezone

from accounts.serializers import CurrentCourseSerializer
from common.matching import extract_user_keywords, score_match, sort_by_match  # sort_by_match는 information에만
from courses.services import build_academic_guide, calc_graduation_progress
from information.models import Information
from notices.models import Notice
from notices.serializers import NoticeListSerializer
from notifications.models import Notification

from .serializers import DashboardInformationSerializer

# 관심사 기반 공지·정보 노출 개수 (spec 5.8).
# 맞춤형(매칭) 항목을 우선 노출하되 부족분은 최신/마감임박 순으로 채운다.
FEED_SIZE = 3

# date.weekday() 0=월 … 6=일 → CurrentCourse.day_of_week 한글 코드
_WEEKDAY_KO = ['월', '화', '수', '목', '금', '토', '일']


def _end_date_key(info) -> int:
    """정보 정렬 2차 키 — 마감 임박(end_date 오름차순) 순, NULL은 마지막."""
    if info.end_date is None:
        return date.max.toordinal()
    return info.end_date.toordinal()


def _latest_batch_count(model) -> int:
    """가장 최근 크롤 배치에서 신규 수집된 건수 (spec 6.10, #165).

    Max(created_at)의 (로컬) 날짜를 구해 그 날짜에 생성된 행 수를 센다.
    크롤러가 update_or_create + created_at=auto_now_add라 기존 항목 재크롤은
    created_at이 안 바뀌므로 진짜 신규만 카운트된다. 읽음·개인화 무관 전역 값.
    """
    latest = model.objects.aggregate(m=Max('created_at'))['m']
    if latest is None:
        return 0
    latest_date = timezone.localtime(latest).date()
    return model.objects.filter(created_at__date=latest_date).count()


def build_dashboard(user) -> dict:
    """메인화면 집계 응답 dict 생성 (spec 6.10)."""
    today = timezone.localdate()
    weekday = _WEEKDAY_KO[today.weekday()]
    user_keywords = extract_user_keywords(user)

    # 오늘 요일 수강과목 — 시작 시간순
    today_courses = list(
        user.current_courses.filter(day_of_week=weekday).order_by('start_time')
    )

    # 공지: 모든 공지를 published_at 최신순으로 단일 정렬 (#155).
    # 학사공지 우선 부스트나 매칭 정렬은 적용하지 않는다 — 학사공지도 자기 게시
    # 시점에 따라 자연스럽게 노출. PUSH fanout(spec §6.9)이 학사공지를 모든
    # 사용자에게 발송하므로 본 슬롯에서는 누락 위험 낮음.
    # match_score는 정렬 키 아닌 정보 노출용 — NoticeListSerializer가 응답에 포함.
    notices = list(
        Notice.objects.select_related('ai_result').order_by('-published_at')[:FEED_SIZE]
    )
    for n in notices:
        n.match_score = score_match(user_keywords, n.tags or [])

    # 정보: 활성 & 미마감 항목만 → 매칭 점수 ↓ → 마감 임박 순
    info_qs = Information.objects.filter(is_active=True).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    )
    information = sort_by_match(
        list(info_qs),
        user_keywords,
        tags_attr='tags',  # AI 태깅 기준 매칭 (#185) — categories는 활동유형이라 매칭 안 됨
        secondary_key=_end_date_key,
    )[:FEED_SIZE]

    return {
        'greeting': {
            # 화면 표시용 인사 문구 — 서버에서 완성 (프론트가 그대로 노출). 이름 없으면 폴백
            'message': (
                f'안녕하세요, {user.name}님! 오늘은 총 {len(today_courses)}개의 수업이 있어요.'
                if user.name else
                f'안녕하세요! 오늘은 총 {len(today_courses)}개의 수업이 있어요.'
            ),
            'weekday': weekday,
            'today_class_count': len(today_courses),
        },
        'graduation_progress_percent': calc_graduation_progress(user),
        # 'AI 가이드 - 지금 해야 할 일' 배너 — 임박한 학사 마감 1건 또는 None (배너 숨김)
        'ai_guide': build_academic_guide(today),
        'today_schedule': CurrentCourseSerializer(today_courses, many=True).data,
        'notices': NoticeListSerializer(notices, many=True).data,
        'information': DashboardInformationSerializer(information, many=True).data,
        # 상단 통계 칩 — 가장 최근 크롤 배치 신규 건수 (전역, 읽음 무관, #165)
        'new_notice_count': _latest_batch_count(Notice),
        'new_information_count': _latest_batch_count(Information),
        'unread_notification_count': Notification.objects.filter(
            user=user, is_read=False,
        ).count(),
    }
