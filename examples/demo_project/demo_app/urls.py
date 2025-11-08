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
    path('performance/', views.PerformanceTestView.as_view(), name='performance-test'),
    path('datatable/', views.ProductDataTableView.as_view(), name='datatable'),

    # Component Library Kitchen Sink
    path('kitchen-sink/', views.KitchenSinkView.as_view(), name='kitchen-sink'),

    # Django Forms Integration
    path('forms/', views.FormsIndexView.as_view(), name='forms-index'),
    path('forms/registration/', views.RegistrationFormView.as_view(), name='forms-registration'),
    path('forms/contact/', views.ContactFormView.as_view(), name='forms-contact'),
    path('forms/profile/', views.ProfileFormView.as_view(), name='forms-profile'),
    path('forms/simple/', views.SimpleContactFormView.as_view(), name='forms-simple'),

    # Auto-rendered forms (different CSS frameworks)
    path('forms/auto/', views.AutoContactFormView.as_view(), name='forms-auto'),
    path('forms/auto/compare/', views.AutoFormComparisonView.as_view(), name='forms-auto-compare'),
    path('forms/auto/tailwind/', views.AutoContactFormTailwindView.as_view(), name='forms-auto-tailwind'),
    path('forms/auto/plain/', views.AutoContactFormPlainView.as_view(), name='forms-auto-plain'),
]
