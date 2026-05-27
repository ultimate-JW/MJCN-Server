from django.urls import path

from .views import TimetableRecommendView, UserPreferenceView


urlpatterns = [
    path('recommend/', TimetableRecommendView.as_view(), name='timetable-recommend'),
    path('preference/', UserPreferenceView.as_view(), name='timetable-preference'),
]
