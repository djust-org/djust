"""Rendered timestamps must land in ``settings.TIME_ZONE`` (#2209).

The bug
-------
The Rust template engine performed no timezone conversion at any layer. Django
applies ``timezone.localtime()`` to an aware datetime before formatting it;
djust formatted whatever offset the serializer handed it, which under
``USE_TZ=True`` is UTC. So a New York project rendered ``23:30`` where Django
renders ``19:30`` — four hours out, in the configuration ``djust new``
generates, since the scaffold sets ``USE_TZ = True``
(``scaffolding/templates.py:171``).

Why these tests drive ``LiveView.render()``
-------------------------------------------
The filter-level parity table lives in
``crates/djust_templates/tests/test_timezone_parity_2209.rs``, which pins every
expectation against a live Django 5.2 render. What THOSE cases cannot see is the
Python half: whether the active zone actually reaches Rust on the render path,
and whether it is re-read often enough. Both are the load-bearing part of the
fix and neither is observable from a filter unit test, so every case here goes
through the real ``render()``.

The re-read frequency is the subtle half. ``timezone.activate()`` is per-request
(the documented way to show each user their own zone) and
``override_settings(TIME_ZONE=...)`` is per-test, while the ``RustLiveView`` is
session-cached and outlives both. A zone captured at ``DjustConfig.ready()``, or
once per view instance, would be stale for exactly the cases users care about —
which is why the wiring sits in ``_sync_state_to_rust`` (the path that re-runs
every event, #1722) and why ``test_a_later_activate_changes_the_next_render``
exists.
"""

import datetime as dt

import pytest
from django.test import RequestFactory, override_settings
from django.utils import timezone as dj_timezone

from djust import LiveView

# 23:30 UTC — chosen so the New York conversion crosses no date boundary
# (19:30 same day) while the Tokyo one does (08:30 the NEXT day), so a test that
# only checked the clock could not pass by accident.
AWARE_SUMMER = dt.datetime(2026, 8, 22, 23, 30, 0, tzinfo=dt.timezone.utc)
AWARE_WINTER = dt.datetime(2026, 1, 15, 23, 30, 0, tzinfo=dt.timezone.utc)
NAIVE = dt.datetime(2026, 8, 22, 23, 30, 0)


class _StampView(LiveView):
    template = '<div dj-id="0">{{ created|date:"Y-m-d H:i" }}</div>'
    _value = AWARE_SUMMER

    def mount(self, request, **kwargs):
        self.created = type(self)._value


def _render(value=AWARE_SUMMER, template=None):
    """Render one datetime through the real LiveView path.

    A fresh subclass per call rather than mutating a shared class attribute —
    ``type(self).attr = value`` in a fixture leaks across tests (#1109).
    """
    attrs = {"_value": value}
    if template is not None:
        attrs["template"] = template
    view = type("_V", (_StampView,), attrs)()
    request = RequestFactory().get("/")
    view.mount(request)
    view.request = request
    return view.render()


@pytest.fixture(autouse=True)
def _reset_activation():
    """Django's activated zone is a thread-local that outlives a test."""
    dj_timezone.deactivate()
    yield
    dj_timezone.deactivate()


# ---------------------------------------------------------------------------
# The bug, through the path the issue reported it on.
# ---------------------------------------------------------------------------


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_an_aware_datetime_renders_in_settings_time_zone():
    # The issue's own reproduction. Django renders '2026-08-22 19:30'; djust
    # rendered '2026-08-22 23:30' before this fix.
    assert "2026-08-22 19:30" in _render()


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_the_conversion_follows_dst_rather_than_a_fixed_offset():
    # Same zone, one hour of difference in the offset: -0400 in August, -0500 in
    # January. This is what rules out passing a single per-render offset down —
    # one render can hold both, and any table of timestamps spanning six months
    # does. Django renders 19:30 and 18:30.
    assert "2026-08-22 19:30" in _render(AWARE_SUMMER)
    assert "2026-01-15 18:30" in _render(AWARE_WINTER)


@override_settings(USE_TZ=True, TIME_ZONE="Asia/Tokyo")
def test_a_conversion_that_crosses_the_date_boundary():
    # +0900 pushes 23:30 into the next day. A test that asserted only on the
    # clock would pass while the date silently stayed wrong.
    assert "2026-08-23 08:30" in _render()


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_a_naive_datetime_is_left_alone():
    # Django does not apply ``localtime`` to a naive value — it is already
    # understood to be local. Converting it would move every timestamp in a
    # ``USE_TZ = False`` project, where naive datetimes are the norm.
    assert "2026-08-22 23:30" in _render(NAIVE)


@override_settings(USE_TZ=False, TIME_ZONE="America/New_York")
def test_use_tz_false_disables_conversion_entirely():
    # ``TIME_ZONE`` is still set here — it always is — so this pins that
    # ``USE_TZ`` is what gates the conversion, not the mere presence of a zone.
    assert "2026-08-22 23:30" in _render()


# ---------------------------------------------------------------------------
# The half a filter-level test cannot see: WHEN the zone is read.
# ---------------------------------------------------------------------------


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_a_later_activate_changes_the_next_render():
    # ``timezone.activate()`` is per-request. A zone captured at app-ready or
    # once per view instance would pin the first value and silently ignore every
    # later switch — the failure mode this test exists for. Both renders below
    # use the same view class and the same value.
    assert "2026-08-22 19:30" in _render()
    dj_timezone.activate("Asia/Tokyo")
    assert "2026-08-23 08:30" in _render()


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_the_same_view_instance_re_reads_the_zone_between_renders():
    # Sharper than the previous case: one view instance, rendered twice, with
    # the zone changed in between. The ``RustLiveView`` is session-cached and
    # outlives a request, so a per-instance cache would pass the test above (two
    # instances) while failing this one.
    view = type("_V", (_StampView,), {})()
    request = RequestFactory().get("/")
    view.mount(request)
    view.request = request

    assert "2026-08-22 19:30" in view.render()
    dj_timezone.activate("Asia/Tokyo")
    assert "2026-08-23 08:30" in view.render()


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_an_unknown_time_zone_renders_unconverted_rather_than_raising():
    # A ``TIME_ZONE`` the bundled tz database does not carry must not 500 the
    # page. Django itself would reject such a value long before this point, so
    # this covers an embedder or a tzdata vintage gap.
    from djust._rust import set_active_timezone

    assert set_active_timezone("Not/AZone") is False
    # And the previously-set zone survives the refusal — a rejected write must
    # not half-apply.
    assert set_active_timezone("America/New_York") is True
    assert set_active_timezone("Not/AZone") is False
    from djust._rust import active_timezone_name

    assert active_timezone_name() == "America/New_York"


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_the_wiring_reports_the_zone_it_applied():
    # Asserts the handoff took effect rather than inferring it from output — a
    # setter with no getter cannot be tested end to end (#2017).
    from djust._rust import active_timezone_name

    _render()
    assert active_timezone_name() == "America/New_York"


@override_settings(USE_TZ=False, TIME_ZONE="America/New_York")
def test_use_tz_false_clears_the_zone_rather_than_leaving_a_stale_one():
    # The ordering hazard: a render under ``USE_TZ=True`` leaves a zone set on
    # this thread, and the thread is reused. If the ``USE_TZ=False`` path only
    # skipped the write instead of clearing, the stale zone would keep
    # converting. Set one first so the test can tell "cleared" from "never set".
    from djust._rust import active_timezone_name, set_active_timezone

    set_active_timezone("America/New_York")
    assert "2026-08-22 23:30" in _render()
    assert active_timezone_name() is None


# ---------------------------------------------------------------------------
# The second render path. `SimpleLiveView` shares no base class with
# `RustBridgeMixin` — it calls `render_template_with_dirs` directly — so a fix
# that lived on the mixin would have covered `LiveView` and silently missed
# this one. That is the #1646 twin this release keeps closing; both now call
# the same `djust.render_env.apply_render_env`.
# ---------------------------------------------------------------------------


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_the_simple_live_view_path_converts_too():
    """The second render path, now reachable (#2219).

    This test used to assert the OPPOSITE — that ``get_context_data`` raises —
    because ``simple_live_view`` could not render at all: it walked
    ``dir(self)`` and ``getattr``-ed Django's ``View.as_view``, a
    ``classonlymethod`` that raises on an instance. It was written to fail the
    moment someone fixed the module, which is the signal to put the real
    behavioural case here. #2219 fixed it, so here it is.

    Worth keeping in THIS file rather than moving: that module shares no base
    with ``RustBridgeMixin`` and renders through ``render_template_with_dirs``,
    so it is a genuine second render path and would happily render every
    timestamp in UTC on its own.
    """
    from djust.simple_live_view import SimpleLiveView

    class _SimpleStamp(SimpleLiveView):
        template = '<div>{{ created|date:"Y-m-d H:i" }}</div>'

        def mount(self, request, **kwargs):
            self.created = AWARE_SUMMER

    view = _SimpleStamp()
    view.mount(RequestFactory().get("/"))
    out = view.render_template()
    assert "2026-08-22 19:30" in out, f"expected the New York time, got {out!r}"


def test_both_render_paths_call_the_same_timezone_function():
    """Structural pin: one function, not two copies that can drift.

    A behavioural test proves each path converts today. It cannot prove they
    still SHARE the mechanism tomorrow — someone could inline a second copy into
    either and both tests would stay green while the two drifted apart. This
    asserts the single source of truth directly (#1125: pin the set, not a
    floor).

    It has already earned that: the set was two entries until #2223, when
    ``template/rendering.py`` — the Django-template BACKEND, a genuine
    top-level render path — turned out to be unwired and rendering both the
    UTC timestamps and the unseparated numbers the other two paths had been
    fixed for.

    ``components/base.py`` JOINED the set in #2539 (ADR-027 movement 2), and
    the reversal is recorded rather than quietly made. The earlier reading was
    that a component render always has an enclosing top-level render to
    inherit a correct thread-local from, so pushing again costs ~12us against
    a ~15us small render — ~78% overhead for no gain. Two things changed it:

    * a ``Component`` rendered OUTSIDE any djust render — from a plain Django
      view, or as the first thing a fresh ``sync_to_async`` worker thread does
      — never had an enclosing render to inherit from, so it was already
      rendering UTC timestamps and unseparated numbers. That was a latent
      gap, not a design.
    * ADR-027's ``template_resolve_lazy`` is not a FORMATTING setting: an
      unwired thread resolves dotted lookups by a different mechanism than
      every other path on the same page. A wrong timezone is a wrong cell; a
      wrong resolution mechanism is the parallel-path drift (#1646) the whole
      movement exists to retire.

    The ~12us is therefore paid deliberately, on the component path only, and
    it is a per-render cost rather than a per-component-instance one. Still
    deliberately absent: the other nested ``_rust.render_template`` callers,
    which are reachable only from inside one of the four entries here.
    """
    import ast
    import pathlib

    import djust

    root = pathlib.Path(djust.__file__).parent
    callers = set()
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name == "render_env.py":
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - not our concern here
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "apply_render_env"
            ):
                callers.add(path.relative_to(root).as_posix())

    assert callers == {
        "components/base.py",
        "mixins/rust_bridge.py",
        "simple_live_view.py",
        "template/rendering.py",
    }, (
        "the set of render paths pushing per-render settings to Rust changed. Add the "
        "new path here once it calls apply_render_env() — and if a path "
        "was REMOVED from this set, check it did not grow its own private copy "
        f"instead. Found: {sorted(callers)}"
    )
