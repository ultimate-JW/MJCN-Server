import time

from django.core.cache import cache
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

# access token의 jti를 남은 만료 시간 동안 캐시에 넣어두고, 요청마다
# 해당 jti가 블랙리스트에 있는지 확인한다. refresh token만 블랙리스트
# 가능하던 SimpleJWT 기본 동작을 보완하여, logout 시 사용하던 access
# token도 즉시 무효화할 수 있게 한다.
#
# ─────────────────────────────────────────────────────────────────────
# 운영 배포 시 필독: 캐시 백엔드 교체 필요
# ─────────────────────────────────────────────────────────────────────
# 이 블랙리스트는 Django 캐시 API(cache.set/get)에 의존한다.
#
# 현재 settings.py에 CACHES 설정이 없어 Django는 기본값인
# LocMemCache(django.core.cache.backends.locmem.LocMemCache)를 사용
# 하는데, 이 백엔드는 **워커 프로세스별로 독립된 메모리**에 데이터를
# 저장한다. 즉 gunicorn -w 4 처럼 멀티 워커로 운영하면:
#
#   1. worker #1이 logout 요청을 받아 jti=abc를 자기 메모리에 저장
#   2. 같은 access token으로 다른 API 요청이 worker #2로 라우팅
#   3. worker #2는 jti=abc를 모르므로 요청을 통과시킴 → 로그아웃 무효
#
# → 운영 환경에서는 모든 워커가 공유하는 캐시 백엔드로 교체해야 한다.
#
# 권장: Redis (Django 4.0+ 내장 지원, TTL 네이티브, 재사용성 높음)
#
#   # requirements: pip install redis
#   CACHES = {
#       'default': {
#           'BACKEND': 'django.core.cache.backends.redis.RedisCache',
#           'LOCATION': os.environ['REDIS_URL'],  # redis://host:6379/1
#       }
#   }
#
# 차선책: DatabaseCache (Redis 인프라가 없을 때)
#
#   CACHES = {
#       'default': {
#           'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
#           'LOCATION': 'django_cache',
#       }
#   }
#   # 최초 1회: python manage.py createcachetable
#
# 로컬 개발(단일 프로세스 runserver)에서는 LocMemCache로도 정상 동작.
# ─────────────────────────────────────────────────────────────────────

_ACCESS_BLACKLIST_KEY = 'access_jti_blacklist:{jti}'


def blacklist_access_jti(jti, ttl_seconds):
    if not jti or ttl_seconds <= 0:
        return
    cache.set(_ACCESS_BLACKLIST_KEY.format(jti=jti), 1, timeout=ttl_seconds)


def is_access_jti_blacklisted(jti):
    if not jti:
        return False
    return cache.get(_ACCESS_BLACKLIST_KEY.format(jti=jti)) is not None


class BlacklistCheckJWTAuthentication(JWTAuthentication):
    def get_validated_token(self, raw_token):
        validated = super().get_validated_token(raw_token)
        if is_access_jti_blacklisted(validated.get('jti')):
            raise InvalidToken('Token has been blacklisted.')
        return validated


def blacklist_current_access_token(request):
    """현재 요청의 access token을 남은 TTL 동안 블랙리스트에 등록."""
    token = getattr(request, 'auth', None)
    if token is None:
        return
    jti = token.get('jti')
    exp = token.get('exp')
    if exp is None:
        return
    ttl = max(int(exp) - int(time.time()), 0)
    blacklist_access_jti(jti, ttl)
