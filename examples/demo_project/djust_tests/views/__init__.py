"""
Test views for djust_tests app
"""

from .cache_test import CacheTestView
from .draft_mode_test import DraftModeTestView
from .loading_test import LoadingTestView
from .test_index import TestIndexView

from .virtual_keyed_demo import VirtualKeyedDemoView  # noqa: F401

__all__ = [
    "VirtualKeyedDemoView",
    "CacheTestView",
    "DraftModeTestView",
    "LoadingTestView",
    "TestIndexView",
]
