from rest_framework import serializers

from .models import Information


class InformationListSerializer(serializers.ModelSerializer):
    """목록 응답용."""

    class Meta:
        model = Information
        fields = [
            'id', 'title', 'organizer', 'url',
            'start_date', 'end_date', 'categories', 'is_active',
            'source', 'source_id',
        ]


class InformationDetailSerializer(serializers.ModelSerializer):
    """상세 응답 — 설명 본문 포함."""

    class Meta:
        model = Information
        fields = [
            'id', 'title', 'organizer', 'description', 'url',
            'start_date', 'end_date', 'categories', 'is_active', 'created_at',
            'source', 'source_id',
        ]
