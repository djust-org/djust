"""
Test views for djust_tests app
"""

from .cache_test import CacheTestView
from .draft_mode_test import DraftModeTestView
from .loading_test import LoadingTestView
from .test_index import TestIndexView

__all__ = [
    'CacheTestView',
    'DraftModeTestView',
    'LoadingTestView',
    'TestIndexView',
]
