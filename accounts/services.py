import random
import string
from datetime import timedelta

from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

from .models import EmailVerification


def generate_verification_code():
    return ''.join(random.choices(string.digits, k=6))


def send_verification_email(user, purpose='signup'):
    code = generate_verification_code()
    expires_at = timezone.now() + timedelta(minutes=3)

    EmailVerification.objects.create(
        user=user,
        code=code,
        purpose=purpose,
        expires_at=expires_at,
    )

    subject_map = {
        'signup': '[MJCN] 이메일 인증 코드',
        'password_reset': '[MJCN] 비밀번호 재설정 인증 코드',
    }

    send_mail(
        subject=subject_map.get(purpose, '[MJCN] 인증 코드'),
        message=f'인증 코드: {code}\n\n3분 이내에 입력해주세요.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )

    return code


def verify_code(user, code, purpose='signup'):
    verification = EmailVerification.objects.filter(
        user=user,
        code=code,
        purpose=purpose,
        is_used=False,
    ).order_by('-created_at').first()

    if not verification:
        return None, '인증 코드가 일치하지 않습니다.'

    if timezone.now() > verification.expires_at:
        return None, '인증 코드가 만료되었습니다. 다시 요청해주세요.'

    verification.is_used = True
    verification.save()
    return verification, None
