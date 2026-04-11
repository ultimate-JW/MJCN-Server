from rest_framework.permissions import BasePermission


class IsEmailVerified(BasePermission):
    message = '이메일 인증을 완료해주세요.'

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_email_verified
