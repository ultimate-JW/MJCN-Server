from django.contrib import admin

from .models import Contest


@admin.register(Contest)
class ContestAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'organizer', 'start_date', 'end_date', 'is_active', 'created_at')
    list_filter = ('is_active', 'start_date', 'end_date')
    search_fields = ('title', 'organizer', 'description', 'url')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)
