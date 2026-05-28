from rest_framework import serializers


class ASCIIEmailField(serializers.EmailField):
    # DRF/Django EmailField는 internationalized email (RFC 6531)을 기본 허용.
    # spec.md 5.1.1 / 5.1.5 — ASCII 이메일만 허용하므로 한글·유니코드 local-part 차단.
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            value.encode('ascii')
        except UnicodeEncodeError:
            raise serializers.ValidationError('올바른 이메일 형식이 아닙니다.')
        return value
