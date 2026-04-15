from smtplib import SMTPException

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework import status, viewsets, serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from .throttles import VerifyEmailPerEmailThrottle

from .models import InterestArea, CourseHistory, CurrentCourse
from .serializers import (
    SignupSerializer, VerifyEmailSerializer, ResendVerificationSerializer,
    LoginSerializer, LogoutSerializer, KakaoLoginSerializer,
    PasswordResetRequestSerializer, PasswordResetVerifySerializer, PasswordResetConfirmSerializer,
    ProfileSerializer, ProfileUpdateSerializer, SettingsSerializer,
    InterestAreaSerializer, CourseHistorySerializer, CurrentCourseSerializer,
)
from .services import send_verification_email, verify_code

User = get_user_model()


# ─── 6.1 인증 ───

@api_view(['POST'])
@permission_classes([AllowAny])
def signup(request):
    serializer = SignupSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        # User 생성과 이메일 발송을 하나의 트랜잭션으로 묶어
        # SMTP 실패 시 User도 롤백하여 "계정은 생성됐는데 인증 코드는 못 받은" 상태 방지
        with transaction.atomic():
            user = serializer.save()
            send_verification_email(user, purpose='signup')
    except IntegrityError:
        # validate_email 통과 후 save() 시점 사이의 동시 가입 경쟁 조건 방어
        return Response(
            {'email': ['이미 사용 중인 이메일입니다.']},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except SMTPException:
        return Response(
            {'detail': '인증 코드 발송에 실패했습니다. 잠시 후 다시 시도해주세요.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(
        {'detail': '인증 코드를 이메일로 발송했습니다. 이메일을 확인해주세요.'},
        status=status.HTTP_201_CREATED,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([AnonRateThrottle, VerifyEmailPerEmailThrottle])
def verify_email(request):
    serializer = VerifyEmailSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data['email'].strip().lower()
    code = serializer.validated_data['code']

    # 계정 enumeration 방지 + 이미 인증된 계정 재인증 차단:
    # 존재하지 않는 계정, 이미 인증된 계정, 잘못된 코드를 모두 동일 오류로 응답
    user = User.objects.filter(email__iexact=email).first()
    if not user or user.is_email_verified:
        return Response(
            {'detail': '인증 코드가 일치하지 않습니다.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    _, error = verify_code(user, code, purpose='signup')
    if error:
        return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)

    user.is_email_verified = True
    user.save(update_fields=['is_email_verified'])

    # 인증 완료 시 JWT 토큰 발급 → 프론트가 세션 유지하여
    # 온보딩 중 앱 종료 후 재접속 시 이어서 진행 가능
    refresh = RefreshToken.for_user(user)
    return Response({
        'detail': '이메일 인증이 완료되었습니다.',
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def resend_verification(request):
    serializer = ResendVerificationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    # 미가입/이미 인증 완료/SMTP 실패 모두 동일 응답 (계정 존재 여부 노출 방지)
    email = serializer.validated_data['email'].strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user and not user.is_email_verified:
        try:
            send_verification_email(user, purpose='signup')
        except SMTPException:
            pass
    return Response({'detail': '인증 코드가 발송되었습니다.'})


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data['email'].strip().lower()
    password = serializer.validated_data['password']

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return Response({'detail': '이메일 또는 비밀번호가 올바르지 않습니다.'}, status=status.HTTP_401_UNAUTHORIZED)

    if not user.check_password(password):
        return Response({'detail': '이메일 또는 비밀번호가 올바르지 않습니다.'}, status=status.HTTP_401_UNAUTHORIZED)

    if not user.is_email_verified:
        return Response({'detail': '이메일 인증을 완료해주세요.'}, status=status.HTTP_403_FORBIDDEN)

    refresh = RefreshToken.for_user(user)
    return Response({
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def kakao_login(request):
    serializer = KakaoLoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    # TODO: 카카오 OAuth2 연동 구현
    return Response({'detail': '카카오 로그인은 아직 구현되지 않았습니다.'}, status=status.HTTP_501_NOT_IMPLEMENTED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    serializer = LogoutSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        token = RefreshToken(serializer.validated_data['refresh'])
        # 토큰 소유자와 요청자 일치 검증 (다른 사용자의 refresh 블랙리스트 방지)
        # JWT payload의 user_id는 문자열로 직렬화될 수 있으므로 문자열 비교
        if str(token.get('user_id')) != str(request.user.id):
            return Response(
                {'detail': '유효하지 않은 토큰입니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        token.blacklist()
    except TokenError:
        return Response({'detail': '유효하지 않은 토큰입니다.'}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'detail': '로그아웃되었습니다.'})


# ─── 비밀번호 재설정 ───

@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_request(request):
    serializer = PasswordResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        user = User.objects.get(email=serializer.validated_data['email'])
        if user.kakao_id and not user.has_usable_password():
            return Response({'detail': '카카오 로그인으로 가입된 계정입니다.'}, status=status.HTTP_400_BAD_REQUEST)
        send_verification_email(user, purpose='password_reset')
    except User.DoesNotExist:
        pass  # 미가입 이메일에도 동일한 응답 (계정 존재 여부 노출 방지)

    return Response({'detail': '인증 코드가 발송되었습니다.'})


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_verify(request):
    serializer = PasswordResetVerifySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        user = User.objects.get(email=serializer.validated_data['email'])
    except User.DoesNotExist:
        return Response({'detail': '인증 코드가 일치하지 않습니다.'}, status=status.HTTP_400_BAD_REQUEST)

    _, error = verify_code(user, serializer.validated_data['code'], purpose='password_reset')
    if error:
        return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'detail': '인증 코드가 확인되었습니다.'})


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_confirm(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        user = User.objects.get(email=serializer.validated_data['email'])
    except User.DoesNotExist:
        return Response({'detail': '인증 코드가 일치하지 않습니다.'}, status=status.HTTP_400_BAD_REQUEST)

    _, error = verify_code(user, serializer.validated_data['code'], purpose='password_reset')
    if error:
        return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(serializer.validated_data['new_password'])
    user.save()
    return Response({'detail': '비밀번호가 변경되었습니다.'})


# ─── 6.2 프로필 / 설정 ───

@api_view(['GET', 'PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def profile(request):
    user = request.user
    if request.method == 'GET':
        return Response(ProfileSerializer(user).data)

    serializer = ProfileUpdateSerializer(user, data=request.data, partial=(request.method == 'PATCH'))
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(ProfileSerializer(user).data)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def settings_view(request):
    user = request.user
    if request.method == 'GET':
        return Response(SettingsSerializer(user).data)

    serializer = SettingsSerializer(user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def withdraw(request):
    request.user.delete()
    return Response({'detail': '회원 탈퇴가 완료되었습니다.'}, status=status.HTTP_200_OK)


# ─── 6.3 관심분야 ───

class InterestAreaViewSet(viewsets.ModelViewSet):
    serializer_class = InterestAreaSerializer
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        return InterestArea.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        if self.request.user.interests.count() >= 3:
            raise serializers.ValidationError({'detail': '관심분야는 최대 3개까지 선택 가능합니다.'})
        serializer.save(user=self.request.user)


# ─── 6.4 수강이력 / 현재수강 ───

class CourseHistoryViewSet(viewsets.ModelViewSet):
    serializer_class = CourseHistorySerializer
    http_method_names = ['get', 'post', 'put', 'delete']

    def get_queryset(self):
        return CourseHistory.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class CurrentCourseViewSet(viewsets.ModelViewSet):
    serializer_class = CurrentCourseSerializer
    http_method_names = ['get', 'post', 'put', 'delete']

    def get_queryset(self):
        return CurrentCourse.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
