from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        # drf-spectacular OpenAPI 인증 확장 등록 (BlacklistCheckJWTAuthentication
        # 을 Bearer JWT 로 노출 → Swagger UI Authorize 버튼 작동).
        # import 자체가 metaclass 기반 자동 등록을 트리거하므로 본문 코드 불필요.
        from . import schema  # noqa: F401
