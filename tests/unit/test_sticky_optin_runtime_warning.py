"""Tests for ``warn_sticky_child_optin_skip`` — ADR-018 iter 18c runtime
one-shot warning for the Decision-5 opt-in mismatch.

When a sticky-child save is skipped because the CHILD opted into
``enable_state_snapshot`` but its PARENT did not, the framework emits a
``logger.warning`` exactly once per ``(parent-class, sticky_id)`` via
``emit_one_shot_class_warning``. These tests exercise the helper directly
with synthetic parent/child instances.

Per Action #1109, each test builds FRESH synthetic classes (via ``type``)
so a prior test's ``emit_one_shot_class_warning`` sentinel doesn't suppress
a later test's warning.
"""

from __future__ import annotations

import logging

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust.mixins.sticky import warn_sticky_child_optin_skip


def _make_view(name, *, sticky_id=None, enable_state_snapshot=False):
    """Build a fresh synthetic view class + instance.

    A distinct class per call keeps ``emit_one_shot_class_warning`` sentinels
    (set on the class) from leaking across tests.
    """
    attrs = {"enable_state_snapshot": enable_state_snapshot}
    if sticky_id is not None:
        attrs["sticky_id"] = sticky_id
    cls = type(name, (object,), attrs)
    return cls()


class TestStickyOptinRuntimeWarning:
    """warn_sticky_child_optin_skip fires once on the opt-in mismatch."""

    def test_warning_fires_on_optin_mismatch(self, caplog):
        """Child opted in (sticky_id + enable_state_snapshot), parent not →
        exactly one warning containing the child name + sticky_id.
        """
        child = _make_view("MismatchChild", sticky_id="sid_a", enable_state_snapshot=True)
        parent = _make_view("MismatchParent", enable_state_snapshot=False)

        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            warn_sticky_child_optin_skip(child, parent)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, "exactly one warning on the misconfiguration"
        msg = warnings[0].getMessage()
        assert "MismatchChild" in msg
        assert "sid_a" in msg

    def test_warning_fires_once_per_parent_sticky_id(self, caplog):
        """Three calls with the same (parent, child) → exactly ONE warning."""
        child = _make_view("OnceChild", sticky_id="sid_once", enable_state_snapshot=True)
        parent = _make_view("OnceParent", enable_state_snapshot=False)

        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            warn_sticky_child_optin_skip(child, parent)
            warn_sticky_child_optin_skip(child, parent)
            warn_sticky_child_optin_skip(child, parent)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, "sentinel dedup — one warning per (parent, sticky_id)"

    def test_warning_distinct_per_sticky_id(self, caplog):
        """Same parent class, two children with different sticky_id → two
        warnings (distinct sentinels keyed by sticky_id).
        """
        # Both children embedded under the SAME parent class instance.
        parent = _make_view("TwoStickyParent", enable_state_snapshot=False)
        child_a = _make_view("ChildA", sticky_id="sid_x", enable_state_snapshot=True)
        child_b = _make_view("ChildB", sticky_id="sid_y", enable_state_snapshot=True)

        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            warn_sticky_child_optin_skip(child_a, parent)
            warn_sticky_child_optin_skip(child_b, parent)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2, "two distinct sticky_ids → two distinct sentinels"

    def test_warning_silent_when_both_opt_in(self, caplog):
        """Both opted in → zero warnings (the save runs, no misconfig)."""
        child = _make_view("BothChild", sticky_id="sid_both", enable_state_snapshot=True)
        parent = _make_view("BothParent", enable_state_snapshot=True)

        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            warn_sticky_child_optin_skip(child, parent)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [], "both opted in — no warning"

    def test_warning_silent_when_child_not_opted_in(self, caplog):
        """Child has no enable_state_snapshot / no sticky_id → zero warnings."""
        # Case 1: child has sticky_id but no enable_state_snapshot.
        child_no_snap = _make_view("NoSnapChild", sticky_id="sid_ns", enable_state_snapshot=False)
        parent = _make_view("NsParent", enable_state_snapshot=False)
        # Case 2: child opted in but has no sticky_id.
        child_no_sid = _make_view("NoSidChild", enable_state_snapshot=True)

        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            warn_sticky_child_optin_skip(child_no_snap, parent)
            warn_sticky_child_optin_skip(child_no_sid, parent)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [], "child not opted in / non-sticky — nothing to warn about"

    def test_warning_gate_off_self_test(self, caplog):
        """#1468 gate-off self-test — proves the warning fires ONLY on the
        misconfiguration.

        With the misconfig (parent not opted in) the helper warns; gating
        the misconfig off (parent opted in) makes the same call path silent
        — so the positive assertion in ``test_warning_fires_on_optin_mismatch``
        is not a tautology.
        """
        child = _make_view("GateChild", sticky_id="sid_gate", enable_state_snapshot=True)
        misconfig_parent = _make_view("GateBadParent", enable_state_snapshot=False)
        ok_parent = _make_view("GateOkParent", enable_state_snapshot=True)

        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            warn_sticky_child_optin_skip(child, misconfig_parent)
            misconfig_count = len([r for r in caplog.records if r.levelno == logging.WARNING])
            caplog.clear()
            warn_sticky_child_optin_skip(child, ok_parent)
            gated_off_count = len([r for r in caplog.records if r.levelno == logging.WARNING])

        assert misconfig_count == 1, "misconfig must warn"
        assert gated_off_count == 0, "gate the misconfig off → silent (not tautological)"


# ---------------------------------------------------------------------------
# Integration — the runtime warning is WIRED at both call sites (#1196).
# A direct helper test verifies the helper; these verify the WS save-block
# else-branch and the HTTP save sweep actually invoke it. They reuse the
# 1471 persistence-suite harness (the _FakeConsumer + the _ParentNoOptIn
# view, which embeds an opted-in sticky child under a non-opted-in parent —
# exactly the Decision-5 misconfiguration).
# ---------------------------------------------------------------------------

_PERSIST_MODULE = "djust.tests.test_sticky_child_persistence_1471"


def _clear_optin_sentinel(parent_cls, sticky_id):
    """Drop a stale ``emit_one_shot_class_warning`` sentinel off a parent
    class so the warning can fire again in a fresh test run.

    The 1471 harness reuses module-level view classes; the one-shot sentinel
    is set on the class, so a prior run would suppress the warning here.
    """
    sentinel = "_djust_warned_sticky_optin_%s" % sticky_id
    if sentinel in parent_cls.__dict__:
        delattr(parent_cls, sentinel)


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_ws_save_block_emits_optin_warning(caplog):
    """A sticky-child WS event under a non-opted-in parent → the WS save
    block's else-branch (``websocket.py``) emits the one-shot warning.

    Confirms the WS call site is wired — not just the helper in isolation.
    """
    from djust.tests.test_sticky_child_persistence_1471 import (
        _ParentNoOptIn,
        _setup_consumer,
    )

    _clear_optin_sentinel(_ParentNoOptIn, "counter")

    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[_PERSIST_MODULE]):
        consumer, _session_key = await sync_to_async(_setup_consumer)(_ParentNoOptIn, "/ws-optin/")
        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            await consumer.handle_event(
                {
                    "type": "event",
                    "event": "increment",
                    "params": {"view_id": "counter"},
                    "ref": 1,
                }
            )

    optin_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "enable_state_snapshot=True" in r.getMessage()
    ]
    assert len(optin_warnings) == 1, (
        "the WS save block must emit the opt-in mismatch warning when a "
        "sticky-child event is skipped by the both-opt-in gate"
    )


@pytest.mark.django_db
def test_http_save_sweep_emits_optin_warning(caplog):
    """An HTTP POST under a non-opted-in parent embedding an opted-in sticky
    child → the HTTP save sweep (``mixins/request.py``) emits the one-shot
    warning for the filtered-out child.

    Confirms the second call site is wired.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    from djust.tests.test_sticky_child_persistence_1471 import _ParentNoOptIn

    _clear_optin_sentinel(_ParentNoOptIn, "counter")

    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[_PERSIST_MODULE]):
        session = SessionStore()
        session.create()

        rf = RequestFactory()
        request = rf.post(
            "/http-optin/",
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT="increment",
        )
        request.user = AnonymousUser()
        request.session = session

        parent = _ParentNoOptIn()
        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            response = parent.post(request)
        assert response.status_code == 200

    optin_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "enable_state_snapshot=True" in r.getMessage()
    ]
    assert len(optin_warnings) == 1, (
        "the HTTP save sweep must emit the opt-in mismatch warning for a "
        "sticky child filtered out of the save set"
    )
