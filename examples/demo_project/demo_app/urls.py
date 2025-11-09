"""
URL configuration for demo app.
"""

from django.urls import path
from . import views

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),

    # Live Demos
    path('demos/', views.DemosIndexView.as_view(), name='demos-index'),
    path('demos/counter/', views.CounterView.as_view(), name='demos-counter'),
    path('demos/todo/', views.TodoView.as_view(), name='demos-todo'),
    path('demos/chat/', views.ChatView.as_view(), name='demos-chat'),
    path('demos/react/', views.ReactDemoView.as_view(), name='demos-react'),
    path('demos/performance/', views.PerformanceTestView.as_view(), name='demos-performance'),
    path('demos/datatable/', views.ProductDataTableView.as_view(), name='demos-datatable'),

    # Component Library Kitchen Sink
    path('kitchen-sink/', views.KitchenSinkView.as_view(), name='kitchen-sink'),

    # Documentation
    path('docs/', views.DocsView.as_view(), name='docs'),

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
