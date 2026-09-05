"""Firstof binds the safety of its output, including the plain empty fallback."""

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe
from djust import _rust
from djust.template import DjustTemplateBackend

SOURCES = [
    "{% firstof missing as x %}{{ head|add:x }}",
    "{% firstof zero false missing as x %}{{ head|add:x }}",
    "{% autoescape off %}{% firstof value as x %}{% autoescape on %}{{ x }}{% endautoescape %}{% endautoescape %}",
    "{% autoescape off %}{% firstof value as x %}{{ head|add:x }}{% endautoescape %}",
    "{% firstof value as x %}{{ x }}|{{ head|add:x }}",
]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("value", ["", mark_safe(""), "<em>A&B</em>", mark_safe("<b>A&B</b>")])
@pytest.mark.parametrize("entry", ["backend", "rust", "dirs"])
def test_bound_output_safety_matches_django(source, value, entry):
    context = {"value": value, "head": mark_safe("<h>&</h>"), "zero": 0, "false": False}
    expected = Engine().from_string(source).render(Context(context))
    if entry == "backend":
        backend = DjustTemplateBackend(
            {"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        actual = backend.from_string(source).render(context)
    elif entry == "rust":
        actual = _rust.render_template(source, context)
    else:
        actual = _rust.render_template_with_dirs(source, context, [])
    assert actual == expected
