"""Django's membership, with, and isolated-include semantics."""

import pytest
from django.template import Context, Engine, TemplateSyntaxError

from djust import _rust
from djust.template import DjustTemplateBackend


@pytest.mark.parametrize("operator", ["in", "not in"])
@pytest.mark.parametrize(
    "needle,haystack",
    [
        (1, [1, 2]),
        (3, [1, 2]),
        ("x", "abc"),
        ("a", "abc"),
        ("a", {"a": 1}),
        ("b", {"a": 1}),
        ([], {"a": 1}),
        (1, None),
        (1, "abc"),
    ],
)
def test_membership(operator, needle, haystack):
    source = "{% if needle " + operator + " haystack %}yes{% else %}no{% endif %}"
    context = {"needle": needle, "haystack": haystack}
    assert _rust.render_template(source, context) == Engine().from_string(source).render(
        Context(context)
    )


@pytest.mark.parametrize(
    "assignments",
    ["x as a", "x as a and y as b", "a=x b=y", "x|upper as a", "'x=y' as a", "x as a and"],
)
def test_with_assignments(assignments):
    source = "{% with " + assignments + " %}{{ a }}:{{ b }}{% endwith %}|{{ a }}"
    context = {"x": "hello", "y": "world", "a": "outside"}
    assert _rust.render_template(source, context) == Engine().from_string(source).render(
        Context(context)
    )


@pytest.mark.parametrize(
    "assignments", ["a=x junk", "x as a y as b", "x as a and y", "a=x and b=y"]
)
def test_with_rejects_unused_arguments(assignments):
    source = "{% with " + assignments + " %}{% endwith %}"
    with pytest.raises(TemplateSyntaxError):
        Engine().from_string(source)
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with pytest.raises(TemplateSyntaxError):
        backend.from_string(source)


@pytest.mark.parametrize("invalid", ["INVALID", "missing:%s"])
def test_include_only_preserves_invalid_variable_setting(tmp_path, invalid):
    (tmp_path / "child.html").write_text("{{ outer }}|{{ value }}|{{ absent }}")
    source = '{% include "child.html" with value="bound" only %}'
    backend = DjustTemplateBackend(
        {
            "NAME": "test",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
            "OPTIONS": {"string_if_invalid": invalid},
        }
    )
    django = Engine(dirs=[str(tmp_path)], string_if_invalid=invalid)
    context = {"outer": "must not leak"}
    assert backend.from_string(source).render(context) == django.from_string(source).render(
        Context(context)
    )
