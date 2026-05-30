"""Tests for #1674 — V005 must catch URL-routed LiveViews missing from
``LIVEVIEW_ALLOWED_MODULES``, and its matching must mirror the WebSocket mount
enforcement (non-empty allowlist, prefix match) to avoid false positives.

Before #1674, ``check_liveviews`` discovered views only via ``__subclasses__()``
(import-timing dependent), so a URL-routed view whose module wasn't imported at
check time slipped through — it then silently degraded to HTTP fallback in the
browser. The fix also walks the root URLconf (``_routed_liveview_classes``) and
aligns V005's matching with ``websocket.py`` (prefix, enforced only when the
allowlist is non-empty).
"""

from __future__ import annotations

from django.test import override_settings
from django.urls import clear_url_caches

_URLCONF = "djust.tests.checkviews_v005_1674"
_ROUTED = "djust.tests.checkviews_v005_1674.RoutedAllowlistView"


def _v005_view_paths(errors):
    """The set of view paths the returned V005 warnings name (by `cls_label`)."""
    return [e.msg for e in errors if e.id == "djust.V005"]


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=["some.other.app"])
def test_routed_liveview_classes_discovers_url_routed_view():
    """The new resolver walk finds a URL-routed LiveView."""
    clear_url_caches()
    from djust.checks import _routed_liveview_classes
    from djust.tests.checkviews_v005_1674 import RoutedAllowlistView

    assert RoutedAllowlistView in set(_routed_liveview_classes())


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=["some.unrelated.pkg"])
def test_v005_flags_routed_view_when_module_not_allowlisted():
    """A URL-routed view whose module isn't allowlisted → V005 fires for it."""
    clear_url_caches()
    from djust.checks import check_liveviews

    msgs = _v005_view_paths(check_liveviews(None))
    assert any("RoutedAllowlistView" in m for m in msgs), (
        "V005 must flag the URL-routed view missing from LIVEVIEW_ALLOWED_MODULES"
    )


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=["djust.tests"])
def test_v005_silent_for_routed_view_under_prefix_allowlist():
    """Prefix match (mirrors WS): allowlist ['djust.tests'] permits
    'djust.tests.checkviews_v005_1674.RoutedAllowlistView' → no V005 for it.
    Guards against false positives on the newly-discovered routed views."""
    clear_url_caches()
    from djust.checks import check_liveviews

    msgs = _v005_view_paths(check_liveviews(None))
    assert not any("RoutedAllowlistView" in m for m in msgs), (
        "prefix-allowlisted routed view must NOT be flagged"
    )


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=[])
def test_v005_silent_when_allowlist_empty_means_allow_all():
    """An explicitly EMPTY allowlist means allow-all (mirrors the WS
    ``if allowed_modules:`` guard) — V005 must fire for NO view. Regression
    guard for the pre-#1674 bug where ``[]`` (not None) flagged every view."""
    clear_url_caches()
    from djust.checks import check_liveviews

    assert _v005_view_paths(check_liveviews(None)) == [], (
        "empty allowlist = allow-all; V005 must be silent"
    )


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=["some.unrelated.pkg"])
def test_v005_gate_off_self_test_routed():
    """#1468 gate-off: the flag-case (unrelated allowlist) fires for the routed
    view; the allow-case (prefix allowlist) does not — proving the positive
    assertion exercises the real branch, not a constant."""
    clear_url_caches()
    from djust.checks import check_liveviews

    flagged = any("RoutedAllowlistView" in m for m in _v005_view_paths(check_liveviews(None)))
    assert flagged, "misconfig must fire V005 for the routed view"

    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"]):
        clear_url_caches()
        silent = not any(
            "RoutedAllowlistView" in m for m in _v005_view_paths(check_liveviews(None))
        )
    assert silent, "gate-off (prefix allowlist) must silence the routed-view V005"
