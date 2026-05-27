from django.contrib import admin

from .models import UserPreference


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'max_credits', 'min_credits', 'no_morning', 'no_evening', 'lunch_break', 'updated_at')
    list_filter = ('no_morning', 'no_evening', 'lunch_break')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('updated_at',)
