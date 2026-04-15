import hashlib

from rest_framework.throttling import SimpleRateThrottle


class _PerEmailThrottle(SimpleRateThrottle):
    """
    request body의 email 값 기준으로 동작하는 throttle의 공통 베이스.
    IP 기준(AnonRateThrottle)은 여러 IP로 우회 가능하므로, 공격 대상인
    email 자체를 키로 사용해 공격면을 제한한다.
    """
    scope = None  # 서브클래스에서 지정

    def get_cache_key(self, request, view):
        email = ''
        if hasattr(request, 'data') and isinstance(request.data, dict):
            email = (request.data.get('email') or '').strip().lower()
        if not email:
            # 이메일이 없으면 throttle 하지 않음 (serializer에서 400으로 걸림)
            return None
        # raw email 대신 sha256 해시 사용:
        # 1) memcached 250자 키 제한 초과 방지 (EmailField는 254자까지 허용)
        # 2) 비-ASCII/공백 등 memcached 키 제약 위반 방지
        # 3) 캐시 백엔드에 이메일 평문이 남는 privacy 이슈 방지
        ident = hashlib.sha256(email.encode('utf-8')).hexdigest()
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident,
        }


class VerifyEmailPerEmailThrottle(_PerEmailThrottle):
    """verify-email 코드 brute force 방어 (동일 이메일 기준)."""
    scope = 'verify_email'


class PasswordResetPerEmailThrottle(_PerEmailThrottle):
    """
    password_reset_verify / password_reset_confirm 코드 brute force 방어.
    anon 30/min은 IP 기반이라 IP 로테이션 시 6자리 코드가 brute force
    가능하므로 이메일 기준 추가 throttle 적용.
    """
    scope = 'password_reset'
