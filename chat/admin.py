from django.contrib import admin

from .models import ChatAttachment, ChatMessage, ChatRoom


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    fields = ['role', 'content', 'created_at']
    readonly_fields = ['created_at']


@admin.register(ChatRoom)
class ChatRoomAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'title', 'category', 'updated_at']
    list_filter = ['category']
    search_fields = ['user__email', 'title']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'room', 'role', 'created_at']
    list_filter = ['role']
    readonly_fields = ['created_at']


@admin.register(ChatAttachment)
class ChatAttachmentAdmin(admin.ModelAdmin):
    list_display = ['id', 'message', 'file_type', 'original_name', 'created_at']
    list_filter = ['file_type']
    readonly_fields = ['created_at']
