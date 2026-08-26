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
    is rare and whose Django answer is surprising to most readers.

    Comparison against an INTEGER is the common case and mostly agrees with
    Django — but NOT below `f64::EPSILON`, and an earlier version of this
    docstring claimed it did without qualification. See
    `test_decimal_equality_with_an_integer_is_epsilon_bounded` for the exact
    boundary; that behaviour is new in this PR, not inherited.
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


def test_a_loop_over_distinct_decimals_renders_each_one() -> None:
    """`hash_value` must hash the DIGITS. Two rows, ONE render, one cache.

    The version this replaces made two SEPARATE `render_template` calls, each
    with its own cache — so the hash never had to distinguish anything and the
    test passed with the digits discarded. It was green while the property it
    names was false, which is the sharpest form of the tautology class
    (#2135/#1200).

    It stayed green through a real regression: a gate-off mutation
    (`d.hash(hasher)` -> `let _ = d;`) leaked into commit `5b723a98`, every
    Decimal hashed identically, and `{% for %}` served row 1's fragment for
    every row — wrong prices in a price table, on djust's own default
    (`loop_render_cache_enabled: True`). The 10,287-green suite and my own
    gate-off both missed it; the re-review's collision probe found it.

    **This test cannot see the loop cache** and does not claim to: `render_template`
    installs no `LoopCacheGuard`, only the `RustLiveView` paths do. An earlier
    version of this docstring said otherwise — false, and proved so by a gate-off
    that discarded the Decimal payload and left this green (#1867).

    The cache guard lives in
    `crates/djust_templates/tests/test_decimal_loop_cache_2214.rs`, where a real
    guard is installed. What remains here is worth keeping on its own terms: a
    Django-parity check that a `{% for %}` over Decimals renders each one.
    """
    rows = [{"price": Decimal("19.99")}, {"price": Decimal("249.00")}]
    source = "{% for r in rows %}<li>{{ r.price }}</li>{% endfor %}"
    out = _rust.render_template(source, {"rows": rows})
    assert out == "<li>19.99</li><li>249.00</li>", (
        f"loop cache served one row's fragment for another: {out!r}. "
        "hash_value must hash the Decimal's digit string."
    )
    assert out == DjangoTemplate(source).render(DjangoContext({"rows": rows}))


def test_decimals_differing_beyond_f64_do_not_collide_in_one_loop() -> None:
    """The same guard at the precision boundary that motivates the variant.

    Hashing a PARSED FLOAT instead of the digits would collide here while
    passing the test above.
    """
    rows = [
        {"v": Decimal("1.00000000000000000001")},
        {"v": Decimal("1.00000000000000000002")},
    ]
    out = _rust.render_template("{% for r in rows %}{{ r.v }}|{% endfor %}", {"rows": rows})
    assert out == "1.00000000000000000001|1.00000000000000000002|", out


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

    # A value that a FLOAT cannot represent, so the subclass assertion fails
    # under a name-based check (`Money` is not named `Decimal`) where it passes
    # under `isinstance`. The previous version used `19.99`, which renders the
    # same either way — decorative, and green under gate-off.
    assert (
        _rust.render_template("{{ p }}", {"p": Money("12345678901234567890.123456789")})
        == "12345678901234567890.123456789"
    )
    assert _rust.render_template("{{ p }}", {"p": Namesake()}) == "not-a-decimal"


def test_every_json_producing_converter_keeps_the_digits() -> None:
    """The three converters the fix added that had no test (#2240 re-review).

    `python_to_json_value` (`fast_json_dumps`), `python_to_json`, and
    `model_serializer`'s are all public exports with no in-repo caller, which is
    exactly why the gate-off found them green: nothing drove them. A converter
    nothing exercises is a converter that can silently revert.
    """
    from djust._rust import fast_json_dumps, serialize_models_fast, serialize_models_to_list

    huge = Decimal("12345678901234567890.123456789")

    out = fast_json_dumps({"p": huge})
    assert '"12345678901234567890.123456789"' in out, out
    assert "e+" not in out.lower()

    # Both take a list of already-extracted field dicts, not model instances.
    for fn in (serialize_models_fast, serialize_models_to_list):
        result = str(fn([{"id": 1, "price": huge}]))
        assert "12345678901234567890.123456789" in result, f"{fn.__name__}: {result}"
        assert "e+19" not in result.lower(), f"{fn.__name__} collapsed it: {result}"


def test_a_tagged_decimal_cannot_inject_json_structure() -> None:
    """`value_to_json` escapes a Decimal through the same helper as a String.

    The hostile value has to arrive as a `Value::Decimal`, and the only way it
    can is the binary tag: a dict with the tag as its single key deserializes
    AS a Decimal, so its payload is attacker-chosen. That is what falsifies the
    "a Decimal only contains digits, so no escaping is needed" reasoning — true
    of the values it considered, false of the type.

    The first version of this test passed a plain Python string, which is the
    `String` arm — it exercised the escaping that already worked and left the
    Decimal arm green under gate-off.
    """
    from djust._rust import RustLiveView

    payload = '","admin":true,"x":"'
    view = RustLiveView('{{ p|json_script:"d" }}')
    view.set_state("p", {"__djust_decimal__": payload})
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())

    # It is a `Value::Decimal` on the Rust side. `get_state()` hands back a str
    # only because `IntoPyObject` falls back when `Decimal(payload)` raises —
    # which is the point: Rust is holding a Decimal whose payload never went
    # near the decimal parser.
    assert restored.get_state()["p"] == payload

    out = restored.render()
    assert '"admin":true' not in out, f"JSON structure injected into a script body: {out}"


# ---------------------------------------------------------------------------
# Arms that were still green after the re-review's fixes — including two of the
# fixes themselves, which I had verified only with an ad-hoc script.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0E+3", "0"), ("-0E+3", "-0"), ("0E+9", "0"), ("0E-3", "0.000"), ("0E+0", "0")],
)
def test_a_zero_coefficient_never_grows_trailing_zeros(raw: str, expected: str) -> None:
    """`format(Decimal('0E+3'), 'f')` is `0`, not `0000`.

    The first expansion claimed in its own doc-comment to implement
    `format(d, 'f')` and rendered `0000`. Reachable from ordinary money
    arithmetic: `Decimal('1000').quantize(Decimal('1E+2'))` minus itself is
    `Decimal('0E+2')`, so a zero balance rendered `000`.
    """
    ctx = {"p": Decimal(raw)}
    assert _rust.render_template("{{ p }}", ctx) == expected
    assert _rust.render_template("{{ p }}", ctx) == DjangoTemplate("{{ p }}").render(
        DjangoContext(ctx)
    )


def test_a_very_long_decimal_uses_djangos_scientific_fallback() -> None:
    """Django switches to `{:e}` when `abs(exponent) + len(digits) > 200`.

    Its own comment says this exists *"to avoid high memory usage in
    `{:f}'.format()`"*. Without the cutoff a twelve-byte Decimal expanded to a
    ten-megabyte string — an amplification `main` never had, since the value was
    an f64 there. The boundary is pinned exactly, because an off-by-one here is
    invisible either side of it.
    """
    assert _rust.render_template("{{ p }}", {"p": Decimal("1E-199")}) == "0." + "0" * 198 + "1"

    # The axis the first version of this test missed entirely: every one of its
    # cases had `1` as the integer part, so none exercised a `0.xxx` `str()`
    # form — where `as_tuple().digits` drops the leading zero and a naive parse
    # does not. That off-by-N fired the cutoff up to six places early and
    # shifted the coefficient when it did (#2240 round 3).
    for raw in (
        "0." + "1" * 100,  # sum == 200 exactly: Django stays fixed-point
        "0." + "1" * 101,  # sum == 202: Django goes scientific
        "0." + "0" * 5 + "1" * 95,  # leading zeros AFTER the point too
        "-0." + "1" * 101,
    ):
        ctx = {"p": Decimal(raw)}
        assert _rust.render_template("{{ p }}", ctx) == DjangoTemplate("{{ p }}").render(
            DjangoContext(ctx)
        ), f"diverged for {raw[:20]}... ({len(raw)} chars)"

    # Ordinary code, not a constructed pathology.
    from decimal import localcontext

    with localcontext() as c:
        c.prec = 120
        ctx = {"p": Decimal(1) / Decimal(7)}
    assert _rust.render_template("{{ p }}", ctx) == DjangoTemplate("{{ p }}").render(
        DjangoContext(ctx)
    )
    for raw in ("1E-200", "1E-201", "1.230E-250", "-1E-201"):
        ctx = {"p": Decimal(raw)}
        assert _rust.render_template("{{ p }}", ctx) == DjangoTemplate("{{ p }}").render(
            DjangoContext(ctx)
        )

    out = _rust.render_template("{{ p }}", {"p": Decimal("1E-10000000")})
    assert out == "1e-10000000", out
    assert len(out) < 100, f"expanded to {len(out)} bytes instead of using scientific form"


def test_a_decimal_returns_from_the_state_round_trip_as_a_decimal() -> None:
    """`IntoPyObject` — a handler reading state back must not see its type change.

    This is the exact path `InMemoryStateBackend.get()` takes, and the reason
    the binary tag exists: without it the value came back a `str`, so
    `view.price - 1` raised `TypeError` on the second event.
    """
    from djust._rust import RustLiveView

    view = RustLiveView("{{ price }}")
    view.set_state("price", Decimal("12345678901234567890.123456789"))
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())

    value = restored.get_state()["price"]
    assert isinstance(value, Decimal), f"came back as {type(value).__name__}: {value!r}"
    assert value == Decimal("12345678901234567890.123456789")


def test_a_positive_scientific_exponent_keeps_its_sign() -> None:
    """`{:+}` — Python writes `1e+212`, not `1e212`.

    Every scientific-branch case elsewhere in this file has a NEGATIVE exponent,
    where the sign appears either way, so the arm was unpinned (#2240 round 3).
    """
    ctx = {"p": Decimal("1E+250")}
    assert _rust.render_template("{{ p }}", ctx) == "1e+250"
    assert _rust.render_template("{{ p }}", ctx) == DjangoTemplate("{{ p }}").render(
        DjangoContext(ctx)
    )


def test_a_non_finite_decimal_passes_through_rather_than_raising() -> None:
    """A DIVERGENCE from Django, in djust's favour, and now pinned.

    Django's `numberformat.format` calls `abs()` on an already-stringified value
    and raises `TypeError` for `NaN`/`Infinity`. djust renders the form `str()`
    gives.

    This pins the BEHAVIOUR. The guards that produce it are pinned separately in
    `crates/djust_core/tests/test_decimal_value_2214.rs`, and an earlier version
    of this docstring claimed they were unreachable — false, and false because
    the "measurement" behind it ran only one arm.
    """
    for form in ("NaN", "sNaN", "Infinity", "-Infinity"):
        ctx = {"p": Decimal(form)}
        assert _rust.render_template("{{ p }}", ctx) == form
        with pytest.raises(TypeError):
            DjangoTemplate("{{ p }}").render(DjangoContext(ctx))


def test_a_tagged_decimal_escapes_backslashes_and_control_characters() -> None:
    """Every escape in the shared helper, not just the quote.

    The injection test above only exercises `"`, so dropping the backslash or
    newline case from `json_string_body` left the suite green.
    """
    import json

    from djust._rust import RustLiveView

    payload = 'a\\b"c\nd\re\tf'
    view = RustLiveView('{{ p|json_script:"d" }}')
    view.set_state("p", {"__djust_decimal__": payload})
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())

    out = restored.render()
    body = out[out.index(">") + 1 : out.rindex("</script>")]
    # The proof that every escape landed: it parses, and round-trips exactly.
    assert json.loads(body) == payload, body


def test_python_to_json_keeps_the_digits() -> None:
    """The sixth converter (`djust_live/src/lib.rs` `python_to_json`).

    It is named in the CHANGELOG and in
    `test_every_json_producing_converter_keeps_the_digits`, which actually drives
    `fast_json_dumps` and the two model serializers — so it stayed green under
    gate-off while being listed as covered (#2240 round 3).

    Reached via `serialize_queryset` -> `serialize_object_with_paths` ->
    `python_to_json`, which is the real path a queryset attribute takes.
    """
    from djust._rust import serialize_queryset

    class Row:
        """Duck-typed: the path walker reads attributes by name."""

        def __init__(self) -> None:
            self.price = Decimal("12345678901234567890.123456789")

    result = serialize_queryset([Row()], ["price"])
    assert result == [{"price": "12345678901234567890.123456789"}], result


def test_decimal_equality_with_an_integer_is_epsilon_bounded() -> None:
    """`{% if p == 0 %}` uses an absolute `f64::EPSILON` tolerance (#2240 round 5).

    NEW in this PR, not inherited: `values_equal`'s wildcard arm was `_ => false`
    before, so a Decimal never compared equal to an integer at all. The new arm
    makes it compare, with `(a - b).abs() < f64::EPSILON`.

    Scoped to pairs that involve a Decimal — see
    `test_plain_float_equality_is_unchanged_by_this_pr`, which is the guard that
    the widening did not leak onto the float path. It did, for two rounds.

    Net-positive — `Decimal('0.00') == 0` is now right where it used to be
    wrong — but it has a boundary, and an unqualified "agrees with Django" is
    false below it. Pinned exactly so the limit is a decision rather than a
    discovery. `<` and `>` are unaffected: `compare_values` already carried the
    same epsilon arm for `(Float, Integer)`.
    """
    source = "{% if p == 0 %}Z{% else %}NZ{% endif %}"

    # Agrees with Django, including the case that used to be wrong.
    for raw in ("0.00", "0E-250", "1E-15", "2.2204460492503130E-16"):
        ctx = {"p": Decimal(raw)}
        assert _rust.render_template(source, ctx) == DjangoTemplate(source).render(
            DjangoContext(ctx)
        ), raw

    # Below the epsilon, djust says zero where Django says non-zero.
    for raw in ("2.2204460492503129E-16", "1E-16", "1E-30"):
        ctx = {"p": Decimal(raw)}
        assert _rust.render_template(source, ctx) == "Z"
        assert DjangoTemplate(source).render(DjangoContext(ctx)) == "NZ"


def test_plain_float_equality_is_unchanged_by_this_pr() -> None:
    """`{% if x == 0 %}` on an ordinary float must answer as it did before #2214.

    The equality widening was written for Decimals and reached `(Float, Integer)`
    too, which involves no Decimal at all. On the previous release that pair fell
    to `_ => false`; widened, it took an absolute `f64::EPSILON` tolerance, so
    `{% if delta == 0 %}` on a float residue silently took the wrong branch —
    the same failure class this PR exists to fix, introduced by its own fix.

    It survived two review rounds because the arm's comment said "every pair
    involving a Decimal" while the code did more. These are the values float
    arithmetic actually produces.
    """
    source = "{% if x == 0 %}Z{% else %}NZ{% endif %}"
    for value in (0.1 + 0.2 - 0.3, 1.0 - 0.9 - 0.1, 1e-17, 5e-324, 2.2e-16):
        ctx = {"x": value}
        assert _rust.render_template(source, ctx) == "NZ", f"{value!r} read as zero"
        assert DjangoTemplate(source).render(DjangoContext(ctx)) == "NZ"

    # A PRE-EXISTING divergence this RESTORES rather than fixes: djust answers
    # `{% if <float> == <int literal> %}` false regardless, so `0.0 == 0` and
    # `19.0 == 19` are both false where Django says true. That predates #2214
    # and is not its to change (#1079) — filed as #2243. Asserted so the
    # restoration is exact and the divergence is recorded, not rediscovered.
    assert _rust.render_template(source, {"x": 0.0}) == "NZ"
    assert DjangoTemplate(source).render(DjangoContext({"x": 0.0})) == "Z"


def test_a_hostile_exponent_magnitude_does_not_crash_the_render() -> None:
    """`i64` arithmetic on an attacker-chosen exponent (#2240 round 6).

    `str_exp` comes from `parse::<i64>()` on text that the binary tag lets a
    `Value::Decimal` hold arbitrarily. `1.5E-9223372036854775808` overflowed the
    subtraction that folds the fractional length into the exponent: a panic on
    the render path in debug, and — since `overflow-checks` is off in release —
    a silent wrap in the shipped wheel.

    Not reachable from a real `Decimal` (CPython rejects exponents past ~2e18),
    only through the tag. That is the same route the escaping fix and both
    `*_guard_is_load_bearing` tests rest on, so it is reachable by this PR's own
    standard; it cannot be attacker-reachable for those and not for this.

    The guard tests enumerated every hostile SHAPE and no hostile MAGNITUDE.
    """
    from djust._rust import RustLiveView

    for payload in (
        "1.5E-9223372036854775808",
        "1.55E-9223372036854775808",
        "12E+9223372036854775807",
        "1.5E+9223372036854775807",
        "0.0000000001E-9223372036854775800",
        "1E-9223372036854775808",
    ):
        view = RustLiveView("{{ p }}")
        view.set_state("p", {"__djust_decimal__": payload})
        restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        # The assertion is that this returns at all.
        assert isinstance(restored.render(), str), payload
