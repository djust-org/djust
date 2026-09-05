"""Static and now use Django's node semantics on Python-backed render paths."""

from datetime import datetime, timezone

import pytest
from django.template import Context, Engine, TemplateSyntaxError
from django.utils import translation

from djust import _rust
from djust.template import DjustTemplateBackend


@pytest.fixture
def backend():
    return DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})


def expected(source, context):
    return (
        Engine(libraries={"static": "django.templatetags.static"})
        .from_string(source)
        .render(Context(context))
    )


@pytest.mark.parametrize(
    "tag",
    [
        '{% static "admin/base.css" %}',
        "{% static path %}",
        "{% static path|lower %}",
        '{% static "special?chars&quoted.html" %}',
        "{% static path as asset %}before[{{ asset }}]after",
        '{% static "admin/base.css" as asset %}{{ asset }}',
        "{% static path as asset %}{% with alias=asset %}{{ alias }}{% endwith %}",
    ],
)
def test_static_matches_django(tag, backend, settings):
    settings.STATIC_URL = "/cdn/"
    source = "{% load static %}" + tag
    context = {"path": "ADMIN/base.css"}
    reference = expected(source, context)
    assert backend.from_string(source).render(context) == reference
    assert _rust.render_template(source, context) == reference


@pytest.mark.parametrize("autoescape", ["on", "off"])
def test_static_storage_result_escapes_once(autoescape, backend, monkeypatch):
    from django.contrib.staticfiles.storage import staticfiles_storage

    monkeypatch.setattr(staticfiles_storage, "url", lambda path: '/assets/"file"?a=1&b=2')
    source = (
        "{% load static %}{% autoescape "
        + autoescape
        + ' %}{% static "file" %}|{% static "file" as asset %}{{ asset }}{% endautoescape %}'
    )
    assert backend.from_string(source).render({}) == expected(source, {})


@pytest.fixture
def fixed_clock(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2024, 1, 2, 15, 4, 5, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr("django.template.defaulttags.datetime", FixedDatetime)


@pytest.mark.parametrize(
    "fmt", ["DATE_FORMAT", "DATETIME_FORMAT", "SHORT_DATE_FORMAT", "Y-m-d H:i:s"]
)
@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("asvar", [False, True])
def test_now_formats_and_bindings(fmt, language, asvar, backend, fixed_clock):
    source = '{% now "' + fmt + ('" as value %}[{{ value }}]' if asvar else '" %}')
    with translation.override(language):
        reference = expected(source, {})
        assert backend.from_string(source).render({}) == reference
        assert _rust.render_template(source, {}) == reference


@pytest.mark.parametrize(
    "source", ["{% now %}", '{% now "Y" extra %}', "{% load static %}{% static %}"]
)
def test_bad_arguments_fail_at_compile_time(source, backend):
    with pytest.raises(TemplateSyntaxError) as reference:
        Engine(libraries={"static": "django.templatetags.static"}).from_string(source)
    with pytest.raises(TemplateSyntaxError) as actual:
        backend.from_string(source)
    assert str(actual.value) == str(reference.value)
