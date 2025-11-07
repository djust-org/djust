"""
URL configuration for demo app.
"""

from django.urls import path
from . import views

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('counter/', views.CounterView.as_view(), name='counter'),
    path('todo/', views.TodoView.as_view(), name='todo'),
    path('chat/', views.ChatView.as_view(), name='chat'),
    path('react/', views.ReactDemoView.as_view(), name='react-demo'),
]
