"""The Rust for-loop context follows the installed Django version."""

import pytest
from django.template import Context, Engine

from djust._rust import render_template


@pytest.mark.parametrize(
    ("source", "values"),
    [
        ("{% for x in xs %}{{ forloop.length }}:{{ x }};{% endfor %}", [1, 2, 3]),
        ("{% for x in xs reversed %}{{ forloop.length }}:{{ x }};{% endfor %}", [1, 2, 3]),
        (
            "{% for x in xs %}{% for y in xs %}"
            "{{ forloop.parentloop.length }}.{{ forloop.length }};"
            "{% endfor %}{% endfor %}",
            [1, 2],
        ),
        ("{% for x in xs %}{{ forloop }}{% endfor %}", [1]),
        ("{% for x in xs %}{{ forloop.length }}{% empty %}empty{% endfor %}", []),
    ],
)
def test_forloop_context_matches_installed_django(source, values):
    context = {"xs": values}
    expected = Engine().from_string(source).render(Context(context))
    assert render_template(source, context) == expected
