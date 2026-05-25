"""drf-spectacular용 응답 스키마 모음 (`/api/docs/` Swagger UI 명시 전용).

함수형 view나 다양한 응답 형태를 가진 endpoint의 OpenAPI 스키마를 생성하기 위해
사용. 실제 API 동작에는 영향 없음 — 응답 데이터는 그대로 dict로 반환.
"""
from rest_framework import serializers


class DetailResponseSerializer(serializers.Serializer):
    """단일 `detail` 메시지 응답 (가장 흔한 형태)."""
    detail = serializers.CharField()


class TokenPairResponseSerializer(serializers.Serializer):
    """로그인 성공 — JWT access/refresh 토큰 쌍."""
    access = serializers.CharField()
    refresh = serializers.CharField()


class VerifyEmailResponseSerializer(serializers.Serializer):
    """이메일 인증 완료 — detail + 토큰 쌍 (인증 후 자동 세션 유지용)."""
    detail = serializers.CharField()
    access = serializers.CharField()
    refresh = serializers.CharField()


class KakaoUserNestedSerializer(serializers.Serializer):
    """카카오 로그인 응답에 포함되는 사용자 메타."""
    id = serializers.IntegerField()
    email = serializers.EmailField()
    name = serializers.CharField()
    is_email_verified = serializers.BooleanField()
    is_onboarding_completed = serializers.BooleanField()


class KakaoLoginResponseSerializer(serializers.Serializer):
    """카카오 OAuth 로그인 — 토큰 + 사용자 정보 + 신규 가입 플래그."""
    access = serializers.CharField()
    refresh = serializers.CharField()
    is_new_user = serializers.BooleanField()
    user = KakaoUserNestedSerializer()
