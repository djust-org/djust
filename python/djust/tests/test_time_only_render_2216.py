"""A `datetime.time` in LiveView state must render, not echo (#2216).

The filter-level table lives in
``crates/djust_templates/tests/test_time_only_filters_2216.rs``, pinned against
a live Django 5.2 render. These cases exist for one thing that table cannot
check: that the **serializer emits the shape the new parse branch accepts**.

The Rust tests hand the filter a string, so they would stay green if
``normalize_django_value`` sent a ``time`` across as, say, ``str(t)`` with a
different separator, or wrapped it. Reproduction fidelity: the harness has to
exercise the real path, not a convenient proxy.
"""

import datetime as dt

from django.test import RequestFactory, override_settings

from djust import LiveView


class _TimeView(LiveView):
    template = '<div dj-id="0">{{ opens|time:"H:i" }}</div>'
    _value = dt.time(9, 5, 30)

    def mount(self, request, **kwargs):
        self.opens = type(self)._value


def _render(value, template=None):
    attrs = {"_value": value}
    if template is not None:
        attrs["template"] = template
    view = type("_V", (_TimeView,), attrs)()
    request = RequestFactory().get("/")
    view.mount(request)
    view.request = request
    return view.render()


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_a_time_field_value_formats_end_to_end():
    # Django renders '09:05'. Pre-fix djust rendered '09:05:30' — the serialized
    # input, echoed back, because no parse branch matched it.
    out = _render(dt.time(9, 5, 30))
    assert "09:05<" in out, f"expected a formatted time, got {out!r}"
    assert "09:05:30" not in out, "the raw serialized value leaked into the page"


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_a_bare_time_is_not_shifted_by_the_active_timezone():
    # It has no instant to convert, and the epoch date it is anchored on inside
    # the formatter must never influence the result. Under New York this would
    # read 04:05 if the anchor leaked.
    assert "09:05<" in _render(dt.time(9, 5, 30))


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_a_date_code_on_a_time_renders_empty_like_django():
    out = _render(dt.time(9, 5, 30), '<div dj-id="0">[{{ opens|date:"Y-m-d" }}]</div>')
    assert "[]" in out, f"Django renders '' for a date code on a time; got {out!r}"


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
def test_a_datetime_in_the_same_view_still_converts():
    # Guard: the new time-only branch must not have captured datetimes on its
    # way past. 23:30 UTC is 19:30 in New York (#2209).
    out = _render(
        dt.datetime(2026, 8, 22, 23, 30, tzinfo=dt.timezone.utc),
        '<div dj-id="0">{{ opens|date:"Y-m-d H:i" }}</div>',
    )
    assert "2026-08-22 19:30" in out
