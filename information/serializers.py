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
            'start_date', 'end_date', 'categories', 'is_active',
            'source', 'source_id',
            'match_score',
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


class ContestCardSerializer(serializers.ModelSerializer):
    """공모전 테마 상세 카드 (이슈 #184). 축소형 — 주최/분야/마감/링크만.

    dday는 services.build_contest_guide가 인스턴스에 부여한 서버 계산값
    (프론트가 계산하지 않도록 응답에 포함). 마감일 없으면 null.
    """

    dday = serializers.IntegerField(allow_null=True, read_only=True)

    class Meta:
        model = Information
        fields = ['id', 'title', 'organizer', 'categories', 'end_date', 'dday', 'url']
