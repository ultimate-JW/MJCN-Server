from django.urls import path

from . import views

app_name = 'information'

urlpatterns = [
    path('', views.InformationListView.as_view(), name='information-list'),
    path('contest-guide/', views.ContestGuideView.as_view(), name='contest-guide'),
    path('<int:pk>/', views.InformationDetailView.as_view(), name='information-detail'),
]
