"""
URL configuration for djust_tests app.
"""

from django.urls import path
from .views import *

app_name = 'tests'

urlpatterns = [
    # Tests Index
    path('', TestIndexView.as_view(), name='index'),

    # Individual Tests
    path('cache/', CacheTestView.as_view(), name='cache'),
    path('draft-mode/', DraftModeTestView.as_view(), name='draft-mode'),
    path('loading/', LoadingTestView.as_view(), name='loading'),
]
