"""drf-spectacular Authentication Extension.

`BlacklistCheckJWTAuthentication` 은 SimpleJWT 를 확장한 커스텀 클래스라
drf-spectacular 의 기본 매처가 인식하지 못해 OpenAPI `securitySchemes` 에
누락된다. 결과적으로 Swagger UI(`/api/docs/`)의 Authorize 버튼이 작동하지 않고
각 인증 endpoint 에 잠금 아이콘이 표시되지 않는다.

여기서 `OpenApiAuthenticationExtension` 을 하나 등록해 "이 클래스는 Bearer JWT"
임을 명시한다. import 만 되면 drf-spectacular 의 metaclass 가 자동 등록.
실제 import 는 `accounts/apps.py` 의 `AccountsConfig.ready()` 에서 수행.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.plumbing import build_bearer_security_scheme_object


class BlacklistCheckJWTAuthScheme(OpenApiAuthenticationExtension):
    """SimpleJWT 호환 Bearer 토큰 schema 노출."""

    target_class = 'accounts.authentication.BlacklistCheckJWTAuthentication'
    name = 'jwtAuth'

    def get_security_definition(self, auto_schema):
        return build_bearer_security_scheme_object(
            header_name='Authorization',
            token_prefix='Bearer',
            bearer_format='JWT',
        )
