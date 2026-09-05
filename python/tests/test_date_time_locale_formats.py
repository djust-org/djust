"""Native date/time formatting reads Django's active language at render time."""

from datetime import datetime, time

import pytest
from django.template import Context, Engine
from django.utils import translation

from djust.template import DjustTemplateBackend


@pytest.fixture(autouse=True)
def restore_native_render_environment():
    """Language overrides restore Django state; restore native state as well."""
    from djust.render_env import apply_render_env

    yield
    apply_render_env()


@pytest.mark.parametrize("language", ["en", "fr", "de", "es", "pl"])
@pytest.mark.parametrize(
    "expression,value",
    [
        ("value|date", datetime(2008, 1, 1, 16, 25)),
        ('value|date:""', datetime(2008, 1, 1)),
        ('value|date:"DATE_FORMAT"', datetime(2008, 1, 1)),
        ('value|date:"SHORT_DATE_FORMAT"', datetime(2008, 1, 1)),
        ('value|date:"D l F E M b N"', datetime(2008, 1, 1)),
        ("value|time", time(16, 25)),
        ('value|time:"E"', time(16, 25)),
        ('value|time:"TIME_FORMAT"', time(16, 25)),
        ('value|time:"P a A"', time(12)),
        ('value|time:"P a A"', time(0)),
    ],
)
def test_locale_formats_match_django(language, expression, value):
    source = "{{ " + expression + " }}"
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with translation.override(language):
        expected = Engine().from_string(source).render(Context({"value": value}))
        assert backend.from_string(source).render({"value": value}) == expected


def test_cached_template_uses_current_language():
    source = "{{ value|date }}|{{ value|time }}"
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    template = backend.from_string(source)
    reference = Engine().from_string(source)
    context = {"value": datetime(2008, 1, 1, 16, 25)}
    for language in ["en", "fr", "de", "fr", "en"]:
        with translation.override(language):
            assert template.render(context) == reference.render(Context(context))


def test_language_blocks_use_the_active_date_dictionary():
    source = (
        '{% load i18n %}{{ value|date:"E" }}|'
        '{% language "pl" %}{{ value|date }}{% endlanguage %}|'
        '{{ value|date:"E" }}'
    )
    context = {"value": datetime(2008, 1, 1)}
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with translation.override("en"):
        expected = (
            Engine(libraries={"i18n": "django.templatetags.i18n"})
            .from_string(source)
            .render(Context(context))
        )
        assert backend.from_string(source).render(context) == expected
