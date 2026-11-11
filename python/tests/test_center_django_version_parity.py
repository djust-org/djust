"""Django 6 changed center's odd-margin alignment to format semantics."""

import pytest
from django.template import Context, Engine

from djust._rust import render_template


@pytest.mark.parametrize(
    "value,width", [("ab", 3), ("ab", 5), ("abcd", 5), ("a", 4), ("é猫", 3), ("ab", -1), ("abc", 3)]
)
def test_center_matches_installed_django(value, width):
    source = "{{ value|center:width }}"
    context = {"value": value, "width": width}
    expected = Engine().from_string(source).render(Context(context))
    assert render_template(source, context) == expected
