"""Tests for #2059 -- ``djust.V013``: an HTTP-only ``dispatch()``/``get()``/
``post()`` override in a LiveView's MRO never runs on a WebSocket mount.

Background: the WS mount path calls ``view_instance.mount(request,
**kwargs)`` directly (``runtime.py``'s ``dispatch_mount``, ~line 2269) -- it
never calls ``dispatch()``/``get()``/``post()``. Any mixin that hooks one of
these to do meaningful HTTP-only work (tenant resolution, rate limiting,
custom auth) silently never runs for a WS-mounted view. Downstream symptom
pattern cited in the issue: ``self._tenant = None`` in handlers, empty
querysets, writes that no-op -- the "TenantMixin class of downstream bug".

``djust.tenants.mixin.TenantMixin`` is itself an example of exactly this
override shape (it defines ``dispatch``/``get``/``post`` for pure-HTTP
Django parity) -- but it is NOT a false positive: ``djust.auth.core.
run_pre_mount_auth`` independently calls its ``_ensure_tenant()`` hook on
every live mount path (WS/SSE/runtime), so the mixin is already
WS-reconciled. ``test_djust_internal_tenant_mixin_does_not_trigger`` below
pins exactly this exclusion using the real production class.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import override_settings

from djust import LiveView
from djust.checks import check_liveviews
from djust.tenants import TenantMixin

_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Synthetic fixtures. Module-level so `type(view).__mro__` / `__module__` /
# `inspect.getfile`/`getsourcelines` all resolve for real, exactly as they
# would for a genuine app-authored mixin. This module's own dotted path
# (``djust.tests.test_check_ws_parity_2059``) contains "test", so the V013
# ancestor-exclusion's djust-prefix carve-out does NOT blanket-exempt these
# classes (mirrors the existing `cls`-level convention in check_liveviews) --
# they are inspected like any other app-authored code.
# ---------------------------------------------------------------------------


class _BadDispatchMixin:
    """Third-party-style mixin hooking dispatch() with HTTP-only setup work
    (rate limiting, tenant resolution, ...) -- the #2059 trigger shape."""

    def dispatch(self, request, *args, **kwargs):
        # Stand-in for real per-request setup (e.g. resolving a tenant, or
        # checking a rate limit) that the WS mount path never runs, because
        # the WS path calls mount() directly and never dispatch().
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


class _ViewWithBadDispatchMixin(_BadDispatchMixin, LiveView):
    """Synthetic LiveView whose MRO carries an HTTP-only dispatch() override."""

    template_name = "checkviews_v013_dummy.html"

    def mount(self, request, **kwargs):
        pass


class _CleanLiveView(LiveView):
    """A LiveView with no dispatch()/get()/post() override anywhere in its
    own (non-djust) MRO -- the negative control."""

    template_name = "checkviews_v013_dummy.html"

    def mount(self, request, **kwargs):
        pass


class _ViewWithDjustTenantMixin(TenantMixin, LiveView):
    """A LiveView using djust's OWN ``djust.tenants.mixin.TenantMixin``,
    which overrides dispatch()/get()/post() for pure-HTTP Django parity but
    is independently reconciled with the WS mount path via
    ``djust.auth.core.run_pre_mount_auth``'s ``_ensure_tenant()`` hook. Must
    NOT trigger V013 -- this is the canonical exclusion case study cited in
    the issue."""

    template_name = "checkviews_v013_dummy.html"

    def mount(self, request, **kwargs):
        pass


def _v013_messages_for(cls: type) -> list:
    """The djust.V013 CheckMessages whose text names *cls*."""
    cls_label = "%s.%s" % (cls.__module__, cls.__qualname__)
    errors = check_liveviews(None)
    return [e for e in errors if e.id == "djust.V013" and cls_label in e.msg]


def test_dispatch_overriding_mixin_triggers_exactly_one_v013_warning():
    """A mixin overriding dispatch() in the MRO -> exactly one djust.V013,
    naming the mixin and the overridden method."""
    msgs = _v013_messages_for(_ViewWithBadDispatchMixin)
    assert len(msgs) == 1, "expected exactly one V013 for the bad-dispatch mixin, got %r" % msgs
    assert "_BadDispatchMixin" in msgs[0].msg
    assert "dispatch()" in msgs[0].msg


def test_clean_liveview_triggers_no_v013_warning():
    """A LiveView with no non-djust dispatch/get/post override -> silent."""
    assert _v013_messages_for(_CleanLiveView) == []


def test_djust_internal_tenant_mixin_does_not_trigger():
    """djust's own TenantMixin overrides dispatch/get/post but is already
    WS-reconciled via run_pre_mount_auth's _ensure_tenant() hook -- must be
    silent. Pins the canonical #2059 downstream-bug exclusion case with the
    real production class (not a synthetic stand-in)."""
    assert _v013_messages_for(_ViewWithDjustTenantMixin) == []


def test_v013_gate_off_suppression_silences_the_trigger():
    """#1468 gate-off: suppressing djust.V013 via DJUST_CONFIG must silence
    the exact same trigger the first test fires on -- proving that
    assertion exercises the real V013 code path, not a coincidental string
    match. Mirrors the existing V005 gate-off shape
    (test_v005_gate_off_self_test_routed)."""
    cls_label = "%s.%s" % (
        _ViewWithBadDispatchMixin.__module__,
        _ViewWithBadDispatchMixin.__qualname__,
    )

    unsuppressed = check_liveviews(None)
    assert any(e.id == "djust.V013" and cls_label in e.msg for e in unsuppressed), (
        "sanity: the trigger must fire before suppression"
    )

    with override_settings(DJUST_CONFIG={"suppress_checks": ["V013"]}):
        suppressed = check_liveviews(None)
    assert not any(e.id == "djust.V013" and cls_label in e.msg for e in suppressed), (
        "gate-off: DJUST_CONFIG suppress_checks=['V013'] must silence the trigger"
    )


_DOGFOOD_SCRIPT = """
import json
import django

django.setup()

from djust.checks import check_liveviews

errors = check_liveviews(None)
v013 = [e.msg for e in errors if e.id == "djust.V013"]
print("V013_JSON_START")
print(json.dumps(v013))
print("V013_JSON_END")
"""


def test_v013_dogfood_zero_warnings_on_demo_project():
    """#1060 dogfood: run the check against examples/demo_project in a FRESH
    subprocess -- not the shared pytest session -- so cross-test import
    pollution cannot produce a false trigger. Concretely: other tests in
    this suite call ``resolve_view_class("demo_app.views.CounterView")``,
    which transitively imports the deprecated, unrouted
    ``demo_app/views_old.py`` (its ``include('demo_app.urls')`` is commented
    out of ``ROOT_URLCONF`` -- see CLAUDE.md's #1849 note on the same stale
    module) and permanently registers its ``ProductDataTableView.post()``
    override in the process-wide ``LiveView.__subclasses__()`` graph for the
    rest of the pytest session. A fresh subprocess mirrors the demo
    project's ACTUAL running state: only views reachable via the real
    ``ROOT_URLCONF`` (walked by ``djust.checks._routed_liveview_classes``)
    or already-imported get discovered -- exactly what a real ``manage.py
    check`` run against the live demo would see.
    """
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = "demo_project.settings"
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(_REPO_ROOT / "python"),
            str(_REPO_ROOT / "examples" / "demo_project"),
            env.get("PYTHONPATH", ""),
        ]
    ).rstrip(os.pathsep)

    proc = subprocess.run(
        [sys.executable, "-c", _DOGFOOD_SCRIPT],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, "dogfood subprocess failed:\nSTDOUT:\n%s\nSTDERR:\n%s" % (
        proc.stdout,
        proc.stderr,
    )

    stdout = proc.stdout
    start = stdout.index("V013_JSON_START") + len("V013_JSON_START")
    end = stdout.index("V013_JSON_END")
    v013 = json.loads(stdout[start:end].strip())
    assert v013 == [], "V013 must be silent on the live (routed) demo project: %r" % v013
