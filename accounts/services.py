import random
import string
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import OperationalError, transaction
from django.utils import timezone

from .models import EmailVerification


def generate_verification_code():
    return ''.join(random.choices(string.digits, k=6))


def send_verification_email(user, purpose='signup'):
    # 새 코드 발급 전에 동일 purpose의 기존 미사용 코드를 전부 무효화.
    # 과거 유출/공유된 코드가 만료 전까지 live 상태로 남는 문제 방지.
    EmailVerification.objects.filter(
        user=user,
        purpose=purpose,
        is_used=False,
    ).update(is_used=True)

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
    # 동시성 방어: 같은 코드로 동시에 두 요청이 들어올 때
    # is_used 체크와 업데이트 사이의 race condition 방지
    try:
        with transaction.atomic():
            verification = (
                EmailVerification.objects
                .select_for_update()
                .filter(
                    user=user,
                    code=code,
                    purpose=purpose,
                    is_used=False,
                )
                .order_by('-created_at')
                .first()
            )

            if not verification:
                return None, '인증 코드가 일치하지 않습니다.'

            if timezone.now() > verification.expires_at:
                return None, '인증 코드가 만료되었습니다. 다시 요청해주세요.'

            verification.is_used = True
            verification.save(update_fields=['is_used'])
            return verification, None
    except OperationalError:
        # SQLite의 경우 row-level lock 미지원으로 database lock 예외 발생 가능
        # 이 경우 다른 요청이 이미 처리 중이라는 뜻이므로 동일한 일반 오류로 응답
        return None, '인증 코드가 일치하지 않습니다.'
