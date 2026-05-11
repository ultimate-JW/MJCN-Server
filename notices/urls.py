from django.urls import path

from . import views

app_name = 'notices'

urlpatterns = [
    path('', views.NoticeListView.as_view(), name='notice-list'),
    path('<int:pk>/', views.NoticeDetailView.as_view(), name='notice-detail'),
]
