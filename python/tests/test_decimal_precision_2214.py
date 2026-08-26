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
import re
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


def test_a_huge_decimal_loses_precision_once_a_filter_does_arithmetic() -> None:
    """The f64 contract, at the boundary where it becomes visible.

    `{{ huge }}` is exact — rendering and transport keep every digit. The moment
    a filter computes, the value goes through `as_f64()` and Django's answer and
    djust's diverge. Pinned because the differential above only exercises
    `19.99`, where the limit never shows (#2240 review).
    """
    ctx = {"huge": Decimal("12345678901234567890.123456789")}
    assert _rust.render_template("{{ huge }}", ctx) == "12345678901234567890.123456789"

    for source in ("{{ huge|floatformat }}", "{{ huge|stringformat:'d' }}"):
        django_says = DjangoTemplate(source).render(DjangoContext(ctx))
        djust_says = _rust.render_template(source, ctx)
        assert djust_says != django_says, (
            f"{source} now agrees with Django ({django_says!r}) — if the "
            "arithmetic path gained real precision, update this limit."
        )


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


# ---------------------------------------------------------------------------
# Arms the first version of this file left untested (#2240 review).
#
# The review's gate-off ran each new arm's mutation against the FULL 10,395-case
# suite; eight survived green, including two of the five wildcard fallbacks the
# PR body highlighted as the audit's payoff. An arm with no test is an arm that
# can be deleted silently, which is the same class as a decorative pin (#1859).
# ---------------------------------------------------------------------------


def test_filesizeformat_treats_a_decimal_as_a_number() -> None:
    """Wildcard-arm coverage: without the Decimal arm the value returns unchanged.

    Compared against djust's own output for the equivalent int rather than
    against Django, because djust separates with a plain space where Django uses
    U+00A0 — a pre-existing divergence that affects ints and floats identically
    and is not this issue's (#1079).
    """
    as_int = _rust.render_template("{{ n|filesizeformat }}", {"n": 2048})
    assert _rust.render_template("{{ n|filesizeformat }}", {"n": Decimal("2048")}) == as_int
    assert as_int != "2048"


def test_dictsort_orders_decimals_numerically() -> None:
    """Wildcard-arm coverage: without it every pair compared Equal, i.e. no sort.

    Rendered directly rather than through `{% for %}`: iterating a *filtered*
    expression renders empty in djust for every element type, a pre-existing bug
    outside this issue (#1079). Four scrambled elements, because a stable sort
    leaves a two-element list alone whether it compares or not.
    """
    rows = [
        {"amt": Decimal("10.50")},
        {"amt": Decimal("2.25")},
        {"amt": Decimal("100.00")},
        {"amt": Decimal("0.99")},
    ]
    out = _rust.render_template("{{ rows|dictsort:'amt' }}", {"rows": rows})
    order = [m for m in re.findall(r"Decimal\(&#x27;([\d.]+)&#x27;\)", out)]
    assert order == ["0.99", "2.25", "10.50", "100.00"], out


def test_a_decimal_is_localized_like_a_number() -> None:
    """#2221: a German site must group a Decimal as it groups a float.

    It did before #2214, when a Decimal simply WAS a float. Asserted against
    djust's own float rendering, which is the property that must not have
    changed, and restored afterwards because the number format is process-global.
    """
    from django.test import override_settings
    from django.utils import translation

    from djust import render_env

    try:
        with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override("de"):
            render_env.apply_number_format()
            as_float = _rust.render_template("{{ p }}", {"p": 1234567.89})
            as_dec = _rust.render_template("{{ p }}", {"p": Decimal("1234567.89")})
        assert as_dec == as_float, f"Decimal {as_dec!r} vs float {as_float!r}"
        assert as_dec != "1234567.89", "localization did not apply at all"
    finally:
        render_env.apply_number_format()


def test_json_script_emits_a_decimal_as_a_quoted_string() -> None:
    """`DjangoJSONEncoder` parity — `json_script` is a path to the browser too."""
    ctx = {"p": Decimal("12345678901234567890.123456789")}
    out = _rust.render_template('{{ p|json_script:"d" }}', ctx)
    assert '"12345678901234567890.123456789"' in out
    assert "e+" not in out.lower()


def test_pprint_shows_the_constructor_form() -> None:
    ctx = {"p": Decimal("19.99")}
    # Escaped, as any filter output is.
    assert _rust.render_template("{{ p|pprint }}", ctx) == "Decimal(&#x27;19.99&#x27;)"


def test_two_decimals_differing_only_in_digits_do_not_share_a_loop_cache_entry() -> None:
    """`hash_value` hashes the DIGITS, not a parsed float.

    Two values that differ beyond f64 precision are different values; hashing
    the parsed float would let a fragment rendered from one be served for the
    other.
    """
    source = "{% for r in rows %}{{ r.v }}|{% endfor %}"
    a = _rust.render_template(source, {"rows": [{"v": Decimal("1.00000000000000000001")}]})
    b = _rust.render_template(source, {"rows": [{"v": Decimal("1.00000000000000000002")}]})
    assert a == "1.00000000000000000001|"
    assert b == "1.00000000000000000002|"
    assert a != b


# ---------------------------------------------------------------------------
# The round-trip the first version got wrong (#2240 review, finding 1).
# ---------------------------------------------------------------------------


def test_django_raises_on_a_non_finite_decimal_and_djust_renders_it() -> None:
    """A stated divergence, in djust's favour, found by a randomized sweep.

    Django's `numberformat.format` calls `abs()` on an already-stringified
    value for `NaN`/`Infinity` and raises `TypeError`. djust renders the form
    `str()` gives. Pinned so the difference is known rather than discovered.
    """
    for form in ("NaN", "Infinity", "-Infinity"):
        ctx = {"p": Decimal(form)}
        with pytest.raises(TypeError):
            DjangoTemplate("{{ p }}").render(DjangoContext(ctx))
        assert _rust.render_template("{{ p }}", ctx) == form


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1E-9", "0.000000001"),
        ("6E-10", "0.0000000006"),
        ("9.08E-9", "0.00000000908"),
        ("4E+1", "40"),
        ("1E+3", "1000"),
        ("-1E-9", "-0.000000001"),
        ("19.99", "19.99"),
    ],
)
def test_an_exponent_form_decimal_renders_expanded(raw: str, expected: str) -> None:
    """Django renders via `"{:f}".format(...)`, NOT `str()` (#2240 review).

    `Decimal('1') / Decimal('1000000000')` is `1E-9`, and so is `.normalize()`
    on many values — this is easy to reach. Rendering `str()` verbatim gave
    `1E-9` where Django gives `0.000000001`: a REGRESSION against the previous
    release, since these rendered correctly while a Decimal was still a float.

    The first version of the fix asserted the opposite in a comment. Verified
    against Django rather than reasoned about, here and by a 6,901-case
    randomized sweep.
    """
    ctx = {"p": Decimal(raw)}
    assert _rust.render_template("{{ p }}", ctx) == expected
    assert _rust.render_template("{{ p }}", ctx) == DjangoTemplate("{{ p }}").render(
        DjangoContext(ctx)
    )


def test_widthratio_treats_a_decimal_as_a_number() -> None:
    """`ToF64::to_f64` — the renderer's own numeric coercion, separate from
    `Value::as_f64` because it also parses strings.

    `{% widthratio %}` is its only consumer (`renderer.rs`), which is why the
    delegate arm had no test: nothing else in the suite drives that path with a
    Decimal. Without the arm `to_f64()` returns `None`, `unwrap_or(0.0)` makes
    every operand zero, and the tag renders `0`.
    """
    ctx = {"v": Decimal("25"), "m": Decimal("50"), "w": Decimal("100")}
    expected = DjangoTemplate("{% widthratio v m w %}").render(DjangoContext(ctx))
    assert _rust.render_template("{% widthratio v m w %}", ctx) == expected
    assert expected == "50"


@pytest.mark.asyncio
async def test_the_actor_path_carries_a_decimal_exactly() -> None:
    """`python_to_value` — the third converter, and the one that renders props.

    The actor path has its own Python->Value converter, distinct from
    `FromPyObject` and from `serialize_python_value`. It takes component props
    and, on mount, the whole `get_context_data()`. It carried the comment
    *"both must agree, or the same object renders differently depending on which
    path it took (#1646)"* while still extracting Decimal as f64 — so once the
    other two were fixed, the same value rendered two ways depending on route.
    Found by the #2240 review. Before this PR all three were consistently wrong,
    which is a different kind of correct.

    Driven through a real component render, so the assertion is on what a user
    would see rather than on an internal shape. `create_component` needs a
    mounted view, hence the mount first; the `view_id` comes back from it.
    """
    from djust._rust import create_session_actor

    handle = await create_session_actor("s-2214")
    mounted = await handle.mount("demo_app.views.CounterView", {}, None)
    html = await handle.create_component(
        mounted["view_id"],
        "c-2214",
        "{{ price }}",
        {"price": Decimal("12345678901234567890.123456789")},
        None,
    )

    assert "12345678901234567890.123456789" in html, html
    assert "e+" not in html.lower(), f"the actor path collapsed it to a float: {html}"


def test_a_decimal_subclass_is_claimed_but_a_namesake_class_is_not() -> None:
    """`isinstance`, not a type-name match.

    A subclass IS a Decimal and must keep its digits; an unrelated class that
    merely shares the name must not be stringified as one. A `type_name ==`
    check — which is what the dead branch this PR removes used — gets both
    backwards.
    """

    class Money(Decimal):
        pass

    class Namesake:
        """Named `Decimal` at runtime without being one."""

        def __str__(self) -> str:
            return "not-a-decimal"

    Namesake.__name__ = "Decimal"

    assert _rust.render_template("{{ p }}", {"p": Money("19.99")}) == "19.99"
    assert _rust.render_template("{{ p }}", {"p": Namesake()}) == "not-a-decimal"
