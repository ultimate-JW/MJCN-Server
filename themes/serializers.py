from rest_framework import serializers

from .models import Theme, ThemeItem


class ThemeItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ThemeItem
        fields = ['id', 'title', 'content', 'external_url', 'item_type', 'order']


class ThemeListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Theme
        fields = ['id', 'title', 'category', 'description', 'order']


class ThemeDetailSerializer(serializers.ModelSerializer):
    items = ThemeItemSerializer(many=True, read_only=True)

    class Meta:
        model = Theme
        fields = ['id', 'title', 'category', 'description', 'order',
                  'created_at', 'items']
