"""Tests for ThemeManager request-level caching (I3)."""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "djust.theming"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    )
    django.setup()

from django.test import RequestFactory, TestCase

from djust.theming.manager import ThemeManager, get_theme_manager
import pytest

pytestmark = pytest.mark.theming


class TestGetThemeManager(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_returns_theme_manager_instance(self):
        request = self.factory.get("/")
        manager = get_theme_manager(request)
        assert isinstance(manager, ThemeManager)

    def test_caches_on_request(self):
        request = self.factory.get("/")
        first = get_theme_manager(request)
        second = get_theme_manager(request)
        assert first is second

    def test_different_requests_get_different_managers(self):
        req1 = self.factory.get("/")
        req2 = self.factory.get("/")
        assert get_theme_manager(req1) is not get_theme_manager(req2)

    def test_none_request_returns_fresh_instance(self):
        m1 = get_theme_manager(None)
        m2 = get_theme_manager(None)
        assert isinstance(m1, ThemeManager)
        assert m1 is not m2
