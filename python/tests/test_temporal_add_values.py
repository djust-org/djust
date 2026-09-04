"""Temporal addition retains Python types and uses Django output formatting."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.template import Context, Engine, Library
from django.utils import translation

from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "left,right",
    [
        (date(2000, 1, 1), timedelta(days=10)),
        (timedelta(days=10), date(2000, 1, 1)),
        (date(2020, 3, 1), timedelta(microseconds=-1)),
        (date.max, timedelta(days=1)),
        (date.min, timedelta(days=-1)),
        (datetime(2020, 2, 28, 23, 59, 59, 999999), timedelta(microseconds=2)),
        (datetime(2024, 3, 9, 12, tzinfo=ZoneInfo("America/New_York")), timedelta(days=1)),
        (datetime(2024, 11, 2, 12, tzinfo=ZoneInfo("America/New_York")), timedelta(days=1)),
        (datetime(2020, 1, 1, tzinfo=timezone(timedelta(hours=3), "TEST")), timedelta(days=2)),
        (timedelta(days=-1, microseconds=1), timedelta(seconds=2)),
        (timedelta.max, timedelta(days=1)),
        (date(2020, 1, 1), date(2020, 1, 2)),
        (date(2020, 1, 1), 1),
        (time(1, 2), timedelta(hours=1)),
    ],
)
@pytest.mark.parametrize(
    "source",
    [
        "{{ a|add:b }}",
        '{{ a|add:b|date:"c" }}',
        "{{ a|add:b|pprint }}",
    ],
)
@pytest.mark.parametrize("language", ["en", "de"])
def test_temporal_add_matches_django(left, right, source, language):
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with translation.override(language):
        expected = Engine().from_string(source).render(Context({"a": left, "b": right}))
        assert backend.from_string(source).render({"a": left, "b": right}) == expected


@pytest.mark.parametrize("use_l10n", [None, False, True])
@pytest.mark.parametrize("use_tz", [None, False, True])
@pytest.mark.parametrize(
    "scope", ["", "{% load l10n %}{% localize on %}", "{% load l10n %}{% localize off %}"]
)
def test_temporal_render_obeys_context_flags(use_l10n, use_tz, scope):
    value = datetime(2024, 6, 1, 18, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    source = scope + "{{ value }}" + ("{% endlocalize %}" if scope else "")
    engine = Engine(libraries={"l10n": "django.templatetags.l10n"})
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with translation.override("de"):
        context = Context({"value": value}, use_l10n=use_l10n, use_tz=use_tz)
        assert backend.from_string(source).render(context) == engine.from_string(source).render(
            context
        )


@pytest.mark.parametrize(
    "value",
    [
        date(2020, 1, 1),
        datetime(2024, 3, 9, 12, tzinfo=ZoneInfo("America/New_York")),
        datetime(2020, 1, 1, tzinfo=timezone(timedelta(hours=3), "TEST")),
        timedelta(days=-3, microseconds=9),
    ],
)
def test_temporal_add_survives_state_roundtrip(value):
    from djust._rust import RustLiveView

    source = '{{ value|add:delta }}|{{ value|add:delta|date:"c" }}|{{ value|add:delta|pprint }}'
    delta = timedelta(days=1)
    view = RustLiveView(source)
    view.set_state("value", value)
    view.set_state("delta", delta)
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
    expected = Engine().from_string(source).render(Context({"value": value, "delta": delta}))
    assert restored.render() == expected


def test_temporal_add_does_not_swallow_base_exception():
    class InterruptingDate(date):
        def __add__(self, other):
            raise KeyboardInterrupt("addition interrupted")

    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with pytest.raises(KeyboardInterrupt, match="addition interrupted"):
        backend.from_string("{{ value|add:delta }}").render(
            {"value": InterruptingDate(2020, 1, 1), "delta": timedelta(days=1)}
        )


register = Library()


@register.filter
def temporal_kind(value):
    return type(value).__name__


@register.filter
def temporal_plus(value, other):
    return value + other


@pytest.mark.parametrize(
    "value", [date(2020, 1, 1), datetime(2020, 1, 1), time(1, 2), timedelta(days=2)]
)
def test_custom_filters_receive_temporal_types(value):
    libraries = {"temporal_fixture": __name__}
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"libraries": libraries}}
    )
    source = "{% load temporal_fixture %}{{ value|temporal_kind }}"
    assert backend.from_string(source).render({"value": value}) == type(value).__name__


def test_custom_filter_temporal_result_and_argument_remain_typed():
    libraries = {"temporal_fixture": __name__}
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"libraries": libraries}}
    )
    source = "{% load temporal_fixture %}{{ value|temporal_plus:delta|temporal_kind }}|{{ value|temporal_plus:delta }}"
    context = {"value": date(2020, 1, 1), "delta": timedelta(days=2)}
    expected = Engine(libraries=libraries).from_string(source).render(Context(context))
    assert backend.from_string(source).render(context) == expected
