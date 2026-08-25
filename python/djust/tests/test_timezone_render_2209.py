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
