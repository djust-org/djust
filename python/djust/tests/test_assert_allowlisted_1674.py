"""Tests for #1674 — ``LiveViewTestClient.assert_allowlisted`` and the
standalone ``assert_all_routed_liveviews_allowlisted`` helper.

These make the allowlist-misconfiguration gap testable from the unit suite:
``mount()`` instantiates the view class directly (bypassing the WS allowlist),
so a routed view forgotten from ``LIVEVIEW_ALLOWED_MODULES`` is otherwise green
in tests yet broken in the browser.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import clear_url_caches

from djust.testing import LiveViewTestClient, assert_all_routed_liveviews_allowlisted
from djust.tests.checkviews_v005_1674 import RoutedAllowlistView

_URLCONF = "djust.tests.checkviews_v005_1674"


# --- per-view assert_allowlisted -------------------------------------------


@override_settings(LIVEVIEW_ALLOWED_MODULES=["some.unrelated.pkg"])
def test_assert_allowlisted_raises_when_not_allowlisted():
    client = LiveViewTestClient(RoutedAllowlistView)
    with pytest.raises(AssertionError, match="LIVEVIEW_ALLOWED_MODULES"):
        client.assert_allowlisted()


@override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"])
def test_assert_allowlisted_passes_under_prefix_allowlist():
    # 'djust.tests' is a prefix of the view's module → permitted, no raise.
    LiveViewTestClient(RoutedAllowlistView).assert_allowlisted()


@override_settings(LIVEVIEW_ALLOWED_MODULES=[])
def test_assert_allowlisted_noop_when_allowlist_empty():
    # Empty allowlist = allow-all (mirrors the WS guard) → no-op.
    LiveViewTestClient(RoutedAllowlistView).assert_allowlisted()


@override_settings(LIVEVIEW_ALLOWED_MODULES=None)
def test_assert_allowlisted_noop_when_allowlist_unset():
    # Unset (None) = allow-all → no-op.
    LiveViewTestClient(RoutedAllowlistView).assert_allowlisted()


# --- standalone assert_all_routed_liveviews_allowlisted --------------------


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=["some.unrelated.pkg"])
def test_assert_all_routed_raises_and_lists_missing():
    clear_url_caches()
    with pytest.raises(AssertionError, match="RoutedAllowlistView"):
        assert_all_routed_liveviews_allowlisted()


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=["djust.tests"])
def test_assert_all_routed_passes_when_allowlisted():
    clear_url_caches()
    assert_all_routed_liveviews_allowlisted()  # no raise


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=[])
def test_assert_all_routed_noop_when_empty():
    clear_url_caches()
    assert_all_routed_liveviews_allowlisted()  # allow-all, no raise
