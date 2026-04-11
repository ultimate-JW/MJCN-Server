from rest_framework import serializers

from .models import (
    AcademicCalendar,
    Course,
    CoursePrerequisite,
    CourseSchedule,
    GraduationRequirement,
)


class CourseScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseSchedule
        fields = ['day_of_week', 'start_time', 'end_time', 'building', 'room']


class CoursePrerequisiteSerializer(serializers.ModelSerializer):
    prerequisite_code = serializers.CharField(source='prerequisite.course_code', read_only=True)
    prerequisite_name = serializers.CharField(source='prerequisite.name', read_only=True)

    class Meta:
        model = CoursePrerequisite
        fields = ['prerequisite_code', 'prerequisite_name']


class CourseSerializer(serializers.ModelSerializer):
    schedules = CourseScheduleSerializer(many=True, read_only=True)
    prerequisites = CoursePrerequisiteSerializer(many=True, read_only=True)

    class Meta:
        model = Course
        fields = [
            'id', 'course_code', 'name', 'college', 'department', 'major',
            'category', 'credits', 'year_open', 'semester_open', 'professor',
            'schedules', 'prerequisites',
        ]


class CourseListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = [
            'id', 'course_code', 'name', 'college', 'department', 'major',
            'category', 'credits', 'professor',
        ]


class GraduationRequirementSerializer(serializers.ModelSerializer):
    class Meta:
        model = GraduationRequirement
        fields = [
            'id', 'department', 'admission_year', 'category',
            'required_credits', 'total_required',
        ]


class AcademicCalendarSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicCalendar
        fields = [
            'id', 'year', 'semester',
            'pre_registration_start', 'pre_registration_end',
            'registration_start', 'registration_end',
            'adjustment_start', 'adjustment_end',
            'semester_start', 'semester_end',
        ]


# --- 추천/이수현황 응답 Serializer ---

class CategoryCreditsSerializer(serializers.Serializer):
    category = serializers.CharField()
    completed = serializers.IntegerField()
    required = serializers.IntegerField()
    remaining = serializers.IntegerField()


class CompletionStatusSerializer(serializers.Serializer):
    categories = CategoryCreditsSerializer(many=True)
    total_completed = serializers.IntegerField()
    total_required = serializers.IntegerField()
    total_remaining = serializers.IntegerField()


class RecommendedCourseSerializer(serializers.Serializer):
    course_code = serializers.CharField()
    name = serializers.CharField()
    category = serializers.CharField()
    credits = serializers.IntegerField()
    professor = serializers.CharField()
    schedules = CourseScheduleSerializer(many=True)


class NextSemesterRecommendationSerializer(serializers.Serializer):
    major_required = RecommendedCourseSerializer(many=True)
    major_elective = RecommendedCourseSerializer(many=True)
    liberal_required = RecommendedCourseSerializer(many=True)
    liberal_elective = RecommendedCourseSerializer(many=True)


class SemesterPlanSerializer(serializers.Serializer):
    year = serializers.IntegerField()
    semester = serializers.IntegerField()
    courses = RecommendedCourseSerializer(many=True)


class CurriculumPlanSerializer(serializers.Serializer):
    plan_number = serializers.IntegerField()
    semesters = SemesterPlanSerializer(many=True)
