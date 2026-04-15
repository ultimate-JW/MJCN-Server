import time

from django.core.cache import cache
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

# access token의 jti를 남은 만료 시간 동안 캐시에 넣어두고, 요청마다
# 해당 jti가 블랙리스트에 있는지 확인한다. refresh token만 블랙리스트
# 가능하던 SimpleJWT 기본 동작을 보완하여, logout 시 사용하던 access
# token도 즉시 무효화할 수 있게 한다.
#
# 주의: Django 기본(local-memory) 캐시는 프로세스별로 분리되어 있어
# 멀티 워커 환경에서는 동작하지 않는다. 운영 환경에서는 반드시 Redis
# 등 공유 캐시로 CACHES 설정을 교체해야 한다.

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
