from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register('interests', views.InterestAreaViewSet, basename='interest')
router.register('course-history', views.CourseHistoryViewSet, basename='course-history')
router.register('current-courses', views.CurrentCourseViewSet, basename='current-course')

urlpatterns = [
    # 6.1 인증
    path('signup/', views.signup, name='signup'),
    path('verify-email/', views.verify_email, name='verify-email'),
    path('verify-email/resend/', views.resend_verification, name='resend-verification'),
    path('login/', views.login_view, name='login'),
    path('login/kakao/', views.kakao_login, name='kakao-login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('logout/', views.logout_view, name='logout'),
    # 비밀번호 재설정
    path('password/reset/', views.password_reset_request, name='password-reset'),
    path('password/reset/verify/', views.password_reset_verify, name='password-reset-verify'),
    path('password/reset/confirm/', views.password_reset_confirm, name='password-reset-confirm'),
    # 6.2 프로필 / 설정
    path('profile/', views.profile, name='profile'),
    path('settings/', views.settings_view, name='settings'),
    path('withdraw/', views.withdraw, name='withdraw'),
    # 6.3, 6.4 ViewSet 라우터
    path('', include(router.urls)),
]
