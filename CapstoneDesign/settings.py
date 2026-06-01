from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
import os

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-change-me')

DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1')

ALLOWED_HOSTS = ['*']

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'drf_spectacular',
    # Local
    'accounts',
    'common',
    'courses',
    'dashboard',
    'notifications',
    'notices',
    'information',
    'chat',
    'timetables',
    'themes',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise: /static/* 요청을 Django가 직접 서빙. SecurityMiddleware 바로 뒤가 권장 위치.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'CapstoneDesign.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'CapstoneDesign.wsgi.application'

# Database (spec 8.5)
#
# DB_ENGINE 환경변수가 'postgresql' 일 때만 PG로 전환. 미설정 시 SQLite 폴백 —
# 로컬·CI는 설정·도커 없이 즉시 실행 가능. 운영은 .env에 DB_ENGINE=postgresql +
# DB_NAME / DB_USER / DB_PASSWORD / DB_HOST / DB_PORT (선택: DB_CONN_MAX_AGE) 설정.

if os.getenv('DB_ENGINE', '').lower() == 'postgresql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME', 'mjcn'),
            'USER': os.getenv('DB_USER', 'mjcn'),
            'PASSWORD': os.getenv('DB_PASSWORD', ''),
            'HOST': os.getenv('DB_HOST', 'localhost'),
            'PORT': os.getenv('DB_PORT', '5432'),
            # 연결 재사용 (cron + 멀티워커 환경에서 connect 오버헤드 감소).
            'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Custom User Model

AUTH_USER_MODEL = 'accounts.User'

# Internationalization

LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_I18N = True
USE_TZ = True

# Static / Media files
#
# 운영(DEBUG=False)에서는 collectstatic 산출물(STATIC_ROOT)을 WhiteNoise가 서빙한다.
# 배포 시 한 번: `python manage.py collectstatic --noinput`

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Storage 분기 (chat 첨부 등 사용자 업로드 미디어).
#
# USE_S3=True (운영): django-storages의 S3Boto3Storage 사용. AWS 자격증명·버킷명
# 환경변수 필수. 버킷은 private이고 django-storages가 응답 시점에 presigned URL
# 생성 (AWS_QUERYSTRING_AUTH=True 기본).
#
# USE_S3=False (로컬 default): FileSystemStorage + MEDIA_ROOT. AWS 자격증명
# 없이도 개발 가능. PR 1~3 동작 그대로.
USE_S3 = os.getenv('USE_S3', 'False').lower() in ('true', '1')

if USE_S3:
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME', '')
    AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'ap-northeast-2')
    AWS_S3_FILE_OVERWRITE = False  # 동명 파일 자동 rename (Django default와 일치)
    AWS_DEFAULT_ACL = None  # 버킷 정책 기준 (ACL 명시 제거 — 최신 S3 기본)
    STORAGES = {
        'default': {'BACKEND': 'storages.backends.s3.S3Storage'},
        'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
    }
else:
    STORAGES = {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# DRF

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'accounts.authentication.BlacklistCheckJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'common.pagination.StandardPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '30/minute',
        'user': '60/minute',
        # 인증 코드 brute force 방어: 동일 이메일 기준 분당 5회
        'verify_email': '5/minute',
        # 비밀번호 재설정 코드 brute force 방어: 동일 이메일 기준 분당 5회
        'password_reset': '5/minute',
    },
}

# 테스트 환경에서는 DRF throttle 비활성화.
# LocMemCache는 테스트 클래스 간에 카운터가 누적되어 무관한 테스트도 429로 떨어짐.
# 운영 throttle은 그대로 유지되므로 보안 영향 없음.
#
# 단, view에 @throttle_classes로 명시된 throttle은 default와 별개로 살아
# 있으므로 rate scope들도 None으로 명시해야 SimpleRateThrottle이
# ImproperlyConfigured 없이 동작 (rate=None → allow_request 항상 True).
import sys
if 'test' in sys.argv or 'pytest' in sys.argv[0]:
    REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] = []
    REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] = {
        'anon': None,
        'user': None,
        'verify_email': None,
        'password_reset': None,
    }

# SimpleJWT

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# Cache
#
# ★ 운영 배포 전 필독 ★
# CACHES 설정이 없으면 Django는 LocMemCache(프로세스 로컬 메모리)를
# 기본값으로 쓴다. 이 경우 멀티 워커(gunicorn -w N 등) 환경에서
# accounts.authentication의 access token 블랙리스트가 워커 간 공유되지
# 않아 로그아웃이 무효화된다(자세한 설명은 accounts/authentication.py
# 상단 주석 참고).
#
# 운영 환경에서는 반드시 아래 중 하나로 교체할 것:
#
# [권장] Redis (pip install redis 필요)
# CACHES = {
#     'default': {
#         'BACKEND': 'django.core.cache.backends.redis.RedisCache',
#         'LOCATION': os.environ['REDIS_URL'],  # redis://host:6379/1
#     }
# }
#
# [차선] DatabaseCache (Redis 인프라가 없을 때, createcachetable 필요)
# CACHES = {
#     'default': {
#         'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
#         'LOCATION': 'django_cache',
#     }
# }

# CORS

CORS_ALLOW_ALL_ORIGINS = DEBUG

# drf-spectacular

SPECTACULAR_SETTINGS = {
    'TITLE': 'MJCN API',
    'DESCRIPTION': '명지대학교 학생 AI 비서 서비스 API',
    'VERSION': '1.0.0',
    # 여러 모델에 'category' 필드가 choices와 함께 정의되어 있어 enum 이름이
    # 자동 'CategoryA77Enum' 같이 충돌 해결됨. 명시적으로 의미 있는 이름 부여.
    'ENUM_NAME_OVERRIDES': {
        'ChatRoomCategoryEnum': 'chat.models.CHAT_CATEGORIES',
        'ThemeCategoryEnum': 'themes.models.Theme.CATEGORY_CHOICES',
        # PR #129 머지로 CourseHistory.category가 Course.CATEGORY_CHOICES를 공유 →
        # 두 모델이 같은 component 'CourseCategoryEnum' 가리키도록 명시 (#133).
        'CourseCategoryEnum': 'courses.models.Course.CATEGORY_CHOICES',
    },
}

# OpenAI (공지사항 AI 처리 파이프라인 — spec 9.1)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
# 단계별 본문 truncate 한도 (문자 수 기준 — 한국어는 토큰 ≈ 1.5~2자/토큰).
# gpt-4o-mini context window는 충분히 크지만 비용/응답속도 위해 자름.
OPENAI_NOTICE_CONTENT_MAX_CHARS = int(os.getenv('OPENAI_NOTICE_CONTENT_MAX_CHARS', '4000'))
OPENAI_REQUEST_TIMEOUT = int(os.getenv('OPENAI_REQUEST_TIMEOUT', '30'))
OPENAI_MAX_RETRIES = int(os.getenv('OPENAI_MAX_RETRIES', '3'))
# VLM 전처리(spec 9.1.6): 한 공지당 VLM에 보낼 이미지 최대 장수
OPENAI_VLM_MAX_IMAGES = int(os.getenv('OPENAI_VLM_MAX_IMAGES', '5'))
# VLM TPM rate limit pacing (spec 9.1.5): 대량 백필 시 OpenAI TPM 한도 초과 완화.
# - REQUEST_INTERVAL: VLM 호출 간 대기(초). 기본 0 → 일일 cron은 건수가 적어 영향 없음.
#   백필 시 환경변수로 1~3초 등 설정해 호출 속도를 늦춤.
# - RATE_LIMIT_RETRIES: 429 발생 시 같은 공지를 백오프 후 재시도하는 최대 횟수.
# - RATE_LIMIT_BACKOFF: 백오프 기준 시간(초). 실제 대기 = base * 2**(n-1), 최대 60초.
OPENAI_VLM_REQUEST_INTERVAL = float(os.getenv('OPENAI_VLM_REQUEST_INTERVAL', '0'))
OPENAI_VLM_RATE_LIMIT_RETRIES = int(os.getenv('OPENAI_VLM_RATE_LIMIT_RETRIES', '5'))
OPENAI_VLM_RATE_LIMIT_BACKOFF = float(os.getenv('OPENAI_VLM_RATE_LIMIT_BACKOFF', '20'))
# chat 컨텍스트 윈도우 (spec 5.2): 멀티턴 대화에 포함할 최근 메시지 개수
# 토큰 비용·응답 품질의 균형. settings로 환경별 조절 가능.
OPENAI_CHAT_CONTEXT_MESSAGES = int(os.getenv('OPENAI_CHAT_CONTEXT_MESSAGES', '10'))

# 카카오 OAuth (spec 5.1.3 / 6.1)
#
# REST API 키와 Redirect URI는 카카오 개발자 콘솔(developers.kakao.com)에서 발급/등록.
# REDIRECT_URI는 콘솔 등록값과 정확히 일치해야 함 (trailing slash·http/https 포함).

KAKAO_REST_API_KEY = os.getenv('KAKAO_REST_API_KEY', '')
KAKAO_CLIENT_SECRET = os.getenv('KAKAO_CLIENT_SECRET', '')
KAKAO_REDIRECT_URI = os.getenv('KAKAO_REDIRECT_URI', '')
KAKAO_REQUEST_TIMEOUT = int(os.getenv('KAKAO_REQUEST_TIMEOUT', '10'))


# Email

EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
# SMTP 자격증명이 없으면 콘솔 백엔드로 자동 전환 (로컬 테스트용)
EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND',
    'django.core.mail.backends.smtp.EmailBackend'
    if EMAIL_HOST_USER else 'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER or 'noreply@mjcn.local'


# Firebase / FCM (spec 9.3)
#
# 서비스 계정 JSON 키 파일의 경로. Firebase 콘솔 > 프로젝트 설정 > 서비스 계정에서 발급.
# 미설정 시 send_pending_pushes 명령은 graceful no-op — 로컬 개발·테스트는
# 자격증명 없이 동작한다 (이메일 콘솔 백엔드 폴백과 동일 철학).
# 키 파일명은 .gitignore에 등재된 firebase-credentials.json 사용 권장.

FIREBASE_CREDENTIALS_PATH = os.getenv('FIREBASE_CREDENTIALS_PATH', '')
