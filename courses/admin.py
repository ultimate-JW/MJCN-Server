from django.contrib import admin

from .models import (
    AcademicCalendar,
    Course,
    CoursePrerequisite,
    CourseSchedule,
    GraduationRequirement,
)


class CourseScheduleInline(admin.TabularInline):
    model = CourseSchedule
    extra = 1


class CoursePrerequisiteInline(admin.TabularInline):
    model = CoursePrerequisite
    fk_name = 'course'
    extra = 0


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ['course_code', 'name', 'college', 'department', 'major', 'category', 'credits', 'professor']
    list_filter = ['college', 'department', 'category', 'year_open', 'semester_open']
    search_fields = ['course_code', 'name', 'professor']
    inlines = [CourseScheduleInline, CoursePrerequisiteInline]


@admin.register(GraduationRequirement)
class GraduationRequirementAdmin(admin.ModelAdmin):
    list_display = ['department', 'admission_year', 'category', 'required_credits', 'total_required']
    list_filter = ['department', 'admission_year', 'category']


@admin.register(AcademicCalendar)
class AcademicCalendarAdmin(admin.ModelAdmin):
    list_display = ['year', 'semester', 'semester_start', 'semester_end']
    list_filter = ['year', 'semester']
