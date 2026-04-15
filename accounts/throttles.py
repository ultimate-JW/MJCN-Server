from rest_framework.throttling import SimpleRateThrottle


class VerifyEmailPerEmailThrottle(SimpleRateThrottle):
    """
    verify-email 요청을 request body의 email 값 기준으로 throttle.
    동일 이메일에 대한 인증 코드 brute force 공격을 방어한다.
    IP 기준(AnonRateThrottle)은 여러 IP로 우회 가능하므로, 공격 대상인
    email 자체를 키로 사용해 공격면을 제한한다.
    """
    scope = 'verify_email'

    def get_cache_key(self, request, view):
        email = ''
        if hasattr(request, 'data') and isinstance(request.data, dict):
            email = (request.data.get('email') or '').strip().lower()
        if not email:
            # 이메일이 없으면 throttle 하지 않음 (serializer에서 400으로 걸림)
            return None
        return self.cache_format % {
            'scope': self.scope,
            'ident': email,
        }
