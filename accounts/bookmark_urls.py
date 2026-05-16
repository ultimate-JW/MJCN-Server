"""북마크 API URL 라우팅 (spec 6.11).

루트 urls.py에서 `/api/v1/bookmarks/` prefix로 mount되도록 별도 분리.
spec URL이 accounts prefix 밖이라 accounts/urls.py와 분리.
"""
from django.urls import path

from . import views

app_name = 'bookmarks'

urlpatterns = [
    path('', views.BookmarkListCreateView.as_view(), name='bookmark-list-create'),
    path('<int:pk>/', views.BookmarkDetailView.as_view(), name='bookmark-detail'),
]
