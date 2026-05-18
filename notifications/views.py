"""알림 API 뷰 (spec 6.9).

- GET /api/v1/notifications/ — 본인 알림 목록
- GET /api/v1/notifications/unread-count/ — 읽지 않은 알림 수
- PATCH /api/v1/notifications/<id>/ — 읽음 처리
- POST /api/v1/notifications/read-all/ — 전체 읽음 처리
- POST /api/v1/notifications/devices/ — FCM 토큰 등록·갱신 (멱등)
- DELETE /api/v1/notifications/devices/ — FCM 토큰 삭제
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.generics import ListAPIView, UpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import FCMDevice, Notification
from .serializers import FCMDeviceRegisterSerializer, FCMDeviceSerializer, NotificationSerializer


# ─── 알림 ───

class NotificationListView(ListAPIView):
    """GET /api/v1/notifications/ — 본인 알림만, 최신순."""
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)


class NotificationDetailView(UpdateAPIView):
    """PATCH /api/v1/notifications/<id>/ — 읽음 처리.

    본인 알림만 (다른 사용자 알림 ID 접근 시 자동 404 — enumeration 방어).
    수정 가능한 필드는 is_read 뿐 (Serializer read_only_fields로 제한).
    """
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['patch', 'head', 'options']

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def unread_count(request):
    """GET /api/v1/notifications/unread-count/ — 읽지 않은 알림 수."""
    count = Notification.objects.filter(user=request.user, is_read=False).count()
    return Response({'unread_count': count})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def read_all(request):
    """POST /api/v1/notifications/read-all/ — 본인 미읽음 알림 전체 읽음 처리."""
    updated = Notification.objects.filter(
        user=request.user, is_read=False,
    ).update(is_read=True)
    return Response({'updated': updated})


# ─── FCM 디바이스 ───

@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def fcm_device(request):
    """POST /api/v1/notifications/devices/ — 토큰 등록·갱신 (멱등).
    DELETE /api/v1/notifications/devices/ — 본인 토큰 삭제 (로그아웃 시).
    """
    if request.method == 'POST':
        return _register_device(request)
    return _delete_device(request)


def _register_device(request):
    serializer = FCMDeviceRegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    token = serializer.validated_data['registration_token']

    # 같은 토큰이 이미 등록되어 있으면 갱신 (디바이스 양도 시 사용자 매핑 변경)
    existing = FCMDevice.objects.filter(registration_token=token).first()
    if existing:
        if existing.user_id != request.user.id:
            existing.user = request.user  # 디바이스 양도
        existing.is_active = True
        existing.save(update_fields=['user', 'is_active', 'updated_at'])
        return Response(
            FCMDeviceSerializer(existing).data,
            status=status.HTTP_200_OK,
        )

    device = FCMDevice.objects.create(
        user=request.user,
        registration_token=token,
    )
    return Response(
        FCMDeviceSerializer(device).data,
        status=status.HTTP_201_CREATED,
    )


def _delete_device(request):
    # 요청 body에 토큰을 받아 본인 디바이스 토큰만 삭제
    token = request.data.get('registration_token')
    if not token:
        return Response(
            {'registration_token': ['필수 필드입니다.']},
            status=status.HTTP_400_BAD_REQUEST,
        )

    deleted, _ = FCMDevice.objects.filter(
        user=request.user, registration_token=token,
    ).delete()
    if deleted == 0:
        # 본인 토큰이 아니거나 없으면 404 (enumeration 방어)
        return Response(
            {'detail': '등록된 디바이스가 없습니다.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(status=status.HTTP_204_NO_CONTENT)
