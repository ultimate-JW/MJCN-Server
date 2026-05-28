from django.urls import path

from . import views

app_name = 'themes'

urlpatterns = [
    path('', views.ThemeListView.as_view(), name='theme-list'),
    path('<int:pk>/', views.ThemeDetailView.as_view(), name='theme-detail'),
]
