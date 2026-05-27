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
