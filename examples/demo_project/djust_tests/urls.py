"""
URL configuration for djust_tests app.
"""

from django.urls import path
from .views import *
from .views import virtual_keyed_demo

app_name = 'tests'

urlpatterns = [
    # Tests Index
    path('', TestIndexView.as_view(), name='index'),

    # Individual Tests
    path('cache/', CacheTestView.as_view(), name='cache'),
    path('draft-mode/', DraftModeTestView.as_view(), name='draft-mode'),
    path('loading/', LoadingTestView.as_view(), name='loading'),

    # #2017 / ADR-026 iteration-3 browser gate
    path('virtual-keyed/', virtual_keyed_demo.VirtualKeyedDemoView.as_view(), name='virtual-keyed'),
]
