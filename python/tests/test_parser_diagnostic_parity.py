"""Diagnostics retain Django's distinctions and the offending token text."""

import pytest
from django.template import Engine, TemplateSyntaxError

from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "source",
    [
        "{% include %}",
        '{% extends "base" %}{% extends "base" %}',
        '{% extends "base" %}plain text{% extends "other" %}',
        '{% extends "base" %}{# comment #}{% extends "other" %}',
        "{% if foo %}{% else if bar %}{% endif %}",
        "\n{% if foo %}\n{% else    if   bar %}{% endif %}",
    ],
)
def test_parser_diagnostic_matches_django(source):
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with pytest.raises(TemplateSyntaxError) as expected:
        Engine().from_string(source)
    with pytest.raises(TemplateSyntaxError) as actual:
        backend.from_string(source)
    # The Rust error wrapper retains its prefix; the Django message is intact.
    assert str(expected.value) in str(actual.value)


def test_malformed_else_debug_span_points_to_clause():
    source = "{% if foo %}\n{% else if bar %}\n{% endif %}"
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"debug": True}}
    )
    with pytest.raises(TemplateSyntaxError) as actual:
        backend.from_string(source)
    assert actual.value.template_debug["during"] == "{% else if bar %}"
    assert actual.value.template_debug["line"] == 2


@pytest.mark.parametrize("comment", ["{# inline #}", "{% comment %}block{% endcomment %}"])
def test_comment_kind_controls_extends_placement(tmp_path, comment):
    (tmp_path / "base").write_text("parent")
    source = comment + '{% extends "base" %}'
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    )
    if comment.startswith("{#"):
        from django.template import Context

        expected = Engine(dirs=[str(tmp_path)]).from_string(source).render(Context())
        assert backend.from_string(source).render({}) == expected == "parent"
    else:
        with pytest.raises(TemplateSyntaxError):
            Engine().from_string(source)
        with pytest.raises(TemplateSyntaxError):
            backend.from_string(source)
