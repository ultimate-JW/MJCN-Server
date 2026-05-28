from django.contrib import admin

from .models import Theme, ThemeItem


class ThemeItemInline(admin.TabularInline):
    model = ThemeItem
    extra = 1
    fields = ('order', 'item_type', 'title', 'external_url', 'content')


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ('id', 'category', 'title', 'is_active', 'order', 'created_at')
    list_filter = ('category', 'is_active')
    list_editable = ('is_active', 'order')
    search_fields = ('title', 'description')
    ordering = ('order', '-created_at')
    readonly_fields = ('created_at',)
    inlines = [ThemeItemInline]


@admin.register(ThemeItem)
class ThemeItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'theme', 'item_type', 'title', 'order')
    list_filter = ('item_type', 'theme__category')
    search_fields = ('title', 'content', 'theme__title')
    ordering = ('theme', 'order')
