"""
Offline/PWA support for djust LiveViews.

Provides progressive web app features including:
- OfflineMixin for offline-capable views
- Service worker generation
- Event queueing and background sync
- IndexedDB state persistence
"""

from .mixin import OfflineMixin

__all__ = ["OfflineMixin"]
