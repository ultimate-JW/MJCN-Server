from django.contrib import admin

from .models import FCMDevice, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'notification_type', 'title', 'is_read', 'is_pushed', 'created_at')
    list_filter = ('notification_type', 'is_read', 'is_pushed')
    search_fields = ('title', 'message', 'user__email')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)


@admin.register(FCMDevice)
class FCMDeviceAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'registration_token_short', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('user__email', 'registration_token')
    ordering = ('-updated_at',)
    readonly_fields = ('created_at', 'updated_at')

    def registration_token_short(self, obj):
        return obj.registration_token[:30] + '...'
    registration_token_short.short_description = 'Token'
