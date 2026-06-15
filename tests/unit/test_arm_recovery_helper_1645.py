"""Regression/consolidation: a single _arm_recovery() helper is the source of
truth for VDOM recovery-baseline arming, so no send path can drift (#1645).

`_recovery_html` / `_recovery_version` were armed by a hand-copied two-line
assignment at every send path (handle_event, server_push, _run_async_work). That
is exactly the drift that caused #1639 — the async path was added without the
arming. This pins the consolidation: the assignment lives in one helper, and
every render-send path routes through it.
"""

from __future__ import annotations

import inspect
import re

import djust.websocket as ws_mod
from djust.websocket import LiveViewConsumer


def test_arm_recovery_sets_both_fields():
    consumer = LiveViewConsumer()
    # #1788: _arm_recovery no longer takes a version arg — it captures the
    # consumer's current _last_sent_version (the version of the frame being
    # armed), so the html_recovery frame carries the consumer version of the
    # frame it replaces (the client sets clientVdomVersion = data.version
    # directly on html_recovery).
    consumer._last_sent_version = 7
    consumer._arm_recovery("<div>x</div>")
    assert consumer._recovery_html == "<div>x</div>"
    assert consumer._recovery_version == 7


def test_arm_recovery_is_the_only_arming_mechanism():
    """No method other than _arm_recovery (and the one-time clear in
    handle_request_html) may assign _recovery_html directly — otherwise a new
    send path could arm inconsistently or forget a field."""
    src = inspect.getsource(ws_mod)
    # All `self._recovery_html = ...` assignments in the module.
    assigns = re.findall(r"self\._recovery_html\s*=\s*(.+)", src)
    # Allowed: the helper's own assignment, and the one-time clear (= None).
    disallowed = [rhs.strip() for rhs in assigns if rhs.strip() not in ("html", "None")]
    assert disallowed == [], (
        "_recovery_html must only be assigned inside _arm_recovery (rhs 'html') "
        f"or cleared (= None); found stray assignments: {disallowed}"
    )


def test_render_send_paths_route_through_arm_recovery():
    """Each render-send path must call _arm_recovery rather than hand-assign."""
    for name in ("handle_event", "server_push", "_run_async_work"):
        method_src = inspect.getsource(getattr(LiveViewConsumer, name))
        assert "_arm_recovery(" in method_src, (
            f"{name} must arm the recovery baseline via self._arm_recovery(...) "
            f"so it can't drift from the other send paths (#1645)."
        )
        # And must NOT hand-assign _recovery_html directly anymore.
        assert "_recovery_html =" not in method_src, (
            f"{name} still hand-assigns _recovery_html; route it through "
            f"_arm_recovery instead (#1645)."
        )


def test_arm_recovery_call_site_count_matches_known_send_paths():
    """Count-based guard (#1655, Action #1125): pin the number of
    ``self._arm_recovery(...)`` call sites so a NEW render-send path can't be
    added without consciously arming the recovery baseline (the #1639 shape:
    a send path that renders+sends but forgets to arm). A new path that arms
    bumps this count (update the list + N below); a new path that FORGETS to
    arm leaves the count unchanged, but is caught by
    ``test_render_send_paths_route_through_arm_recovery`` extended with its name.

    Known sites (5): _run_async_work patches-branch + full-HTML-fallback,
    handle_event patches-branch, handle_event full-HTML (html_update) fallback
    (#1785), server_push.
    """
    import inspect

    import djust.websocket as ws_mod

    src = inspect.getsource(ws_mod)
    call_sites = src.count("self._arm_recovery(")
    assert call_sites == 5, (
        f"expected 5 self._arm_recovery() call sites (the known render-send "
        f"paths), found {call_sites}. If you added a render-send path, arm the "
        f"recovery baseline there and update this count + the enumerated list "
        f"(#1655/#1645/#1639)."
    )
