from django.contrib import admin

from .models import Notice, NoticeAIResult


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ('id', 'source', 'title', 'published_at', 'end_date', 'created_at')
    list_filter = ('source', 'published_at')
    search_fields = ('title', 'content', 'url')
    ordering = ('-published_at',)
    readonly_fields = ('created_at',)


@admin.register(NoticeAIResult)
class NoticeAIResultAdmin(admin.ModelAdmin):
    list_display = (
        'notice_id', 'status', 'notice_type', 'last_stage',
        'retry_count', 'model_name', 'updated_at',
    )
    list_filter = ('status', 'notice_type', 'model_name')
    search_fields = ('notice__title', 'summary')
    ordering = ('-updated_at',)
    readonly_fields = ('created_at', 'updated_at', 'content_hash')
