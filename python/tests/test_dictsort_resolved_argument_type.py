"""`dictsort` types a VARIABLE argument by what it resolves to.

Django's `_property_resolver(arg)` runs `float(arg)` on the resolved object, so
a context string `"3"` builds `itemgetter("3")` (a key lookup) and only an
`int` indexes. djust read the resolved text as if it were an unquoted literal,
so `{{ p|dictsort:k }}` with `k = "3"` sorted a list of strings by character
where Django's `str["3"]` raises `TypeError` and the filter renders `""`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

STRINGS = ["ba", "ab", "ca"]
DICTS = [{"3": "z", "name": "b"}, {"3": "a", "name": "a"}]


def render_both(source: str, ctx: dict[str, Any]) -> tuple[str, str]:
    django_out = DjangoTemplate(source).render(DjangoContext(ctx))
    djust_out = _rust.render_template(source, normalize_django_value(ctx))
    return django_out, djust_out


@pytest.mark.parametrize("filter_name", ["dictsort", "dictsortreversed"])
@pytest.mark.parametrize(
    ("value", "key"),
    [
        # The cells that diverged: a string, or a non-integer number, that
        # cannot index a `str`, so Django renders "".
        (STRINGS, "3"),
        (STRINGS, "0"),
        (STRINGS, Decimal("0")),
        (STRINGS, 1.5),
        # The cells that already agreed, kept so the fix cannot over-refuse:
        # an `int` indexes, a `bool` is an `int`, and a string key looks up a
        # dict.
        (STRINGS, 1),
        (STRINGS, 0),
        (STRINGS, True),
        (DICTS, "3"),
        (DICTS, "name"),
    ],
)
def test_a_variable_argument_agrees_with_django(filter_name: str, value: Any, key: Any) -> None:
    source = "{{ p|%s:k }}" % filter_name
    django_out, djust_out = render_both(source, {"p": value, "k": key})
    assert djust_out == django_out, (key, django_out, djust_out)


def test_the_unresolvable_cells_render_nothing() -> None:
    """Non-vacuity: the first rows really are Django's `""`."""
    for key in ("3", "0", Decimal("0"), 1.5):
        django_out, djust_out = render_both("{{ p|dictsort:k }}", {"p": STRINGS, "k": key})
        assert django_out == djust_out == "", key


def test_a_literal_argument_is_unchanged() -> None:
    """An unquoted integer literal still indexes, a quoted one still looks up."""
    django_out, djust_out = render_both("{{ p|dictsort:0 }}", {"p": STRINGS})
    assert djust_out == django_out != ""
    django_out, djust_out = render_both('{{ p|dictsort:"0" }}', {"p": STRINGS})
    assert django_out == djust_out == ""
