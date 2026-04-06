import re

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import InterestArea, CourseHistory, CurrentCourse

User = get_user_model()


# ─── 인증 ───

class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    password_confirm = serializers.CharField(write_only=True)

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('이미 사용 중인 이메일입니다.')
        return value

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
        return value


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


# ─── 프로필 ───

class InterestAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = InterestArea
        fields = ['id', 'category', 'custom_text']


class CourseHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseHistory
        fields = ['id', 'course_name', 'year', 'semester',
                  'grade_received', 'category', 'credits']


class CurrentCourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = CurrentCourse
        fields = ['id', 'course_name', 'course_code', 'day_of_week',
                  'start_time', 'end_time', 'professor', 'room', 'building']


class ProfileSerializer(serializers.ModelSerializer):
    interests = InterestAreaSerializer(many=True, read_only=True)
    course_histories = CourseHistorySerializer(many=True, read_only=True)
    current_courses = CurrentCourseSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'grade', 'semester',
                  'graduation_year', 'graduation_month', 'major',
                  'is_email_verified', 'notification_enabled',
                  'interests', 'course_histories', 'current_courses']
        read_only_fields = ['id', 'email', 'is_email_verified']


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['name', 'grade', 'semester', 'graduation_year', 'graduation_month', 'major']

    def validate_name(self, value):
        if value and (len(value) < 2 or len(value) > 10):
            raise serializers.ValidationError('이름은 2자 이상 10자 이하여야 합니다.')
        if value and not re.match(r'^[가-힣a-zA-Z]+$', value):
            raise serializers.ValidationError('이름은 한글 또는 영어만 입력 가능합니다.')
        return value


class SettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['notification_enabled', 'notification_chat', 'notification_notice', 'notification_information']
