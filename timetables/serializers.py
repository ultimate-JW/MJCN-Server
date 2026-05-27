"""5.3.6 시간표 추천 응답 직렬화 (#97).

응답 구조:
- TimetableResponseSerializer { plans: [TimetablePlanSerializer], note: str|null }
- TimetablePlanSerializer    { score, total_credits, credits_by_category, offerings, reason_codes }
- CourseOfferingSerializer   { id, course_code, course_name, category, credit, section_no, professor, schedules }
- CourseScheduleSerializer   { day, start_time, end_time, room }

UserPreference는 별도 CRUD (RetrieveUpdateAPIView).
"""
from rest_framework import serializers

from courses.models import CourseOffering, CourseSchedule

from .models import UserPreference


class TimetableRecommendRequestSerializer(serializers.Serializer):
    """POST /timetables/recommend/ 입력 — Swagger 자동 문서화용 메타.

    실제 validation은 view에서 직접 payload dict로 처리 (field-level merge — #97 결정 5).
    이 serializer는 호출되지 않고 drf-spectacular schema 생성용.
    """
    year = serializers.IntegerField(required=False, help_text='추천 대상 학년도 (없으면 400 + MISSING_YEAR_SEMESTER)')
    semester = serializers.IntegerField(required=False, help_text='추천 대상 학기 (1/2)')
    prefer_off_days = serializers.ListField(child=serializers.CharField(), required=False, help_text='공강 선호 요일 (예: ["금"])')
    banned_days = serializers.ListField(child=serializers.CharField(), required=False, help_text='수업 금지 요일')
    no_morning = serializers.BooleanField(required=False, help_text='1교시(09:00 시작) 분반 제외')
    no_evening = serializers.BooleanField(required=False, help_text='18시 이후 시작 분반 제외')
    lunch_break = serializers.BooleanField(required=False, help_text='12-14시 사이 1시간 공강 선호')
    max_credits = serializers.IntegerField(required=False, help_text='학기 학점 상한 (default UserPreference=18)')
    min_credits = serializers.IntegerField(required=False, help_text='학기 학점 하한 (default UserPreference=15)')


class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = (
            'prefer_off_days', 'banned_days',
            'no_morning', 'no_evening', 'lunch_break',
            'max_credits', 'min_credits',
            'updated_at',
        )
        read_only_fields = ('updated_at',)


class CourseScheduleSerializer(serializers.ModelSerializer):
    day = serializers.CharField(source='day_of_week')

    class Meta:
        model = CourseSchedule
        fields = ('day', 'start_time', 'end_time', 'room')


class CourseOfferingSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source='course.course_code')
    course_name = serializers.CharField(source='course.name')
    category = serializers.CharField(source='course.category')
    credit = serializers.IntegerField(source='course.credits')
    schedules = CourseScheduleSerializer(many=True)

    class Meta:
        model = CourseOffering
        fields = (
            'id', 'course_code', 'course_name', 'category',
            'credit', 'section_no', 'professor', 'schedules',
        )


class TimetablePlanSerializer(serializers.Serializer):
    score = serializers.IntegerField()
    total_credits = serializers.IntegerField()
    credits_by_category = serializers.DictField(child=serializers.IntegerField())
    offerings = CourseOfferingSerializer(many=True)
    # reason_codes — list of {code: str, meta?: dict}. drf-spectacular용 자유 형식.
    reason_codes = serializers.ListField(child=serializers.DictField())


class TimetableResponseSerializer(serializers.Serializer):
    plans = TimetablePlanSerializer(many=True)
    note = serializers.CharField(allow_null=True)
