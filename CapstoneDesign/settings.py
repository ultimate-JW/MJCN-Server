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
    'notices',
    'information',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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

# Database

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

STATIC_URL = 'static/'
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

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
