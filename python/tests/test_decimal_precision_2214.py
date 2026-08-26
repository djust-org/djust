"""`Decimal` must reach the template and the wire with its digits intact (#2214).

The bug: `serialize_python_value`'s `type_name == "Decimal"` branch was dead,
because `extract::<f64>()` ran above it and PyO3's `f64` extraction goes through
`PyFloat_AsDouble`, which honours `Decimal.__float__`. Every `Decimal` became a
binary double before the branch could see it. `DecimalField` is Django's money
type; a binary double is what it exists to avoid.

The fix is a `Value::Decimal(String)` variant carrying the exact digits, NOT the
one-line branch move the issue suggested. That was measured and regresses two
template behaviours: the serialized value is written back into the template
context, so the Rust renderer sees the same value the wire does, and as a plain
string `{{ p|floatformat }}` stops rounding and `{% if p > 10 %}` compares
lexically.

**The template cases are a differential against real Django**, not a table of
expected strings written by hand. A curated table samples the axis you thought
of; Django is importable here, so there is no reason to guess (v1.1.1-2 retro).
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

#: The value under test plus the neighbours a filter or comparison might reach.
CTX = {
    "p": Decimal("19.99"),
    "huge": Decimal("12345678901234567890.123456789"),
    "zero": Decimal("0.00"),
    "neg": Decimal("-3.5"),
}

#: Every idiom a `DecimalField` plausibly meets in a template. `{{ p }}` and
#: `{{ huge }}` are the ones the bug broke; the rest are the guard that fixing
#: them did not break anything else.
TEMPLATES = [
    "{{ p }}",
    "{{ huge }}",
    "{{ neg }}",
    "{{ zero }}",
    "{{ p|floatformat }}",
    "{{ p|floatformat:2 }}",
    "{{ p|floatformat:0 }}",
    "{{ neg|floatformat:1 }}",
    "{{ p|add:1 }}",
    "{{ p|stringformat:'.3f' }}",
    "{{ p|stringformat:'d' }}",
    "{% if p > 10 %}BIG{% else %}small{% endif %}",
    "{% if p < 10 %}small{% else %}BIG{% endif %}",
    "{% if neg < 0 %}NEG{% else %}POS{% endif %}",
    "{% if zero %}T{% else %}F{% endif %}",
    "{% if p %}T{% else %}F{% endif %}",
]


@pytest.mark.parametrize("source", TEMPLATES)
def test_a_decimal_renders_exactly_as_django_renders_it(source: str) -> None:
    expected = DjangoTemplate(source).render(DjangoContext(CTX))
    assert _rust.render_template(source, CTX) == expected, (
        f"template {source!r} diverged from Django.\n"
        f"  django: {expected!r}\n"
        f"  djust:  {_rust.render_template(source, CTX)!r}"
    )


def test_the_differential_would_catch_a_regression() -> None:
    """Gate-off for the harness itself (#1468).

    A differential is only worth its runtime if a wrong answer fails it. Feeding
    Django a different value must diverge — otherwise the comparison above is
    passing for some reason other than agreement.
    """
    source = "{{ p }}"
    wrong = DjangoTemplate(source).render(DjangoContext({"p": Decimal("19.98")}))
    assert _rust.render_template(source, CTX) != wrong


# ---------------------------------------------------------------------------
# The wire — what the browser actually receives.
# ---------------------------------------------------------------------------


def test_the_exact_digits_reach_the_wire() -> None:
    from djust._rust import serialize_context

    out = serialize_context(CTX)
    parsed = json.loads(out) if isinstance(out, (str, bytes)) else out

    # A JSON *string*, matching `DjangoJSONEncoder.default`. This is a wire-format
    # change: a JSON number cannot carry the precision, so there is no version of
    # the fix that keeps a number AND the digits.
    assert parsed["p"] == "19.99"
    assert isinstance(parsed["p"], str)

    # The damage the bug actually did: 29 significant digits do not fit in a
    # binary double. Pre-fix this arrived as 1.2345678901234567e+19.
    assert parsed["huge"] == "12345678901234567890.123456789"
    assert "e+" not in parsed["huge"].lower()


def test_the_python_converters_stay_consistent_with_each_other() -> None:
    """The Python pair is deliberately NOT fixed here, and must stay a pair.

    `normalize_django_value` is documented as the fast path for djust's own
    encoder, and `TestParityWithJSONRoundtrip` pins them equal. A first pass at
    #2214 changed only the encoder to `str` — free parity with Django on a
    JSON-only path, or so it looked — and split that invariant; the parity suite
    caught it.

    So both stay on `float`, and this asserts they agree rather than leaving the
    coupling implicit. Fixing them needs a consumer audit (`runtime.py` dumps
    state with no encoder), which is why it is not in the Rust fix.
    """
    from djust.serialization import DjangoJSONEncoder as DjustEncoder
    from djust.serialization import normalize_django_value

    for value in (Decimal("19.99"), Decimal("12345678901234567890.123456789")):
        assert normalize_django_value(value) == json.loads(json.dumps(value, cls=DjustEncoder))


def test_uuid_still_stringifies() -> None:
    """The branch the fix split apart still handles its other half.

    `UUID` shared the dead branch and was never affected — it is not
    float-convertible, so it reached the check. Pinned so narrowing the branch
    to UUID-only did not drop it.
    """
    import uuid

    from djust._rust import serialize_context

    out = serialize_context({"u": uuid.UUID(int=1)})
    parsed = json.loads(out) if isinstance(out, (str, bytes)) else out
    assert parsed["u"] == str(uuid.UUID(int=1))


# ---------------------------------------------------------------------------
# Stated limits. Both are pre-existing and neither is introduced by the variant
# — a `Decimal` was a `Value::Float` before, so it behaved this way already.
# Written down so they are a decision rather than an accident (#1867).
# ---------------------------------------------------------------------------


def test_decimal_equality_against_a_float_literal_diverges_from_django() -> None:
    """`{% if p == 19.99 %}`: Django says False, djust says True.

    Python compares Decimal to float EXACTLY, and the float nearest `19.99` is
    `19.989999999999998863...`, so Django's answer is False. djust compares via
    `f64` and answers True.

    Deliberate. The variant's contract is exact *rendering and transport* with
    `as_f64()` for arithmetic — matching Django here needs arbitrary-precision
    comparison, i.e. a new dependency, for an idiom (`== <float literal>`) that
    is rare and whose Django answer is surprising to most readers. Comparison
    against an INTEGER, which is the common case, agrees with Django and is
    pinned in the differential above.
    """
    source = "{% if p == 19.99 %}EQ{% else %}NE{% endif %}"
    assert DjangoTemplate(source).render(DjangoContext(CTX)) == "NE"
    assert _rust.render_template(source, CTX) == "EQ"


def test_two_decimals_differing_beyond_f64_compare_equal() -> None:
    """The same limit, in its sharpest form: comparison is f64-precision.

    Rendering and transport keep every digit; `<`, `>` and `==` do not. Pinned
    so a future reader knows the boundary rather than discovering it.
    """
    ctx = {
        "a": Decimal("1.00000000000000000001"),
        "b": Decimal("1.00000000000000000002"),
    }
    assert _rust.render_template("{% if a == b %}EQ{% else %}NE{% endif %}", ctx) == "EQ"
    # ...but neither value lost a digit on the way out.
    assert _rust.render_template("{{ a }}", ctx) == "1.00000000000000000001"
