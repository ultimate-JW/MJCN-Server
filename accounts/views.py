from datetime import timedelta
from smtplib import SMTPException

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status, viewsets, serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken

from drf_spectacular.utils import extend_schema, OpenApiResponse

from .authentication import blacklist_current_access_token
from .throttles import VerifyEmailPerEmailThrottle, PasswordResetPerEmailThrottle

from .models import InterestArea, CourseHistory, CurrentCourse, Bookmark, PendingSignup
from .schemas import (
    DetailResponseSerializer,
    KakaoLoginResponseSerializer,
    TokenPairResponseSerializer,
    VerifyEmailResponseSerializer,
)
from .serializers import (
    SignupSerializer, VerifyEmailSerializer, ResendVerificationSerializer,
    LoginSerializer, LogoutSerializer, KakaoLoginSerializer,
    PasswordResetRequestSerializer, PasswordResetVerifySerializer, PasswordResetConfirmSerializer,
    ProfileSerializer, ProfileUpdateSerializer, SettingsSerializer,
    InterestAreaSerializer, CourseHistorySerializer, CurrentCourseSerializer,
    WithdrawSerializer,
    BookmarkCreateSerializer, BookmarkListSerializer,
)
from .services import (
    send_verification_email, send_signup_code_email, verify_code,
    generate_verification_code,
    KakaoOAuthError, exchange_kakao_token, fetch_kakao_user, extract_kakao_profile,
)

User = get_user_model()


# ─── 6.1 인증 ───

@extend_schema(
    request=SignupSerializer,
    responses={
        201: DetailResponseSerializer,
        400: DetailResponseSerializer,
        503: DetailResponseSerializer,
    },
)
@api_view(['POST'])
@permission_classes([AllowAny])
def signup(request):
    """회원가입 (spec 5.1.1).

    이 시점에는 User row를 만들지 않는다. PendingSignup에 (email, password_hash,
    code, code_expires_at)를 upsert로 저장하고 인증 코드 메일만 발송. User INSERT는
    verify_email에서 인증 코드 검증을 통과한 시점에 일어난다.
    """
    serializer = SignupSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data['email']
    password_hash = make_password(serializer.validated_data['password'])
    code = generate_verification_code()
    code_expires_at = timezone.now() + timedelta(minutes=3)

    # 동시 가입 시도는 update_or_create가 row 덮어쓰기로 흡수.
    # 같은 이메일로 두 번째 signup 호출 시 기존 row의 password_hash·code·expires
    # 가 새 값으로 교체되어 이전 코드는 자동 무효화된다 (spec 5.1.1).
    PendingSignup.objects.update_or_create(
        email=email,
        defaults={
            'password_hash': password_hash,
            'code': code,
            'code_expires_at': code_expires_at,
            'attempts': 0,
        },
    )

    try:
        send_signup_code_email(email, code)
    except SMTPException:
        # PendingSignup row는 남겨두어 사용자가 resend로 복구 가능 (spec 9.4).
        return Response(
            {'detail': '인증 코드 발송에 실패했습니다. 잠시 후 다시 시도해주세요.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(
        {'detail': '인증 코드를 이메일로 발송했습니다. 이메일을 확인해주세요.'},
        status=status.HTTP_201_CREATED,
    )


@extend_schema(
    request=VerifyEmailSerializer,
    responses={200: VerifyEmailResponseSerializer, 400: DetailResponseSerializer},
)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([AnonRateThrottle, VerifyEmailPerEmailThrottle])
def verify_email(request):
    """이메일 인증 (spec 5.1.1).

    PendingSignup의 code·expires를 검증하고 통과 시 트랜잭션 안에서
    User row 생성 + PendingSignup row 삭제 + JWT 발급.
    """
    serializer = VerifyEmailSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data['email'].strip().lower()
    code = serializer.validated_data['code']

    invalid_msg = '인증 코드가 일치하지 않습니다.'
    expired_msg = '인증 코드가 만료되었습니다. 다시 요청해주세요.'

    # 이미 인증 완료된 User가 있으면 enumeration 방지를 위해 동일 오류 반환.
    if User.objects.filter(email__iexact=email).exists():
        return Response({'detail': invalid_msg}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            pending = (
                PendingSignup.objects
                .select_for_update()
                .filter(email=email)
                .first()
            )
            if not pending or pending.code != code:
                # 실패 시도 카운팅 (brute force 보조 방어).
                if pending:
                    pending.attempts = pending.attempts + 1
                    pending.save(update_fields=['attempts'])
                return Response({'detail': invalid_msg}, status=status.HTTP_400_BAD_REQUEST)

            if timezone.now() > pending.code_expires_at:
                return Response({'detail': expired_msg}, status=status.HTTP_400_BAD_REQUEST)

            # password_hash는 이미 make_password 결과이므로 password 컬럼에
            # 직접 주입한다 (set_password를 다시 부르지 않음).
            user = User.objects.create(
                email=pending.email,
                password=pending.password_hash,
                is_email_verified=True,
            )
            pending.delete()
    except IntegrityError:
        # User.email unique 위반: 동시 verify 시도 race. 동일 오류로 응답.
        return Response({'detail': invalid_msg}, status=status.HTTP_400_BAD_REQUEST)

    refresh = RefreshToken.for_user(user)
    return Response({
        'detail': '이메일 인증이 완료되었습니다.',
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    })


@extend_schema(
    request=ResendVerificationSerializer,
    responses={200: DetailResponseSerializer},
)
@api_view(['POST'])
@permission_classes([AllowAny])
def resend_verification(request):
    """회원가입 인증 코드 재발송 (spec 5.1.1).

    PendingSignup의 code·code_expires_at·attempts를 갱신하고 새 메일 발송.
    미가입(PendingSignup 없음)·이미 인증 완료(User 존재)·SMTP 실패 모두 동일
    응답으로 계정 존재 여부 노출 방지.
    """
    serializer = ResendVerificationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data['email'].strip().lower()
    pending = PendingSignup.objects.filter(email=email).first()
    if pending and not User.objects.filter(email__iexact=email).exists():
        pending.code = generate_verification_code()
        pending.code_expires_at = timezone.now() + timedelta(minutes=3)
        pending.attempts = 0
        pending.save(update_fields=['code', 'code_expires_at', 'attempts', 'updated_at'])
        try:
            send_signup_code_email(email, pending.code)
        except SMTPException:
            pass
    return Response({'detail': '인증 코드가 발송되었습니다.'})


@extend_schema(
    request=LoginSerializer,
    responses={
        200: TokenPairResponseSerializer,
        401: DetailResponseSerializer,
        403: DetailResponseSerializer,
    },
)
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


@extend_schema(
    request=KakaoLoginSerializer,
    responses={
        200: KakaoLoginResponseSerializer,
        401: DetailResponseSerializer,
        500: DetailResponseSerializer,
        502: DetailResponseSerializer,
    },
)
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


@extend_schema(
    request=LogoutSerializer,
    responses={200: DetailResponseSerializer, 400: DetailResponseSerializer},
)
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

@extend_schema(
    request=PasswordResetRequestSerializer,
    responses={200: DetailResponseSerializer},
)
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


@extend_schema(
    request=PasswordResetVerifySerializer,
    responses={200: DetailResponseSerializer, 400: DetailResponseSerializer},
)
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


@extend_schema(
    request=PasswordResetConfirmSerializer,
    responses={200: DetailResponseSerializer, 400: DetailResponseSerializer},
)
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

@extend_schema(methods=['GET'], responses={200: ProfileSerializer})
@extend_schema(
    methods=['PUT', 'PATCH'],
    request=ProfileUpdateSerializer,
    responses={200: ProfileSerializer},
)
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


@extend_schema(methods=['GET'], responses={200: SettingsSerializer})
@extend_schema(
    methods=['PATCH'], request=SettingsSerializer,
    responses={200: SettingsSerializer},
)
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


@extend_schema(
    request=WithdrawSerializer,
    responses={200: DetailResponseSerializer, 401: DetailResponseSerializer},
)
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
    # drf-spectacular가 path parameter 타입을 모델에서 추론하도록 클래스 속성 명시.
    # 실제 조회는 아래 get_queryset()이 user 필터를 추가해 처리.
    queryset = InterestArea.objects.all()

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
    queryset = CourseHistory.objects.all()  # drf-spectacular path param 추론용

    def get_queryset(self):
        return CourseHistory.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class CurrentCourseViewSet(viewsets.ModelViewSet):
    serializer_class = CurrentCourseSerializer
    http_method_names = ['get', 'post', 'put', 'delete']
    queryset = CurrentCourse.objects.all()  # drf-spectacular path param 추론용

    def get_queryset(self):
        return CurrentCourse.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ─── 6.11 북마크 ───

from rest_framework.generics import ListCreateAPIView, DestroyAPIView
from rest_framework.exceptions import NotFound

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view

from notices.models import Notice
from information.models import Information


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter(
                'type', OpenApiTypes.STR, OpenApiParameter.QUERY,
                description=(
                    "북마크 종류 (필수). 'notice' 또는 'information'. "
                    "누락·잘못된 값 → 400 (spec 6.11)."
                ),
                required=True,
                enum=['notice', 'information'],
            ),
            OpenApiParameter(
                'source', OpenApiTypes.STR, OpenApiParameter.QUERY,
                description=(
                    "type='notice'일 때만 적용. 공지 출처 필터 "
                    "(academic/general/event/scholarship/...). spec 6.11."
                ),
            ),
            OpenApiParameter(
                'category', OpenApiTypes.STR, OpenApiParameter.QUERY,
                description=(
                    "type='information'일 때만 적용. 정보 카테고리 필터 "
                    "(예: 공모전). spec 6.11."
                ),
            ),
        ],
    ),
)
class BookmarkListCreateView(ListCreateAPIView):
    """GET /api/v1/bookmarks/ - 본인 북마크 목록 (type/source/category 필터)
    POST /api/v1/bookmarks/ - 북마크 추가 (멱등)
    """
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return BookmarkCreateSerializer
        return BookmarkListSerializer

    # ─ GET ─

    def get_queryset(self):
        qs = Bookmark.objects.filter(user=self.request.user)
        params = self.request.query_params

        # GET 시 type 파라미터 필수 (spec 6.11 — type=notice 또는 type=information)
        # POST는 body의 content_type을 쓰므로 이 검증 안 함
        if self.request.method == 'GET':
            type_param = params.get('type')
            if not type_param:
                raise serializers.ValidationError({'type': ['type 파라미터는 필수입니다.']})
            if type_param not in (Bookmark.CONTENT_TYPE_NOTICE, Bookmark.CONTENT_TYPE_INFORMATION):
                raise serializers.ValidationError({'type': [f'잘못된 type 값: {type_param!r}']})
            qs = qs.filter(content_type=type_param)
        else:
            type_param = params.get('type')
            if type_param:
                qs = qs.filter(content_type=type_param)

        # 공지 북마크 + source 필터 (spec 6.11 피그마 디자인)
        if type_param == Bookmark.CONTENT_TYPE_NOTICE:
            source = params.get('source')
            if source:
                notice_ids = Notice.objects.filter(source=source).values_list('id', flat=True)
                qs = qs.filter(object_id__in=list(notice_ids))

        # 정보 북마크 + category 필터
        elif type_param == Bookmark.CONTENT_TYPE_INFORMATION:
            category = params.get('category')
            if category:
                import json
                needle = json.dumps(category, ensure_ascii=True)
                info_ids = Information.objects.filter(
                    categories__icontains=needle
                ).values_list('id', flat=True)
                qs = qs.filter(object_id__in=list(info_ids))

        return qs

    def get_serializer_context(self):
        """N+1 회피용 — 페이지네이션된 북마크의 target 객체들을 한번에 prefetch."""
        ctx = super().get_serializer_context()

        # paginate_queryset이 호출되어야 self.paginator._page가 채워지므로,
        # 여기서 직접 paginated_queryset 만들어서 in_bulk로 prefetch
        page = self.paginator.page if hasattr(self, 'paginator') and getattr(self.paginator, 'page', None) else None
        if page is None:
            return ctx

        bookmarks = list(page.object_list)
        notice_ids = [b.object_id for b in bookmarks if b.content_type == Bookmark.CONTENT_TYPE_NOTICE]
        info_ids = [b.object_id for b in bookmarks if b.content_type == Bookmark.CONTENT_TYPE_INFORMATION]

        ctx['notice_map'] = Notice.objects.select_related('ai_result').in_bulk(notice_ids)
        ctx['info_map'] = Information.objects.in_bulk(info_ids)
        return ctx

    def list(self, request, *args, **kwargs):
        # 페이지네이션을 먼저 수행해서 get_serializer_context의 page를 채움
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    # ─ POST ─

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        content_type = serializer.validated_data['content_type']
        object_id = serializer.validated_data['object_id']

        # 대상 객체 존재 확인 (없으면 404 — spec 6.11 정책)
        if content_type == Bookmark.CONTENT_TYPE_NOTICE:
            exists = Notice.objects.filter(id=object_id).exists()
        elif content_type == Bookmark.CONTENT_TYPE_INFORMATION:
            exists = Information.objects.filter(id=object_id).exists()
        else:
            exists = False

        if not exists:
            raise NotFound(detail='북마크 대상이 존재하지 않습니다.')

        # 멱등 처리 — 이미 있으면 그대로 200 반환
        bookmark, created = Bookmark.objects.get_or_create(
            user=request.user,
            content_type=content_type,
            object_id=object_id,
        )
        response_serializer = BookmarkCreateSerializer(bookmark)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class BookmarkDetailView(DestroyAPIView):
    """DELETE /api/v1/bookmarks/<id>/ - 본인 북마크 삭제.

    본인 소유 아닌 북마크 ID로 시도하면 404 (spec 6.11 enumeration 방어).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BookmarkListSerializer  # DRF 요구사항 (실제로는 안 씀)

    def get_queryset(self):
        # 본인 북마크만 조회 가능 → 다른 사용자 북마크 ID 접근 시 자동 404
        return Bookmark.objects.filter(user=self.request.user)

