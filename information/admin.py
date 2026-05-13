from django.contrib import admin

from .models import Information


@admin.register(Information)
class InformationAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'source', 'source_id', 'title', 'organizer',
        'start_date', 'end_date', 'is_active', 'created_at',
    )
    list_filter = ('source', 'is_active', 'start_date', 'end_date')
    search_fields = ('title', 'organizer', 'description', 'url', 'source_id')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)
