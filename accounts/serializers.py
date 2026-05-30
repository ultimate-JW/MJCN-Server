import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from courses.models import CourseOffering

from .fields import ASCIIEmailField
from .models import InterestArea, CourseHistory, CurrentCourse, Bookmark, PendingSignup

User = get_user_model()


# ─── 인증 ───

class SignupSerializer(serializers.Serializer):
    email = ASCIIEmailField()
    password = serializers.CharField(write_only=True)
    password_confirm = serializers.CharField(write_only=True)

    def validate_email(self, value):
        # 이메일 정규화: 대소문자 구분 없이 저장/비교하여
        # 'Abc@mju.ac.kr'과 'abc@mju.ac.kr'을 동일 계정으로 처리.
        # 이미 인증 완료된 User만 차단 — PendingSignup이 있는 경우는
        # views.signup에서 update_or_create로 자연스럽게 갱신 처리한다.
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

    # NOTE: 이전과 달리 SignupSerializer.create()는 User를 만들지 않는다.
    # PendingSignup row 생성은 views.signup이 직접 처리 (User INSERT는 verify 시점).


class VerifyEmailSerializer(serializers.Serializer):
    email = ASCIIEmailField()
    code = serializers.CharField(max_length=8)


class ResendVerificationSerializer(serializers.Serializer):
    email = ASCIIEmailField()


class LoginSerializer(serializers.Serializer):
    email = ASCIIEmailField()
    password = serializers.CharField(write_only=True)


class KakaoLoginSerializer(serializers.Serializer):
    authorization_code = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = ASCIIEmailField()


class PasswordResetVerifySerializer(serializers.Serializer):
    email = ASCIIEmailField()
    code = serializers.CharField(max_length=8)


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = ASCIIEmailField()
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
        # liberal_subtype / core_area — CourseHistory.save() override가 course_code로
        # Course에서 자동 복사 (#47 Phase 2). 프론트가 응답으로 확인 가능하도록 노출 (#114).
        fields = ['id', 'course_name', 'course_code', 'year', 'semester',
                  'grade_received', 'category', 'credits',
                  'liberal_subtype', 'core_area']

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
    """현재 수강과목 — `offering_id` 한 개로 7개 평문 필드 자동 hydrate (spec 4.2, #149).

    POST·PUT·PATCH 모두 `offering_id` write-only 필드만 받음. 시리얼라이저가
    CourseOffering + 첫 schedule을 조회해 course_name·code·요일·시간·교수·강의실을 채움.
    응답은 평문 필드 read-only로 노출 (snapshot).
    """
    offering_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = CurrentCourse
        fields = ['id', 'offering_id', 'course_name', 'course_code', 'day_of_week',
                  'start_time', 'end_time', 'professor', 'room']
        read_only_fields = ['id', 'course_name', 'course_code', 'day_of_week',
                            'start_time', 'end_time', 'professor', 'room']

    def validate_offering_id(self, value):
        try:
            offering = (
                CourseOffering.objects
                .select_related('course')
                .prefetch_related('schedules')
                .get(pk=value)
            )
        except CourseOffering.DoesNotExist:
            raise serializers.ValidationError('존재하지 않는 분반입니다.')
        if not offering.schedules.exists():
            raise serializers.ValidationError('강의 시간 정보가 없습니다.')
        # create/update 재호출 비용 절감 — 검증 시점에 조회한 객체를 캐시
        self._offering = offering
        return value

    def validate(self, data):
        # unique_together (user, day_of_week, start_time) 사전 체크 →
        # IntegrityError(500) 대신 깔끔한 400.
        offering = getattr(self, '_offering', None)
        if offering is None:
            return data
        schedule = offering.schedules.first()
        user = self.context['request'].user
        qs = CurrentCourse.objects.filter(
            user=user,
            day_of_week=schedule.day_of_week,
            start_time=schedule.start_time,
        )
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {'offering_id': '같은 시간대에 이미 다른 과목이 등록돼 있습니다.'}
            )
        return data

    def _snapshot(self, offering: CourseOffering) -> dict:
        # 분반당 schedule은 보통 1건. 둘 이상이면 첫 건 사용 (자동 매칭 단순화).
        schedule = offering.schedules.first()
        return {
            'course_name': offering.course.name,
            'course_code': offering.course.course_code,
            'day_of_week': schedule.day_of_week,
            'start_time': schedule.start_time,
            'end_time': schedule.end_time,
            'professor': offering.professor or offering.course.professor or '',
            'room': schedule.room or '',
        }

    def create(self, validated_data):
        snapshot = self._snapshot(self._offering)
        return CurrentCourse.objects.create(
            user=self.context['request'].user, **snapshot,
        )

    def update(self, instance, validated_data):
        snapshot = self._snapshot(self._offering)
        for field, value in snapshot.items():
            setattr(instance, field, value)
        instance.save(update_fields=list(snapshot.keys()))
        return instance


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


# ─── 북마크 ───

class BookmarkCreateSerializer(serializers.ModelSerializer):
    """POST /api/v1/bookmarks/ 요청·응답용."""

    class Meta:
        model = Bookmark
        fields = ['id', 'content_type', 'object_id', 'created_at']
        read_only_fields = ['id', 'created_at']


class BookmarkListSerializer(serializers.ModelSerializer):
    """GET /api/v1/bookmarks/ 목록 응답용.

    `target` 필드에 Notice 또는 Information 메타를 nest해서 노출.
    뷰의 get_serializer_context()에서 'notice_map'/'info_map' 주입 필요 (N+1 회피).
    """
    target = serializers.SerializerMethodField()

    class Meta:
        model = Bookmark
        fields = ['id', 'content_type', 'object_id', 'created_at', 'target']

    # content_type에 따라 NoticeListSerializer 또는 InformationListSerializer
    # 결과를 반환 — polymorphic. Swagger에는 nullable object로 명시.
    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_target(self, obj):
        # 지연 import — 순환 의존 방지
        from notices.serializers import NoticeListSerializer
        from information.serializers import InformationListSerializer

        if obj.content_type == Bookmark.CONTENT_TYPE_NOTICE:
            notice_map = self.context.get('notice_map', {})
            notice = notice_map.get(obj.object_id)
            return NoticeListSerializer(notice).data if notice else None

        if obj.content_type == Bookmark.CONTENT_TYPE_INFORMATION:
            info_map = self.context.get('info_map', {})
            info = info_map.get(obj.object_id)
            return InformationListSerializer(info).data if info else None

        return None
