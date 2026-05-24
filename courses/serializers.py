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


class ChapelStatusSerializer(serializers.Serializer):
    """채플 이수 회수 진척도 (graduation_requirements.md §2.1).
    학번별 required: 1996~1998 = 2회 / 1999 이후 = 4회."""
    completed = serializers.IntegerField()
    required = serializers.IntegerField()
    remaining = serializers.IntegerField()


class CompletionStatusSerializer(serializers.Serializer):
    categories = CategoryCreditsSerializer(many=True)
    chapel = ChapelStatusSerializer()
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
    """다음학기 추천 한 과목 응답 (spec 5.3.1).

    View에서 many=True로 호출되어 점수 내림차순 정렬된 리스트를 직렬화한다.
    `score`는 디버깅·튜닝 시 우선순위 검증용으로 함께 노출한다.
    """
    score = serializers.IntegerField()
    course_code = serializers.CharField()
    name = serializers.CharField()
    category = serializers.CharField()
    credits = serializers.IntegerField()
    professor = serializers.CharField()
    schedules = CourseScheduleSerializer(many=True)


class SemesterPlanSerializer(serializers.Serializer):
    """학기 1개 응답 — 4 카테고리로 분리 (spec 5.3.2, #25).

    semester 값: 1/2 정규학기, 3 하계 계절학기, 4 동계 계절학기.
    빈 카테고리도 키는 유지 (빈 배열). 프론트가 키 존재 체크 안 해도 됨.
    """
    year = serializers.IntegerField()
    semester = serializers.IntegerField()
    major_required = RecommendedCourseSerializer(many=True)     # 전공필수
    major_elective = RecommendedCourseSerializer(many=True)     # 전공선택
    liberal_required = RecommendedCourseSerializer(many=True)   # 교양필수
    liberal_elective = RecommendedCourseSerializer(many=True)   # 교양선택


class CurriculumPlanSerializer(serializers.Serializer):
    plan_number = serializers.IntegerField()
    max_credits = serializers.IntegerField()                    # 이 plan의 학점 상한
    semesters = SemesterPlanSerializer(many=True)
