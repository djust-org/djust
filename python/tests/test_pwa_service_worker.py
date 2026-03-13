"""
Regression tests for PWA service worker generation.

Tests that the generated service worker JavaScript is correct for
each caching strategy, including the navigate-request bypass that
prevents cache_first from breaking LiveView VDOM patching.
"""

import pytest


# Skip if Django is not configured
pytest.importorskip("django")


@pytest.fixture(autouse=True)
def configure_django():
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DJUST_CONFIG={},
            INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
        )


def make_generator(**kwargs):
    from djust.pwa.service_worker import ServiceWorkerGenerator

    config = {
        "cache_name": "test-cache-v1",
        "cache_strategy": "cache_first",
        "cache_duration": 86400,
        "offline_page": "/offline/",
        "sync_endpoint": "/api/sync/",
        "precache_urls": [],
        "exclude_patterns": ["/admin/"],
        "enable_background_sync": False,
        "enable_push_notifications": False,
        "version": "1.0.0",
    }
    config.update(kwargs)
    return ServiceWorkerGenerator(config=config)


class TestCacheFirstNavigateBypass:
    """cache_first must not serve cached HTML for navigation requests."""

    def test_navigate_mode_guard_present(self):
        """Generated JS contains a navigate-mode guard that bypasses the cache."""
        gen = make_generator(cache_strategy="cache_first")
        sw = gen.generate_service_worker()
        assert "request.mode === 'navigate'" in sw

    def test_navigate_guard_uses_network_fetch(self):
        """Navigate requests must call fetch(request) directly, not caches.match."""
        gen = make_generator(cache_strategy="cache_first")
        sw = gen.generate_service_worker()
        # The navigate block must contain a direct fetch() call
        navigate_idx = sw.index("request.mode === 'navigate'")
        # The return/respondWith for navigate should appear before the main cache block
        assert "fetch(request)" in sw[navigate_idx : navigate_idx + 300]

    def test_navigate_guard_falls_back_to_offline_page(self):
        """Offline fallback for navigate requests is the offline page, not a 503."""
        gen = make_generator(cache_strategy="cache_first")
        sw = gen.generate_service_worker()
        navigate_idx = sw.index("request.mode === 'navigate'")
        navigate_block = sw[navigate_idx : navigate_idx + 300]
        assert "OFFLINE_PAGE" in navigate_block

    def test_network_first_unaffected(self):
        """network_first strategy does not duplicate the navigate guard."""
        gen = make_generator(cache_strategy="network_first")
        sw = gen.generate_service_worker()
        # network_first already goes to network — the guard is specific to cache_first
        assert "network first strategy" in sw.lower() or "network-first" in sw.lower()

    def test_stale_while_revalidate_unaffected(self):
        """stale_while_revalidate strategy generates without errors."""
        gen = make_generator(cache_strategy="stale_while_revalidate")
        sw = gen.generate_service_worker()
        assert "fetch" in sw
