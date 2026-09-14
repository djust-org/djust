"""Immutable temporal lookup reuse must preserve live Django semantics."""

import datetime as dt

import pytest
from django.template import Context, Engine
from django.utils import timezone

from djust import _rust
from djust.template.backend import DjustTemplateBackend


def render(mode, source, context):
    if mode == "direct":
        return _rust.render_template(source, context)
    if mode == "backend":
        backend = DjustTemplateBackend(
            {"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        return backend.from_string(source).render(context)
    view = _rust.RustLiveView(source)
    for key, value in context.items():
        view.set_state(key, value)
    return view.render()


@pytest.mark.parametrize("mode", ["direct", "backend", "view"])
@pytest.mark.parametrize(
    "value,expression",
    [
        (dt.datetime(2026, 9, 13, 12, 34, 56, 123456), 'value|date:"Y-m-d H:i:s.u"'),
        (dt.date(2026, 9, 13), 'value|date:"Y-m-d"'),
        (dt.time(12, 34, 56, 123456), 'value|time:"H:i:s.u"'),
        (dt.timedelta(days=-2, seconds=31), "value.total_seconds"),
        (dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc), 'value|date:"c T"'),
    ],
)
def test_repeated_temporal_lookup_matches_django(mode, value, expression):
    source = "{% for row in rows %}{{ " + expression + " }};{% endfor %}"
    context = {"value": value, "rows": range(4)}
    expected = Engine().from_string(source).render(Context(context))
    assert render(mode, source, context) == expected


class MutableDate(dt.date):
    label = "first"

    def isoformat(self):
        return self.label


class MutableZone(dt.tzinfo):
    offset = 0

    def utcoffset(self, value):
        return dt.timedelta(hours=self.offset)

    def dst(self, value):
        return dt.timedelta(0)

    def tzname(self, value):
        return "custom"


def test_subclass_methods_remain_live_across_renders():
    value = MutableDate(2026, 9, 13)
    view = _rust.RustLiveView("{{ value.isoformat }}")
    view.set_state("value", value)
    assert view.render() == "first"
    value.label = "second"
    assert view.render() == "second"


def test_custom_timezone_is_not_reused():
    zone = MutableZone()
    value = dt.datetime(2026, 9, 13, 12, tzinfo=zone)
    source = '{{ value|date:"c" }}'
    view = _rust.RustLiveView(source)
    view.set_state("value", value)
    with timezone.override(dt.timezone.utc):
        for offset in (0, 3, -4):
            zone.offset = offset
            expected = Engine().from_string(source).render(Context({"value": value}, use_tz=False))
            assert view.render() == expected


@pytest.mark.parametrize("failure", [False, True])
def test_temporal_reuse_still_applies_protection_floor(monkeypatch, failure):
    import djust.serialization as serialization

    value = dt.date(2026, 9, 13)
    view = _rust.RustLiveView('{{ value|date:"Y" }}')
    view.set_state("value", value)
    original = serialization._protect_sidecar_value
    seen = []

    def protect(obj):
        if obj is value:
            seen.append(obj)
            if failure:
                raise RuntimeError("protection unavailable")
            return dt.date(2030, 1, 1)
        return original(obj)

    monkeypatch.setattr(serialization, "_protect_sidecar_value", protect)
    assert view.render() == ("" if failure else "2030")
    assert seen


def test_bare_subclass_conversion_remains_live():
    value = MutableDate(2026, 9, 13)
    source = '{{ value|json_script:"date" }}'
    view = _rust.RustLiveView(source)
    view.set_state("value", value)
    for label in ("first", "second"):
        value.label = label
        expected = Engine().from_string(source).render(Context({"value": value}))
        assert view.render() == expected


def test_naive_timestamp_lookup_observes_process_timezone(monkeypatch):
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("process timezone switching is unavailable")
    value = dt.datetime(2026, 9, 13, 12)
    source = '{{ value.timestamp|floatformat:"0" }}'
    view = _rust.RustLiveView(source)
    view.set_state("value", value)
    try:
        with monkeypatch.context() as patch:
            for zone in ("UTC", "Etc/GMT+5"):
                patch.setenv("TZ", zone)
                time.tzset()
                expected = Engine().from_string(source).render(Context({"value": value}))
                assert view.render() == expected
    finally:
        time.tzset()
