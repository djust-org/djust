"""`{% if <float> == <int literal> %}` against Django (#2243).

`values_equal` in `crates/djust_templates/src/renderer.rs` had arms for
`(Integer, Integer)` and `(Float, Float)` and nothing for the mixed pair, so a
float compared against an integer literal was NEVER equal: `{% if x == 0 %}`
answered "not zero" for `0.0`. `try_compare` next to it has carried mixed
arms all along, so `{% if x > 0 %}` was right the whole time — only equality
diverged.

The fix compares EXACTLY, which is the part worth pinning. An absolute
`f64::EPSILON` tolerance would make `{% if delta == 0 %}` true for
`0.1 + 0.2 - 0.3` (`5.55e-17`) — a float residue silently taking the wrong
branch. That shipped briefly in #2240 and was reverted as a worse bug than the
one it fixed, so every residue below is asserted non-zero on both engines.

Django is importable here, so the answers below are MEASURED rather than
asserted from a hand-written table (v1.1.1-2 retro). `test_differential_*` is
the randomized sweep; the named cases are the ones worth naming.
"""

from __future__ import annotations

import random

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402


def django_says(source: str, ctx: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(ctx))


def djust_says(source: str, ctx: dict) -> str:
    return _rust.render_template(source, ctx)


BOTH_ORDERS = (
    "{{% if x {op} {lit} %}}T{{% else %}}F{{% endif %}}",
    "{{% if {lit} {op} x %}}T{{% else %}}F{{% endif %}}",
)


# ---------------------------------------------------------------------------
# The reported cases.
# ---------------------------------------------------------------------------


def test_the_reported_cases_from_the_issue() -> None:
    """`0.0 == 0` and `19.0 == 19`, verbatim from #2243's table."""
    for value, lit, expected in ((0.0, 0, "Z"), (19.0, 19, "EQ")):
        source = "{%% if x == %d %%}%s{%% else %%}N%s{%% endif %%}" % (lit, expected, expected)
        ctx = {"x": value}
        assert djust_says(source, ctx) == expected, f"{value} == {lit}"
        assert djust_says(source, ctx) == django_says(source, ctx)


def test_a_float_residue_is_still_not_zero() -> None:
    """The trap the exact comparison exists to avoid (#2240 round 6).

    These are what float arithmetic actually produces. Every one is non-zero to
    Python, so every one must be non-zero here; an epsilon tolerance in the new
    arms would call the first two zero and take the wrong branch.
    """
    for value in (0.1 + 0.2 - 0.3, 1.0 - 0.9 - 0.1, 1e-17, 5e-324, 2.2e-16, -1e-17):
        for template in BOTH_ORDERS:
            source = template.format(op="==", lit=0)
            ctx = {"x": value}
            assert djust_says(source, ctx) == "F", f"{value!r} read as zero"
            assert django_says(source, ctx) == "F"


def test_equality_holds_with_the_literal_on_either_side() -> None:
    """A two-sided guard pinned on one side is half a guard (#1859).

    `values_equal` needs BOTH `(Integer, Float)` and `(Float, Integer)`; the
    left-hand literal form is the only thing that exercises the first.
    """
    for template in BOTH_ORDERS:
        for value, lit in ((0.0, 0), (19.0, 19), (-19.0, -19), (2.5, 2)):
            for op in ("==", "!="):
                source = template.format(op=op, lit=lit)
                ctx = {"x": value}
                assert djust_says(source, ctx) == django_says(source, ctx), (source, value)


def test_the_in_operator_shares_the_same_comparison() -> None:
    """`values_equal` is also the sink for `{% if needle in seq %}`.

    Django: `0 in [0.0]` is true. It was false here for the same reason
    `{% if x == 0 %}` was.
    """
    source = "{% if x in xs %}T{% else %}F{% endif %}"
    for ctx in (
        {"x": 0, "xs": [0.0, 1.0]},
        {"x": 0.0, "xs": [0, 1]},
        {"x": 19, "xs": [18.5, 19.0]},
        {"x": 0.5, "xs": [0, 1]},
    ):
        assert djust_says(source, ctx) == django_says(source, ctx), ctx


# ---------------------------------------------------------------------------
# The exactness boundary.
# ---------------------------------------------------------------------------


def test_an_integer_beyond_f64_precision_is_not_equal_to_its_rounded_float() -> None:
    """Why the arms are not `*a as f64 == *b`, which #2243 proposed.

    `9007199254740993 as f64` IS `9007199254740992.0` — the cast rounds — so the
    naive spelling answers true for two values Python calls different. This case
    agreed with Django BEFORE the fix (everything was false) and would have
    started disagreeing with it, i.e. the obvious shape trades one divergence
    for a narrower one.
    """
    pairs = (
        (9007199254740993, 9007199254740992.0, "F"),  # 2^53 + 1 vs 2^53
        (9007199254740992, 9007199254740992.0, "T"),  # 2^53 itself is exact
        (2**62 + 1, float(2**62), "F"),
    )
    for lit, value, expected in pairs:
        for template in BOTH_ORDERS:
            source = template.format(op="==", lit=lit)
            ctx = {"x": value}
            assert djust_says(source, ctx) == expected, (lit, value)
            assert django_says(source, ctx) == expected, (lit, value)


def test_a_float_out_of_i64_range_equals_no_integer() -> None:
    """`b as i64` saturates rather than wrapping, so the range guard is load-bearing.

    Without it `1e300` would compare equal to `i64::MAX` and `-1e300` to
    `i64::MIN`.
    """
    for lit, value in (
        (2**63 - 1, 1e300),
        (-(2**63), -1e300),
        (2**63 - 1, float(2**63)),  # 2^63 is one past i64::MAX
    ):
        source = "{%% if x == %d %%}T{%% else %%}F{%% endif %%}" % lit
        ctx = {"x": value}
        assert djust_says(source, ctx) == "F", (lit, value)
        assert django_says(source, ctx) == "F", (lit, value)


def test_non_finite_and_fractional_floats_equal_no_integer() -> None:
    for value in (float("inf"), float("-inf"), float("nan"), 0.5, -0.5, 19.5):
        for lit in (0, 1, 19, -19):
            source = "{%% if x == %d %%}T{%% else %%}F{%% endif %%}" % lit
            ctx = {"x": value}
            assert djust_says(source, ctx) == "F", (value, lit)
            assert django_says(source, ctx) == "F", (value, lit)


def test_negative_zero_equals_zero() -> None:
    """`-0.0 == 0` is true in Python; `(-0.0).fract()` is `-0.0`, which is `== 0.0`."""
    source = "{% if x == 0 %}T{% else %}F{% endif %}"
    assert djust_says(source, {"x": -0.0}) == "T"
    assert django_says(source, {"x": -0.0}) == "T"


# ---------------------------------------------------------------------------
# Regressions: the arms this PR did NOT touch.
# ---------------------------------------------------------------------------


def test_like_for_like_comparisons_are_unchanged() -> None:
    """int/int and float/float keep their own arms and their own answers."""
    cases = (
        ("{% if x == 0 %}T{% else %}F{% endif %}", {"x": 0}, "T"),
        ("{% if x == 0 %}T{% else %}F{% endif %}", {"x": 1}, "F"),
        ("{% if x != 19 %}T{% else %}F{% endif %}", {"x": 19}, "F"),
        ("{% if x == 19.5 %}T{% else %}F{% endif %}", {"x": 19.5}, "T"),
        ("{% if x == 19.5 %}T{% else %}F{% endif %}", {"x": 19.6}, "F"),
        # Not a Django-parity claim: `(Float, Float)` keeps its epsilon, which
        # Django does not have. Pinned as unchanged, not as correct (#1079).
        ("{% if x == y %}T{% else %}F{% endif %}", {"x": 1e-17, "y": 0.0}, "T"),
    )
    for source, ctx, expected in cases:
        assert djust_says(source, ctx) == expected, source

    # Everything above except the last also agrees with Django.
    for source, ctx, _expected in cases[:-1]:
        assert djust_says(source, ctx) == django_says(source, ctx), source


def test_strings_do_not_become_numbers() -> None:
    """`numeric_pair`'s reason for existing: `{% if "5" == 5 %}` stays false."""
    for source, ctx in (
        ('{% if x == "5" %}T{% else %}F{% endif %}', {"x": 5.0}),
        ("{% if x == 5 %}T{% else %}F{% endif %}", {"x": "5"}),
        ("{% if x == 5 %}T{% else %}F{% endif %}", {"x": "5.0"}),
    ):
        assert djust_says(source, ctx) == "F", source
        assert django_says(source, ctx) == "F", source


def test_none_and_missing_still_compare_as_before() -> None:
    """#2203's arm is above the new ones and unaffected by them."""
    for source, ctx, expected in (
        ("{% if x == None %}T{% else %}F{% endif %}", {"x": None}, "T"),
        ("{% if x == None %}T{% else %}F{% endif %}", {"x": 0.0}, "F"),
        ("{% if absent == None %}T{% else %}F{% endif %}", {}, "T"),
        ("{% if x == 0 %}T{% else %}F{% endif %}", {"x": None}, "F"),
    ):
        assert djust_says(source, ctx) == expected, source


def test_decimal_equality_is_untouched() -> None:
    """The `is_decimal_pair` arm below the new ones keeps its epsilon (#2214).

    A Decimal is neither `Value::Integer` nor `Value::Float`, so it cannot reach
    the new arms — this pins that it did not, by asserting the epsilon behaviour
    the Decimal arm has and the new arms deliberately do not.
    """
    from decimal import Decimal

    source = "{% if p == 0 %}Z{% else %}NZ{% endif %}"
    assert djust_says(source, {"p": Decimal("0.00")}) == "Z"
    # Below f64::EPSILON: still the Decimal arm's answer, not the exact one.
    assert djust_says(source, {"p": Decimal("1E-30")}) == "Z"
    assert django_says(source, {"p": Decimal("1E-30")}) == "NZ"


# ---------------------------------------------------------------------------
# The differential.
# ---------------------------------------------------------------------------


# Float literals, written the way a template author writes them.
#
# No `1e-17`: Django's `FilterExpression` regex is `[-+\.]?\d[\d\.e]*`, which
# has no place for a sign inside the exponent, so `{% if x == 1e-17 %}` is a
# TemplateSyntaxError there rather than a comparison. Not djust's to match, and
# not a case a template can express — the differential would be measuring the
# harness. `1e300` parses on both.
FLOAT_LITERALS = ("0.0", "-0.0", "19.0", "-19.0", "0.5", "19.5", "1e300")


def _sweep_values() -> tuple[list, list]:
    """Curated edge values plus a seeded random sample of each type."""
    rng = random.Random(2243)

    floats = [
        0.0,
        -0.0,
        1.0,
        19.0,
        -19.0,
        0.5,
        -0.5,
        19.5,
        1e-17,
        -1e-17,
        5e-324,
        2.2e-16,
        0.1 + 0.2 - 0.3,
        1.0 - 0.9 - 0.1,
        1e300,
        -1e300,
        float(2**53),
        float(2**53) + 2.0,
        float(2**62),
        float(2**63),
        float("inf"),
        float("-inf"),
        float("nan"),
    ]
    ints = [0, 1, -1, 19, -19, 2, 2**53, 2**53 + 1, 2**62, 2**62 + 1, 2**63 - 1, -(2**63)]

    for _ in range(25):
        floats.append(float(rng.randint(-1000, 1000)))
        floats.append(rng.uniform(-1000.0, 1000.0))
        floats.append(rng.choice([1, -1]) * rng.uniform(0.0, 1e-15))
        ints.append(rng.randint(-1000, 1000))
    return floats, ints


def test_differential_against_django_over_mixed_numeric_pairs() -> None:
    """Every combination of a numeric context value and a numeric literal.

    The curated cases above sample one axis; this sweeps the cross product with
    both operand orders and both operators, which is what caught the `as f64`
    rounding case. Reference answers come from Django itself.
    """
    floats, ints = _sweep_values()
    checked = 0
    mismatches = []

    for op in ("==", "!="):
        for template in BOTH_ORDERS:
            # (literal, values to put in the context) — the integer literal is
            # swept against float context values and vice versa, so both
            # `(Integer, Float)` and `(Float, Integer)` are exercised on both
            # sides of the operator.
            for literals, values in ((ints, floats), (FLOAT_LITERALS, ints)):
                for lit in literals:
                    source = template.format(op=op, lit=lit)
                    # Compiling the Django template once per source rather than
                    # once per case keeps the sweep to a couple of seconds.
                    compiled = DjangoTemplate(source)
                    for value in values:
                        ctx = {"x": value}
                        checked += 1
                        if djust_says(source, ctx) != compiled.render(DjangoContext(ctx)):
                            mismatches.append((source, value))

    assert checked > 5000, f"sweep degenerated to {checked} cases"
    assert not mismatches, f"{len(mismatches)}/{checked} disagree with Django: {mismatches[:10]}"
