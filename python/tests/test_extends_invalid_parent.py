"""Missing/falsy parent names follow Django's strict expression semantics."""

import pytest
from django.template import Context, Engine, TemplateSyntaxError

from djust.template import DjustTemplateBackend


def backend(tmp_path, invalid=""):
    return DjustTemplateBackend(
        {
            "NAME": "test",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
            "OPTIONS": {"string_if_invalid": invalid},
        }
    )


@pytest.mark.parametrize(
    "operand,context",
    [
        ("missing", {}),
        ("parent", {"parent": ""}),
        ("parent", {"parent": None}),
        ("parent", {"parent": False}),
        ("parent", {"parent": 0}),
        ("parent", {"parent": []}),
        ("parent", {"parent": {}}),
        ("''", {}),
        ("0", {}),
        ("False", {}),
        ("None", {}),
        ("'x'|cut:'x'", {}),
        ('missing|default_if_none:"base.html"', {}),
    ],
)
def test_falsy_parent_error_matches_django(tmp_path, operand, context):
    source = "{% extends " + operand + " %}"
    with pytest.raises(TemplateSyntaxError) as expected:
        Engine().from_string(source).render(Context(context))
    with pytest.raises(TemplateSyntaxError) as actual:
        backend(tmp_path).from_string(source).render(context)
    assert str(actual.value) == str(expected.value)


@pytest.mark.parametrize("invalid", ["INVALID", "missing-%s.html"])
@pytest.mark.parametrize("operand", ["missing", 'missing|default:"base.html"', "missing|upper"])
def test_missing_parent_uses_invalid_marker_before_filters(tmp_path, invalid, operand):
    name = invalid.replace("%s", "missing")
    (tmp_path / name).write_text("marker parent")
    (tmp_path / "base.html").write_text("fallback parent")
    source = "{% extends " + operand + " %}"
    expected = (
        Engine(dirs=[str(tmp_path)], string_if_invalid=invalid)
        .from_string(source)
        .render(Context())
    )
    assert backend(tmp_path, invalid).from_string(source).render({}) == expected == "marker parent"


@pytest.mark.parametrize(
    "tag",
    [
        '{% with result=missing|default:"fallback" %}{{ result }}{% endwith %}',
        "{% with result=missing|upper %}{{ result }}{% endwith %}",
    ],
)
def test_strict_tag_expression_does_not_filter_invalid_marker(tmp_path, tag):
    expected = Engine(string_if_invalid="invalid-%s").from_string(tag).render(Context())
    assert backend(tmp_path, "invalid-%s").from_string(tag).render({}) == expected
