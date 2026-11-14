"""The allowlist checks ask the WebSocket mount gate itself (#3994).

``LiveViewTestClient.assert_allowlisted``, ``assert_all_routed_liveviews_allowlisted``
and the ``djust.V005`` system check exist to say, before a browser does, that
the WebSocket mount will refuse a view. The mount gate
(``djust.security.mount.is_view_path_allowed``) matches an allowlist entry on a
dotted-segment boundary; the three checks used a bare ``startswith``, so an
entry that is a string prefix of a module but not a package of it
(``"djust"`` against ``djust_docs.views``) passed every check while the mount
refused the view. The demo project had fourteen routed views in that state.

Each case below uses an entry that is a string prefix of the routed view's
module and not a segment of it, so only a segment match refuses it.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import clear_url_caches

from djust.security.mount import is_view_path_allowed
from djust.testing import LiveViewTestClient, assert_all_routed_liveviews_allowlisted
from djust.tests.checkviews_v005_1674 import RoutedAllowlistView

_URLCONF = "djust.tests.checkviews_v005_1674"
_ROUTED = "djust.tests.checkviews_v005_1674.RoutedAllowlistView"
# A string prefix of the module ``djust.tests.checkviews_v005_1674``, not a package of it.
_NOT_A_SEGMENT = ["djust.tests.checkviews_v005"]


@override_settings(LIVEVIEW_ALLOWED_MODULES=_NOT_A_SEGMENT)
def test_the_mount_gate_refuses_a_string_prefix_that_is_not_a_segment():
    """The reference the three checks below must agree with."""
    assert not is_view_path_allowed(_ROUTED)


@override_settings(LIVEVIEW_ALLOWED_MODULES=_NOT_A_SEGMENT)
def test_assert_allowlisted_refuses_what_the_mount_refuses():
    with pytest.raises(AssertionError, match="LIVEVIEW_ALLOWED_MODULES"):
        LiveViewTestClient(RoutedAllowlistView).assert_allowlisted()


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=_NOT_A_SEGMENT)
def test_assert_all_routed_refuses_what_the_mount_refuses():
    clear_url_caches()
    with pytest.raises(AssertionError, match="RoutedAllowlistView"):
        assert_all_routed_liveviews_allowlisted()


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=_NOT_A_SEGMENT)
def test_v005_flags_what_the_mount_refuses():
    clear_url_caches()
    from djust.checks import check_liveviews

    flagged = [e.msg for e in check_liveviews(None) if e.id == "djust.V005"]
    assert any("RoutedAllowlistView" in m for m in flagged)


@override_settings(ROOT_URLCONF=_URLCONF, LIVEVIEW_ALLOWED_MODULES=["djust.tests."])
def test_a_trailing_dot_entry_still_admits_its_package():
    """The gate normalises ``"pkg."`` to ``"pkg"``; the checks agree."""
    clear_url_caches()
    from djust.checks import check_liveviews

    assert is_view_path_allowed(_ROUTED)
    LiveViewTestClient(RoutedAllowlistView).assert_allowlisted()
    assert_all_routed_liveviews_allowlisted()
    flagged = [e.msg for e in check_liveviews(None) if e.id == "djust.V005"]
    assert not any("RoutedAllowlistView" in m for m in flagged)
