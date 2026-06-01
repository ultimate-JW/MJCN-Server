from rest_framework import serializers

from .models import Information


class InformationListSerializer(serializers.ModelSerializer):
    """목록 응답용."""

    # spec 5.10 — 관심사 매칭 점수. view에서 instance에 attribute로 부여.
    match_score = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Information
        fields = [
            'id', 'title', 'organizer', 'url',
            'start_date', 'end_date', 'categories', 'tags', 'is_active',
            'source', 'source_id',
            'match_score',
        ]


class InformationDetailSerializer(serializers.ModelSerializer):
    """상세 응답 — 설명 본문 포함."""

    class Meta:
        model = Information
        fields = [
            'id', 'title', 'organizer', 'description', 'url',
            'start_date', 'end_date', 'categories', 'tags', 'is_active', 'created_at',
            'source', 'source_id',
        ]
