"""
URL configuration for djust_homepage app.
"""

from django.urls import path
from . import views

app_name = 'homepage'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),

    # Homepage Embedded Demos
    path('home-demos/counter/', views.HomeCounterDemo.as_view(), name='home-counter'),
    path('home-demos/search/', views.HomeSearchDemo.as_view(), name='home-search'),
    path('home-demos/data/', views.HomeLiveDataDemo.as_view(), name='home-data'),
    path('home-demos/todo/', views.HomeTodoDemo.as_view(), name='home-todo'),
    path('home-demos/navbar-badge/', views.NavbarBadgeDemo.as_view(), name='home-navbar-badge'),
]
