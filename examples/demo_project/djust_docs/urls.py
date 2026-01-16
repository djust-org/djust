"""
URL configuration for djust_docs app.
"""

from django.urls import path
from . import views

app_name = 'docs'

urlpatterns = [
    # Documentation
    path('', views.DocsView.as_view(), name='index'),
    path('components/', views.ComponentsGuideView.as_view(), name='components-guide'),
]
