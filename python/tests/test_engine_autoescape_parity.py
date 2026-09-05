"""Engine and Context autoescape policy matches Django across render entry points."""

import pytest
from django.template import Context, Engine
from djust import _rust
from djust.template import DjustTemplateBackend

SOURCES = [
    "{% load i18n %}{% translate value %}",
    "{% load i18n %}{% blocktranslate %}{{ value }}{% endblocktranslate %}",
    "{{ value }}",
    "{{ value|upper }}",
    "{{ value|linebreaksbr }}",
    "{% firstof value %}",
    "{% cycle value other %}",
    "{% autoescape on %}{{ value }}{% endautoescape %}|{{ value }}",
    "{% autoescape off %}{{ value }}{% endautoescape %}|{{ value }}",
    "{% with x=value %}{{ x }}{% endwith %}|{{ value }}",
    '{% include "part.html" with x=value only %}|{{ value }}',
]


@pytest.mark.parametrize("on", [False, True])
@pytest.mark.parametrize("entry", ["backend", "rust", "dirs"])
@pytest.mark.parametrize("source", SOURCES)
def test_engine_autoescape_matches_django(tmp_path, on, entry, source):
    if entry == "rust" and "include" in source:
        pytest.skip("standalone Rust entry has no loader")
    (tmp_path / "part.html").write_text("{{ x }}")
    data = {"value": "<b>A&B</b>", "other": "<i>other</i>", "autoescape": not on}
    expected = (
        Engine(dirs=[tmp_path], autoescape=on, libraries={"i18n": "django.templatetags.i18n"})
        .from_string(source)
        .render(Context(data, autoescape=on))
    )
    if entry == "backend":
        backend = DjustTemplateBackend(
            {
                "NAME": "autoescape",
                "DIRS": [tmp_path],
                "APP_DIRS": False,
                "OPTIONS": {"autoescape": on},
            }
        )
        actual = backend.from_string(source).render(data)
    elif entry == "rust":
        actual = _rust.render_template(source, data, autoescape=on)
    else:
        actual = _rust.render_template_with_dirs(source, data, [str(tmp_path)], autoescape=on)
    assert actual == expected


@pytest.mark.parametrize("engine_on", [False, True])
@pytest.mark.parametrize("context_on", [False, True])
def test_explicit_context_overrides_engine_default(engine_on, context_on):
    data = {"value": "<b>A&B</b>", "autoescape": not context_on}
    source = "{{ value }}"
    backend = DjustTemplateBackend(
        {"NAME": "autoescape", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"autoescape": engine_on}}
    )
    expected = (
        Engine(autoescape=engine_on)
        .from_string(source)
        .render(Context(data, autoescape=context_on))
    )
    assert backend.from_string(source).render(Context(data, autoescape=context_on)) == expected


def test_plain_render_default_stays_escaped_after_explicit_off():
    source = "{{ value }}"
    data = {"value": "<b>A&B</b>", "autoescape": False}
    assert _rust.render_template(source, data, autoescape=False) == "<b>A&B</b>"
    assert _rust.render_template(source, data) == "&lt;b&gt;A&amp;B&lt;/b&gt;"
    assert _rust.render_template_with_dirs(source, data, []) == "&lt;b&gt;A&amp;B&lt;/b&gt;"


def test_dict_attributes_cannot_override_engine_policy():
    from django.template.backends.django import DjangoTemplates

    class ContextData(dict):
        autoescape = False

    data = ContextData(value="<b>A&B</b>")
    params = {"NAME": "autoescape", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    expected = DjangoTemplates(params).from_string("{{ value }}").render(data)
    assert DjustTemplateBackend(params).from_string("{{ value }}").render(data) == expected
