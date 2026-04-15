import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import OperationalError, transaction
from django.utils import timezone

from .models import EmailVerification


def generate_verification_code():
    # 인증 코드는 암호학적으로 안전한 PRNG로 생성해야 함
    # (random 모듈은 예측 가능하여 인증/보안 용도에 부적합)
    return ''.join(str(secrets.randbelow(10)) for _ in range(6))


def send_verification_email(user, purpose='signup'):
    # DB 변경(기존 코드 무효화 + 새 코드 생성)을 하나의 트랜잭션으로 묶어
    # 중간 실패 시 "기존 코드는 무효화됐는데 새 코드는 없는" 상태 방지.
    # send_mail은 트랜잭션 밖에서 호출 — 커밋된 후 발송해야 메일 발송 실패 시
    # DB 롤백으로 기존 유효 코드를 되돌릴 수 있고, 발송만 성공하고 DB 롤백되는
    # 역전 케이스도 방지.
    with transaction.atomic():
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
