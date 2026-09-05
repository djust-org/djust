"""Django's must-be-first diagnostics retain token spelling and origin."""

import pytest
from django.template import Engine, Origin, TemplateSyntaxError
from django.template.base import Template
from djust.template import DjustTemplateBackend
from djust.template.rendering import DjustTemplate


@pytest.mark.parametrize("name", [None, "index.html", "nested/ünicode.html", "quote'name.html"])
@pytest.mark.parametrize(
    "tag", ["{% extends 'base.html' %}", '{%   extends   "base.html"   %}', "{% extends parent %}"]
)
@pytest.mark.parametrize(
    "prefix", ["{% block content %}B{% endblock %}", "é🙂\n{% if yes %}Y{% endif %}\n"]
)
def test_first_tag_diagnostic(name, tag, prefix):
    source = prefix + tag
    origin = Origin(name=name or "<unknown source>", template_name=name)
    with pytest.raises(TemplateSyntaxError) as django_error:
        Template(source, origin=origin, name=name, engine=Engine(debug=True))
    backend = DjustTemplateBackend(
        {"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"debug": True}}
    )
    with pytest.raises(TemplateSyntaxError) as rust_error:
        DjustTemplate(source, backend, origin=origin if name else None)
    assert str(django_error.value) in str(rust_error.value)
    info = rust_error.value.template_debug
    assert info["during"] == tag
    assert info["line"] == prefix.count("\n") + 1
    assert info["start"] == len(prefix)
    assert info["end"] == len(source)


@pytest.mark.parametrize("debug", [False, True])
def test_string_template_origin_and_source(debug):
    source = "string template é🙂"
    expected = Engine(debug=debug).from_string(source)
    backend = DjustTemplateBackend(
        {"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"debug": debug}}
    )
    actual = backend.from_string(source)
    assert actual.origin.name == expected.origin.name
    assert actual.origin.loader_name == expected.origin.loader_name
    assert actual.origin.template_name == expected.origin.template_name
    assert actual.source == expected.source
