"""알림 API URL 라우팅 (spec 6.9)."""
from django.urls import path

from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.NotificationListView.as_view(), name='notification-list'),
    path('unread-count/', views.unread_count, name='unread-count'),
    path('read-all/', views.read_all, name='read-all'),
    path('devices/', views.fcm_device, name='fcm-device'),
    path('<int:pk>/', views.NotificationDetailView.as_view(), name='notification-detail'),
]
