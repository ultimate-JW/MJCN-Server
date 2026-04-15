import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import InterestArea, CourseHistory, CurrentCourse

User = get_user_model()


# ─── 인증 ───

class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    password_confirm = serializers.CharField(write_only=True)

    def validate_email(self, value):
        # 이메일 정규화: 대소문자 구분 없이 저장/비교하여
        # 'Abc@mju.ac.kr'과 'abc@mju.ac.kr'을 동일 계정으로 처리
        normalized = value.strip().lower()
        if User.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError('이미 사용 중인 이메일입니다.')
        return normalized

    def validate_password(self, value):
        if len(value) < 8 or len(value) > 20:
            raise serializers.ValidationError('비밀번호는 8자 이상 20자 이하여야 합니다.')
        if not re.search(r'[a-zA-Z]', value):
            raise serializers.ValidationError('비밀번호에 영문이 포함되어야 합니다.')
        if not re.search(r'[0-9]', value):
            raise serializers.ValidationError('비밀번호에 숫자가 포함되어야 합니다.')
        if not re.search(r'[^a-zA-Z0-9]', value):
            raise serializers.ValidationError('비밀번호에 특수문자가 포함되어야 합니다.')
        return value

    def validate(self, data):
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError({'password_confirm': '비밀번호가 일치하지 않습니다.'})
        if data['email'] == data['password']:
            raise serializers.ValidationError({'password': '이메일과 동일한 비밀번호는 사용할 수 없습니다.'})
        # Django 기본 password validators 적용 (common password, 숫자-only,
        # 사용자 정보 유사성 등). AUTH_PASSWORD_VALIDATORS 설정 참조.
        try:
            django_validate_password(data['password'], user=User(email=data['email']))
        except DjangoValidationError as e:
            raise serializers.ValidationError({'password': list(e.messages)})
        return data

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        return User.objects.create_user(**validated_data)


class VerifyEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=8)


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class KakaoLoginSerializer(serializers.Serializer):
    authorization_code = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=8)


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=8)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        if len(value) < 8 or len(value) > 20:
            raise serializers.ValidationError('비밀번호는 8자 이상 20자 이하여야 합니다.')
        if not re.search(r'[a-zA-Z]', value):
            raise serializers.ValidationError('비밀번호에 영문이 포함되어야 합니다.')
        if not re.search(r'[0-9]', value):
            raise serializers.ValidationError('비밀번호에 숫자가 포함되어야 합니다.')
        if not re.search(r'[^a-zA-Z0-9]', value):
            raise serializers.ValidationError('비밀번호에 특수문자가 포함되어야 합니다.')
        # Django 기본 password validators 적용 (common password, 숫자-only 등).
        # user 컨텍스트는 view에서 처리되므로 여기서는 기본 검증만.
        try:
            django_validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        return value


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class WithdrawSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)


# ─── 프로필 ───

class InterestAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = InterestArea
        fields = ['id', 'category', 'custom_text']


class CourseHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseHistory
        fields = ['id', 'course_name', 'course_code', 'year', 'semester',
                  'grade_received', 'category', 'credits']

    def validate_credits(self, value):
        if value < 1 or value > 10:
            raise serializers.ValidationError('학점은 1 이상 10 이하여야 합니다.')
        return value

    def validate_semester(self, value):
        if value not in (1, 2):
            raise serializers.ValidationError('semester는 1(봄학기) 또는 2(가을학기)만 허용됩니다.')
        return value

    def validate_year(self, value):
        if value < 1900 or value > 2100:
            raise serializers.ValidationError('year는 1900 이상 2100 이하여야 합니다.')
        return value


class CurrentCourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = CurrentCourse
        fields = ['id', 'course_name', 'course_code', 'day_of_week',
                  'start_time', 'end_time', 'professor', 'room', 'building']

    def validate(self, data):
        # PUT은 전체, PATCH는 부분 — 병합된 최종 상태로 검증
        instance = self.instance
        start = data.get('start_time', instance.start_time if instance else None)
        end = data.get('end_time', instance.end_time if instance else None)
        if start is not None and end is not None and start >= end:
            raise serializers.ValidationError({
                'end_time': 'end_time은 start_time보다 이후여야 합니다.',
            })
        return data


class ProfileSerializer(serializers.ModelSerializer):
    interests = InterestAreaSerializer(many=True, read_only=True)
    course_histories = CourseHistorySerializer(many=True, read_only=True)
    current_courses = CurrentCourseSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'grade', 'semester', 'admission_year',
                  'graduation_year', 'graduation_month', 'major',
                  'is_email_verified', 'is_onboarding_completed', 'notification_enabled',
                  'interests', 'course_histories', 'current_courses']
        read_only_fields = ['id', 'email', 'is_email_verified']


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['name', 'grade', 'semester', 'admission_year',
                  'graduation_year', 'graduation_month', 'major',
                  'is_onboarding_completed']

    def validate_name(self, value):
        if value and (len(value) < 2 or len(value) > 10):
            raise serializers.ValidationError('이름은 2자 이상 10자 이하여야 합니다.')
        if value and not re.match(r'^[가-힣a-zA-Z]+$', value):
            raise serializers.ValidationError('이름은 한글 또는 영어만 입력 가능합니다.')
        return value

    def validate_admission_year(self, value):
        if value is not None and (value < 1900 or value > 2100):
            raise serializers.ValidationError('admission_year는 1900 이상 2100 이하여야 합니다.')
        return value

    def validate_graduation_year(self, value):
        if value is not None and (value < 1900 or value > 2100):
            raise serializers.ValidationError('graduation_year는 1900 이상 2100 이하여야 합니다.')
        return value

    def validate(self, data):
        # 졸업 희망 시기: graduation_year와 graduation_month는 세트로 관리
        # "선택 안 함" = 둘 다 null / 선택 시 = 둘 다 값
        # PATCH의 경우 일부 필드만 전송될 수 있으므로 병합된 최종 상태로 검증
        instance = self.instance
        new_year = data.get(
            'graduation_year',
            instance.graduation_year if instance else None,
        )
        new_month = data.get(
            'graduation_month',
            instance.graduation_month if instance else None,
        )
        if (new_year is None) != (new_month is None):
            raise serializers.ValidationError({
                'graduation_year': 'graduation_year와 graduation_month는 둘 다 값을 가지거나 둘 다 null이어야 합니다 ("선택 안 함").',
            })
        if new_month is not None and new_month not in (2, 8):
            raise serializers.ValidationError({
                'graduation_month': 'graduation_month는 2 또는 8만 허용됩니다.',
            })
        return data


class SettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['notification_enabled', 'notification_chat', 'notification_notice', 'notification_information']
