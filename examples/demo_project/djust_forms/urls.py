"""
URL configuration for djust_forms app.
"""

from django.urls import path
from . import views

app_name = 'forms'

urlpatterns = [
    # Forms Index
    path('', views.FormsIndexView.as_view(), name='index'),

    # Form Examples
    path('registration/', views.RegistrationFormView.as_view(), name='registration'),
    path('contact/', views.ContactFormView.as_view(), name='contact'),
    path('profile/', views.ProfileFormView.as_view(), name='profile'),
    path('simple/', views.SimpleContactFormView.as_view(), name='simple'),

    # Auto-rendered forms (different CSS frameworks)
    path('auto/', views.AutoContactFormView.as_view(), name='auto'),
    path('auto/compare/', views.AutoFormComparisonView.as_view(), name='auto-compare'),
    path('auto/tailwind/', views.AutoContactFormTailwindView.as_view(), name='auto-tailwind'),
    path('auto/plain/', views.AutoContactFormPlainView.as_view(), name='auto-plain'),
]
