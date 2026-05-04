from django.contrib import admin

from .models import Notice


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ('id', 'source', 'title', 'published_at', 'end_date', 'created_at')
    list_filter = ('source', 'published_at')
    search_fields = ('title', 'content', 'url')
    ordering = ('-published_at',)
    readonly_fields = ('created_at',)
