"""대시보드 메인화면 데이터 집계 (spec 5.8 / 6.10).

새 모델 없이 기존 앱(courses / notices / information / notifications / accounts)
데이터를 읽어 단일 응답으로 조합하는 읽기 전용 집계 로직.
"""
from __future__ import annotations

from datetime import date

from django.db.models import Q
from django.utils import timezone

from accounts.serializers import CurrentCourseSerializer
from common.matching import extract_user_keywords, sort_by_match
from courses.services import calc_graduation_progress
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


def build_dashboard(user) -> dict:
    """메인화면 집계 응답 dict 생성 (spec 6.10)."""
    today = timezone.localdate()
    weekday = _WEEKDAY_KO[today.weekday()]
    user_keywords = extract_user_keywords(user)

    # 오늘 요일 수강과목 — 시작 시간순
    today_courses = list(
        user.current_courses.filter(day_of_week=weekday).order_by('start_time')
    )

    # 공지: 매칭 점수 ↓ → 최신순. sort_by_match가 점수 0 항목도 published_at
    # 내림차순으로 정렬하므로, 상위 N개를 자르면 "맞춤형 우선 + 부족분 최신 채움"이 된다.
    notices = sort_by_match(
        list(Notice.objects.select_related('ai_result').all()),
        user_keywords,
        tags_attr='tags',
        secondary_key=lambda n: -n.published_at.timestamp(),
    )[:FEED_SIZE]

    # 정보: 활성 & 미마감 항목만 → 매칭 점수 ↓ → 마감 임박 순
    info_qs = Information.objects.filter(is_active=True).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    )
    information = sort_by_match(
        list(info_qs),
        user_keywords,
        tags_attr='categories',
        secondary_key=_end_date_key,
    )[:FEED_SIZE]

    return {
        'greeting': {
            'user_name': user.name,
            'weekday': weekday,
            'today_class_count': len(today_courses),
        },
        'graduation_progress_percent': calc_graduation_progress(user),
        'today_schedule': CurrentCourseSerializer(today_courses, many=True).data,
        'notices': NoticeListSerializer(notices, many=True).data,
        'information': DashboardInformationSerializer(information, many=True).data,
        'unread_notification_count': Notification.objects.filter(
            user=user, is_read=False,
        ).count(),
    }
