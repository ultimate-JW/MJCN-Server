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
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken

from .authentication import blacklist_current_access_token
from .throttles import VerifyEmailPerEmailThrottle, PasswordResetPerEmailThrottle

from .models import InterestArea, CourseHistory, CurrentCourse
from .serializers import (
    SignupSerializer, VerifyEmailSerializer, ResendVerificationSerializer,
    LoginSerializer, LogoutSerializer, KakaoLoginSerializer,
    PasswordResetRequestSerializer, PasswordResetVerifySerializer, PasswordResetConfirmSerializer,
    ProfileSerializer, ProfileUpdateSerializer, SettingsSerializer,
    InterestAreaSerializer, CourseHistorySerializer, CurrentCourseSerializer,
    WithdrawSerializer,
)
from .services import (
    send_verification_email, verify_code,
    KakaoOAuthError, exchange_kakao_token, fetch_kakao_user, extract_kakao_profile,
)

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
        # 타이밍 공격 방어: 계정이 없을 때도 해시 연산을 수행하여
        # 존재/미존재 응답 시간을 비슷하게 맞춤 (Django ModelBackend 패턴)
        User().set_password(password)
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
    """카카오 OAuth 로그인 (spec 5.1.3 / 6.1).

    플로우:
      1. authorization_code → 카카오 access_token 교환
      2. access_token → 카카오 사용자 정보 조회
      3. kakao_id 기준 User get_or_create (신규/기존 분기)
         - 이메일 중복 시 기존 계정에 kakao_id 연동
         - 신규는 set_unusable_password + is_email_verified=True
      4. SimpleJWT 발급 후 {access, refresh, is_new_user, user} 응답
    """
    serializer = KakaoLoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    code = serializer.validated_data['authorization_code']

    # 1~2. 카카오 API 호출
    try:
        token_data = exchange_kakao_token(code)
        user_info = fetch_kakao_user(token_data['access_token'])
    except KakaoOAuthError as e:
        if e.kind == 'upstream':
            return Response(
                {'detail': '카카오 인증 서버와 통신할 수 없습니다.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        # invalid_code, malformed 등은 클라이언트 측 문제로 처리
        return Response(
            {'detail': '카카오 인증에 실패했습니다.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    profile = extract_kakao_profile(user_info)
    kakao_id = profile['kakao_id']

    # 3. User get_or_create (kakao_id 우선, 이메일 중복 시 연동)
    is_new_user = False
    user = User.objects.filter(kakao_id=kakao_id).first()
    if user is None:
        # kakao_id 매칭 없음 → 이메일로 기존 계정 확인 (계정 연동)
        existing_by_email = None
        if profile['email']:
            existing_by_email = User.objects.filter(
                email__iexact=profile['email']
            ).first()

        if existing_by_email and not existing_by_email.kakao_id:
            # 기존 이메일 가입 계정 + 카카오 미연동 → 연동
            existing_by_email.kakao_id = kakao_id
            existing_by_email.save(update_fields=['kakao_id'])
            user = existing_by_email
        else:
            # 진짜 신규 가입
            try:
                with transaction.atomic():
                    user = User.objects.create(
                        email=profile['email'],
                        name=profile['name'],
                        kakao_id=kakao_id,
                        is_email_verified=True,  # 카카오 인증 거침
                        is_onboarding_completed=False,  # 온보딩 플로우로 유도
                    )
                    user.set_unusable_password()
                    user.save(update_fields=['password'])
            except IntegrityError:
                # 동시 가입 경쟁 — 다시 조회
                user = User.objects.filter(kakao_id=kakao_id).first()
                if user is None:
                    return Response(
                        {'detail': '회원 생성 중 오류가 발생했습니다.'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
            is_new_user = True

    # 4. JWT 발급
    refresh = RefreshToken.for_user(user)
    return Response({
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'is_new_user': is_new_user,
        'user': {
            'id': user.id,
            'email': user.email,
            'name': user.name,
            'is_email_verified': user.is_email_verified,
            'is_onboarding_completed': user.is_onboarding_completed,
        },
    })


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

    # 현재 요청에 사용된 access token도 남은 TTL 동안 블랙리스트 처리하여
    # refresh 무효화 후에도 access token이 만료까지 유효한 채로 남는 문제 방지
    blacklist_current_access_token(request)

    return Response({'detail': '로그아웃되었습니다.'})


# ─── 비밀번호 재설정 ───

@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_request(request):
    serializer = PasswordResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data['email'].strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    # 미가입/카카오 전용 계정/SMTP 실패 모두 동일 응답 (enumeration 방지).
    # 과거 카카오 계정일 때 400을 반환하여 "계정 존재 + 카카오" 여부가
    # 오라클로 노출되던 문제 수정.
    if user is not None and not (user.kakao_id and not user.has_usable_password()):
        try:
            send_verification_email(user, purpose='password_reset')
        except SMTPException:
            pass

    return Response({'detail': '인증 코드가 발송되었습니다.'})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([AnonRateThrottle, PasswordResetPerEmailThrottle])
def password_reset_verify(request):
    serializer = PasswordResetVerifySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data['email'].strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return Response({'detail': '인증 코드가 일치하지 않습니다.'}, status=status.HTTP_400_BAD_REQUEST)

    # consume=False: verify 단계에서는 코드를 소모하지 않음.
    # 소모 시 뒤이은 password_reset_confirm이 실패하여 재설정 플로우가 막힘.
    _, error = verify_code(user, serializer.validated_data['code'], purpose='password_reset', consume=False)
    if error:
        return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'detail': '인증 코드가 확인되었습니다.'})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([AnonRateThrottle, PasswordResetPerEmailThrottle])
def password_reset_confirm(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data['email'].strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return Response({'detail': '인증 코드가 일치하지 않습니다.'}, status=status.HTTP_400_BAD_REQUEST)

    _, error = verify_code(user, serializer.validated_data['code'], purpose='password_reset')
    if error:
        return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])
        # 비밀번호 변경 시 기존에 발급된 refresh token 전부 블랙리스트 처리
        # (탈취된 세션이 변경 후에도 유효한 채로 남는 문제 방지)
        for token in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=token)

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
    # 탈취된 access token으로 즉시 탈퇴되는 것을 막기 위해 비밀번호 재확인.
    # 카카오 전용 계정처럼 비밀번호가 없는 경우는 password 없이 허용.
    serializer = WithdrawSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = request.user
    if user.has_usable_password():
        if not user.check_password(serializer.validated_data['password']):
            return Response(
                {'detail': '비밀번호가 올바르지 않습니다.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

    user.delete()
    return Response({'detail': '회원 탈퇴가 완료되었습니다.'}, status=status.HTTP_200_OK)


# ─── 6.3 관심분야 ───

class InterestAreaViewSet(viewsets.ModelViewSet):
    serializer_class = InterestAreaSerializer
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        return InterestArea.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        # 동시 요청으로 3개 초과 생성되는 경쟁 조건 방어:
        # 트랜잭션 내에서 User 행을 잠근 뒤 count → create 순서로 직렬화
        with transaction.atomic():
            User.objects.select_for_update().get(pk=self.request.user.pk)
            if InterestArea.objects.filter(user=self.request.user).count() >= 3:
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
