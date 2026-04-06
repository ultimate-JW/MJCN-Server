from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, InterestArea, CourseHistory, CurrentCourse, EmailVerification


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'name', 'major', 'grade', 'is_email_verified', 'is_staff']
    search_fields = ['email', 'name']
    ordering = ['email']
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('개인정보', {'fields': ('name', 'grade', 'semester', 'graduation_year', 'graduation_month', 'major', 'kakao_id')}),
        ('설정', {'fields': ('is_email_verified', 'notification_enabled', 'notification_chat', 'notification_notice', 'notification_information')}),
        ('권한', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2'),
        }),
    )


@admin.register(InterestArea)
class InterestAreaAdmin(admin.ModelAdmin):
    list_display = ['user', 'category']


@admin.register(CourseHistory)
class CourseHistoryAdmin(admin.ModelAdmin):
    list_display = ['user', 'course_name', 'year', 'semester']


@admin.register(CurrentCourse)
class CurrentCourseAdmin(admin.ModelAdmin):
    list_display = ['user', 'course_name', 'day_of_week', 'start_time', 'end_time']


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'code', 'purpose', 'created_at', 'is_used']
