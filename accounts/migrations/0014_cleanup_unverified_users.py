"""미인증 dead User 정리 (spec 5.1.1 정책 변경에 따라).

새 정책에서는 이메일 인증 통과 시점에만 User row가 생성된다. 이전 정책에서
signup만 하고 인증 안 한 채로 남아 있는 dead row(`is_email_verified=False`)
를 일괄 삭제. 로그인은 이미 차단된 상태(views.login_view:171-172)라
기능 손실 없음. kakao_id가 있는 카카오 계정은 안전망으로 제외 (실제 0건일
가능성이 높지만 정책 차등을 코드로도 보장).
"""

from django.db import migrations


def cleanup_unverified_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(is_email_verified=False, kakao_id__isnull=True).delete()


def noop_reverse(apps, schema_editor):
    # 삭제된 dead row는 복구할 수 없으므로 reverse는 no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_pendingsignup_alter_emailverification_purpose'),
    ]

    operations = [
        migrations.RunPython(cleanup_unverified_users, noop_reverse),
    ]
