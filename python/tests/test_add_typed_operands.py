"""The add filter retains argument types through conversion and concatenation."""

from decimal import Decimal

import pytest
from django.template import Context, Engine

from djust import _rust


VALUES = [
    "",
    "text",
    "1",
    "1.5",
    16,
    1.5,
    True,
    None,
    [],
    [1, "<x>"],
    (),
    (1, "<x>"),
    {},
    Decimal("1.5"),
    10**70,
]


@pytest.mark.parametrize("lhs", VALUES)
@pytest.mark.parametrize("rhs", VALUES)
def test_resolved_add_operands(lhs, rhs):
    source = "{{ lhs|add:rhs }}"
    context = {"lhs": lhs, "rhs": rhs}
    expected = Engine().from_string(source).render(Context(context))
    assert _rust.render_template(source, context) == expected


@pytest.mark.parametrize("operand", ["16", "1.5", "1e2", "True", "None", '"16"', '"1.5"'])
@pytest.mark.parametrize("lhs", ["", "text", "1.5", 3, [1], (1,)])
def test_literal_add_operands(lhs, operand):
    source = "{{ lhs|add:" + operand + " }}"
    context = {"lhs": lhs}
    expected = Engine().from_string(source).render(Context(context))
    assert _rust.render_template(source, context) == expected


@pytest.mark.parametrize("lhs,rhs", [([1, 2], [3]), ((1, 2), (3,)), ([], []), ((), ())])
def test_add_output_retains_sequence_type(lhs, rhs):
    source = "{% with result=lhs|add:rhs %}{{ result|length }}|{{ result|pprint }}|{% for item in result %}{{ item }}{% endfor %}{% endwith %}"
    context = {"lhs": lhs, "rhs": rhs}
    expected = Engine().from_string(source).render(Context(context))
    assert _rust.render_template(source, context) == expected
