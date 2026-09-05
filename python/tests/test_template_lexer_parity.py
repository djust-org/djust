"""Literal source boundaries follow Django's lexer, including verbatim (#2626)."""

import pytest
from django.template import Context, Engine

from djust import _rust


@pytest.mark.parametrize(
    "source",
    [
        "{% verbatim %}{{bare   }}{% endverbatim %}",
        "{% verbatim %}é\n{{  x}} {# comment #}<Widget />{% endverbatim %}",
        "{% verbatim special %}Don't {% endverbatim %} yet{% endverbatim special %}",
        "{% verbatim special %}{# {% endverbatim special %} #}{{ x }}{% endverbatim special %}",
        "{% verbatim %}{{ '{% endverbatim %}' }}{% endverbatim %}",
        "{% verbatim %}{% verbatim %}{{ }}{% endverbatim %}{{ value }}",
        "{{ moo\n }}{{ value }}",
        "{% invalid\n tag %}{{ value }}",
        "{# hidden\n comment #}{{ value }}",
        "{{ broken\n {{ value }}",
    ],
)
def test_literal_source_matches_django(source):
    context = {"value": "after"}
    expected = Engine().from_string(source).render(Context(context))
    assert _rust.render_template(source, context) == expected
