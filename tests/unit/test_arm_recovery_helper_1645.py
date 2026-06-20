"""Regression/consolidation: a single _arm_recovery() helper is the source of
truth for VDOM recovery-baseline arming, so no send path can drift (#1645).

`_recovery_html` / `_recovery_version` were armed by a hand-copied two-line
assignment at every send path (handle_event, server_push, _run_async_work). That
is exactly the drift that caused #1639 — the async path was added without the
arming. This pins the consolidation: the assignment lives in one helper, and
every render-send path routes through it.

#1817 extends the consolidation: render-send paths now allocate the wire version
AND arm recovery in a SINGLE step via ``_next_version_armed(html)`` (which calls
``_arm_recovery`` internally), so the version allocation and recovery baseline
can never drift apart either. ``_arm_recovery`` therefore now has exactly one
direct call site — the ``_next_version_armed`` helper body.
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
    """Each render-send path must arm the recovery baseline (#1645).

    #1817: render-send paths now arm via the shared ``_next_version_armed(html)``
    helper (which advances the wire version AND calls ``_arm_recovery`` in one
    step) rather than a hand-copied ``_next_version()`` + ``_arm_recovery(html)``
    pair. Accept either form so the pin survives the #1817 consolidation while
    still enforcing that each path arms and never hand-assigns ``_recovery_html``.
    """
    # Finding #6: ``handle_event`` is now a thin tenant-context wrapper around
    # ``_handle_event_inner``; the arm-recovery code lives in the inner method.
    for name in ("_handle_event_inner", "server_push", "_run_async_work"):
        method_src = inspect.getsource(getattr(LiveViewConsumer, name))
        arms = "_arm_recovery(" in method_src or "_next_version_armed(" in method_src
        assert arms, (
            f"{name} must arm the recovery baseline via self._next_version_armed(html) "
            f"(#1817) or self._arm_recovery(...) so it can't drift from the other send "
            f"paths (#1645)."
        )
        # And must NOT hand-assign _recovery_html directly anymore.
        assert "_recovery_html =" not in method_src, (
            f"{name} still hand-assigns _recovery_html; route it through "
            f"_arm_recovery / _next_version_armed instead (#1645)."
        )


def test_arm_recovery_call_site_count_matches_known_send_paths():
    """Count-based guard (#1655, Action #1125): pin the number of
    ``self._arm_recovery(...)`` call sites so the single-source-of-truth helper
    invariant can't silently regress.

    #1817 consolidation: after #1817 every render-send path arms via the shared
    ``_next_version_armed(html)`` helper, which is the ONLY place that calls
    ``_arm_recovery(html)`` directly. So the direct ``self._arm_recovery(`` count
    collapses to exactly 1 (the helper body). The render-send call-site count is
    now pinned by ``_next_version_armed`` invocations in
    ``test_ws_send_version_1788.test_every_client_checked_send_path_uses_next_version``
    — a NEW render-send path that forgets to arm trips THAT count test.
    """
    import inspect

    import djust.websocket as ws_mod

    src = inspect.getsource(ws_mod)
    call_sites = src.count("self._arm_recovery(")
    assert call_sites == 1, (
        f"expected exactly 1 self._arm_recovery() call site — the _next_version_armed "
        f"helper body, the single source of truth for recovery arming after the #1817 "
        f"consolidation; found {call_sites}. A direct _arm_recovery() call outside the "
        f"helper means a path bypassed _next_version_armed (re-route it), and the "
        f"render-send call-site count is pinned by the _next_version_armed count test "
        f"in test_ws_send_version_1788 (#1655/#1645/#1639/#1817)."
    )

    # The single direct call site lives in the _next_version_armed helper.
    helper_src = inspect.getsource(ws_mod.LiveViewConsumer._next_version_armed)
    assert helper_src.count("self._arm_recovery(") == 1, (
        "the single _arm_recovery() call site must be inside _next_version_armed "
        "(the consolidated render-send arming helper, #1817)."
    )
