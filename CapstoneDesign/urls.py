from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path('admin/', admin.site.urls),
    # API
    path('api/v1/accounts/', include('accounts.urls')),
    path('api/v1/bookmarks/', include('accounts.bookmark_urls')),
    path('api/v1/courses/', include('courses.urls')),
    path('api/v1/dashboard/', include('dashboard.urls')),
    path('api/v1/notices/', include('notices.urls')),
    path('api/v1/information/', include('information.urls')),
    path('api/v1/notifications/', include('notifications.urls')),
    path('api/v1/chat/', include('chat.urls')),
    # API 문서
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
