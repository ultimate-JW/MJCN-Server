from django.urls import path

from . import views

app_name = 'courses'

urlpatterns = [
    path('', views.CourseSearchView.as_view(), name='course-search'),
    path('status/', views.CompletionStatusView.as_view(), name='completion-status'),
    path('recommend/next/', views.NextSemesterRecommendView.as_view(), name='recommend-next'),
    path('recommend/curriculum/', views.CurriculumRecommendView.as_view(), name='recommend-curriculum'),
]
