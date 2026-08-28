"""`{% if <bool> == <number> %}` and `{% if <bool> > 0 %}` against Django (#2244).

`bool` subclasses `int` in Python, so `True == 1`, `False == 0` and `True > 0`
are all true and Django agrees. djust said false to all three: `values_equal` in
`crates/djust_templates/src/renderer.rs` had a `(Bool, Bool)` arm and no mixed
Bool/numeric arm, so a bool against a number fell to `_ => false`; `try_compare`
had no `Bool` arm at all, so a bool reached `numeric_pair` — which admits only
`{Integer, Float, Decimal}` — got `None`, and yielded 0, "equal". Both `>` and
`<` were therefore false while `>=` and `<=` were both true, which is how the
ordering hole managed to look half-correct.

The fix substitutes `Value::Integer(0 | 1)` for a bool operand and re-enters, so
a bool takes the SAME arm its integer value takes. That is what Python does, and
it is why the strongest assertion here is not a Django table but an
**equivalence**: for every operator and every right-hand operand,

    djust(<bool> OP y)  ==  djust(<int(bool)> OP y)

That holds even where djust and Django still disagree — NaN ordering and
sub-epsilon Decimal equality are pre-existing `(Integer, *)` divergences (#1079),
and a bool now inherits them rather than having answers of its own. Pinning the
equivalence rather than an exclusion list means the day those are fixed for
integers, bools are fixed with them and this file needs no edit.

The extra hole `try_compare` had: `{% if a > b %}` on two bools was also 0,
"equal", because there is no bool-vs-bool ordering arm either. The substitution
covers that pair too. `values_equal`'s `(Bool, Bool)` arm is deliberately NOT
substituted — same answer, and skipping it keeps that arm live.

`values_identity` (`is` / `is not`) must NOT widen: `True is 1` is false in
Python. Both engines already agreed and `test_is_does_not_widen_*` pins it.

Django is importable here, so every answer below is MEASURED rather than
asserted from a hand-written table (v1.1.1-2 retro).
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

OPS = ("==", "!=", ">", "<", ">=", "<=")

# `{% if x OP <literal> %}` and `{% if <literal> OP x %}` — a two-sided guard
# pinned on one side is half a guard (#1859).
BOTH_ORDERS = (
    "{{% if x {op} {lit} %}}T{{% else %}}F{{% endif %}}",
    "{{% if {lit} {op} x %}}T{{% else %}}F{{% endif %}}",
)

# `{% if x OP y %}` and `{% if y OP x %}` — the only form that can carry a
# Decimal, which has no template-literal syntax.
BOTH_ORDERS_VARS = (
    "{{% if x {op} y %}}T{{% else %}}F{{% endif %}}",
    "{{% if y {op} x %}}T{{% else %}}F{{% endif %}}",
)


def django_says(source: str, ctx: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(ctx))


def djust_says(source: str, ctx: dict) -> str:
    return _rust.render_template(source, ctx)


# ---------------------------------------------------------------------------
# The reported cases.
# ---------------------------------------------------------------------------


def test_the_reported_cases_from_the_issue() -> None:
    """The four rows of #2244's table, verbatim, plus the ordering hole."""
    cases = (
        ("{% if flag == 1 %}T{% else %}F{% endif %}", {"flag": True}, "T"),
        ("{% if flag == 1.0 %}T{% else %}F{% endif %}", {"flag": True}, "T"),
        ("{% if flag == 0 %}T{% else %}F{% endif %}", {"flag": False}, "T"),
        ("{% if flag == True %}T{% else %}F{% endif %}", {"flag": True}, "T"),
        ("{% if flag > 0 %}T{% else %}F{% endif %}", {"flag": True}, "T"),
    )
    for source, ctx, expected in cases:
        assert djust_says(source, ctx) == expected, source
        assert djust_says(source, ctx) == django_says(source, ctx), source


def test_both_operand_orders_and_both_equality_operators() -> None:
    for template in BOTH_ORDERS:
        for value in (True, False):
            for lit in (0, 1, -1, 2, "0.0", "1.0", "0.5", "True", "False"):
                for op in ("==", "!="):
                    source = template.format(op=op, lit=lit)
                    ctx = {"flag": value, "x": value}
                    assert djust_says(source, ctx) == django_says(source, ctx), (source, value)


def test_all_four_ordering_operators() -> None:
    """`try_compare` yielded 0 for a bool, so `>`/`<` were false and

    `>=`/`<=` were true — half of which looked correct by accident.
    """
    for template in BOTH_ORDERS:
        for value in (True, False):
            for lit in (0, 1, -1, 2, "0.0", "1.0", "0.5"):
                for op in (">", "<", ">=", "<="):
                    source = template.format(op=op, lit=lit)
                    ctx = {"x": value}
                    assert djust_says(source, ctx) == django_says(source, ctx), (source, value)


def test_two_bools_can_be_ordered() -> None:
    """`try_compare` has no bool-vs-bool arm either, so this was 0/"equal"."""
    for source, ctx, expected in (
        ("{% if a > b %}T{% else %}F{% endif %}", {"a": True, "b": False}, "T"),
        ("{% if a < b %}T{% else %}F{% endif %}", {"a": True, "b": False}, "F"),
        ("{% if a >= b %}T{% else %}F{% endif %}", {"a": False, "b": False}, "T"),
        ("{% if a < b %}T{% else %}F{% endif %}", {"a": False, "b": True}, "T"),
    ):
        assert djust_says(source, ctx) == expected, (source, ctx)
        assert djust_says(source, ctx) == django_says(source, ctx), (source, ctx)


def test_bool_against_a_decimal() -> None:
    """`Value::Decimal` exists since #2214 and `True == Decimal('1')` is true."""
    for template in BOTH_ORDERS_VARS:
        for flag in (True, False):
            for dec in (Decimal("0"), Decimal("1"), Decimal("0.5"), Decimal("-1"), Decimal("1.0")):
                for op in OPS:
                    source = template.format(op=op)
                    ctx = {"x": flag, "y": dec}
                    assert djust_says(source, ctx) == django_says(source, ctx), (source, flag, dec)


def test_the_in_operator_shares_the_same_comparison() -> None:
    """`values_equal` is also the sink for `{% if needle in seq %}`."""
    source = "{% if x in xs %}T{% else %}F{% endif %}"
    for ctx in (
        {"x": True, "xs": [1, 2]},
        {"x": True, "xs": [1.0]},
        {"x": False, "xs": [0]},
        {"x": False, "xs": [1, 2]},
        {"x": 1, "xs": [True]},
        {"x": 0, "xs": [False]},
        {"x": 2, "xs": [True, False]},
    ):
        assert djust_says(source, ctx) == django_says(source, ctx), ctx


# ---------------------------------------------------------------------------
# The arm that must NOT widen.
# ---------------------------------------------------------------------------


def test_is_does_not_widen_to_numbers() -> None:
    """`True is 1` is FALSE in Python — `values_identity` stays strict.

    This agreed with Django before the fix and must still agree after it, which
    is the asymmetry #2244 exists to preserve: `==` and `<`/`>` widen, `is` does
    not.
    """
    for template in BOTH_ORDERS:
        for value in (True, False):
            for lit in (0, 1, "0.0", "1.0"):
                for op in ("is", "is not"):
                    source = template.format(op=op, lit=lit)
                    ctx = {"x": value}
                    expected = "F" if op == "is" else "T"
                    assert djust_says(source, ctx) == expected, (source, value)
                    assert djust_says(source, ctx) == django_says(source, ctx), (source, value)


def test_is_between_two_bools_is_unchanged() -> None:
    for template in BOTH_ORDERS:
        for value in (True, False):
            for lit in ("True", "False", "None"):
                for op in ("is", "is not"):
                    source = template.format(op=op, lit=lit)
                    ctx = {"x": value}
                    assert djust_says(source, ctx) == django_says(source, ctx), (source, value)


# ---------------------------------------------------------------------------
# Regressions: the arms this must NOT have touched.
# ---------------------------------------------------------------------------


def test_strings_and_none_do_not_become_numbers() -> None:
    """The substitution replaces a BOOL and nothing else (#1079).

    `"1" == True` and `None == False` are false in Python, and were false here
    before the fix.
    """
    for source, ctx in (
        ('{% if x == "1" %}T{% else %}F{% endif %}', {"x": True}),
        ('{% if x == "True" %}T{% else %}F{% endif %}', {"x": True}),
        ('{% if "0" == x %}T{% else %}F{% endif %}', {"x": False}),
        ("{% if x == y %}T{% else %}F{% endif %}", {"x": True, "y": "1"}),
        ("{% if x == None %}T{% else %}F{% endif %}", {"x": False}),
        ("{% if x == None %}T{% else %}F{% endif %}", {"x": True}),
        ("{% if absent == x %}T{% else %}F{% endif %}", {"x": False}),
    ):
        assert djust_says(source, ctx) == "F", source
        assert django_says(source, ctx) == "F", source


def test_like_for_like_numeric_comparisons_are_unchanged() -> None:
    """The `(Bool, Bool)`, `(Integer, Integer)`, `(Float, Float)`,

    `(Integer, Float)` and Decimal arms keep their own answers (#2243, #2214).
    """
    cases = (
        ("{% if x == y %}T{% else %}F{% endif %}", {"x": True, "y": True}, "T"),
        ("{% if x == y %}T{% else %}F{% endif %}", {"x": True, "y": False}, "F"),
        ("{% if x != y %}T{% else %}F{% endif %}", {"x": False, "y": False}, "F"),
        ("{% if x == 0 %}T{% else %}F{% endif %}", {"x": 0}, "T"),
        ("{% if x == 19.5 %}T{% else %}F{% endif %}", {"x": 19.5}, "T"),
        ("{% if x == 0 %}T{% else %}F{% endif %}", {"x": 0.0}, "T"),  # #2243
        ("{% if x > 0 %}T{% else %}F{% endif %}", {"x": 0.5}, "T"),
        ("{% if p == 0 %}T{% else %}F{% endif %}", {"p": Decimal("0.00")}, "T"),
        ("{% if p > 10 %}T{% else %}F{% endif %}", {"p": Decimal("19.99")}, "T"),
        # Not a Django-parity claim: `(Float, Float)` keeps its epsilon, which
        # Django does not have. Pinned as unchanged, not as correct (#1079).
        ("{% if x == y %}T{% else %}F{% endif %}", {"x": 1e-17, "y": 0.0}, "T"),
    )
    for source, ctx, expected in cases:
        assert djust_says(source, ctx) == expected, (source, ctx)

    for source, ctx, _expected in cases[:-1]:
        assert djust_says(source, ctx) == django_says(source, ctx), (source, ctx)


def test_a_float_residue_is_still_not_equal_to_a_bool() -> None:
    """#2243's trap, re-checked from the bool side.

    A bool routes through `int_eq_float`, which compares EXACTLY. An absolute
    tolerance would call `0.1 + 0.2 - 0.3` equal to `False`.
    """
    for value in (0.1 + 0.2 - 0.3, 1.0 - 0.9 - 0.1, 1e-17, 5e-324, 2.2e-16):
        source = "{% if x == y %}T{% else %}F{% endif %}"
        ctx = {"x": False, "y": value}
        assert djust_says(source, ctx) == "F", value
        assert django_says(source, ctx) == "F", value


def test_a_bool_is_not_equal_to_an_integer_beyond_f64_precision() -> None:
    """The other #2243 trap: the fix must not be `as f64`.

    Vacuous for a bool on its own — 0 and 1 are nowhere near 2^53 — but it pins
    that the bool path reaches `int_eq_float` rather than some new float cast.
    """
    for lit in (9007199254740993, 2**62 + 1, 2**63 - 1):
        source = "{%% if x == %d %%}T{%% else %%}F{%% endif %%}" % lit
        for value in (True, False):
            ctx = {"x": value}
            assert djust_says(source, ctx) == "F", (lit, value)
            assert django_says(source, ctx) == "F", (lit, value)


# ---------------------------------------------------------------------------
# The equivalence — the real pin.
# ---------------------------------------------------------------------------


def _sweep_operands() -> list:
    """Right-hand operands, curated edges plus a seeded random sample.

    Deliberately NOT filtered: NaN, sub-epsilon Decimals, strings and `None` all
    stay in, because the assertion below is `bool ≡ its integer` rather than
    `bool == Django`, and that holds for every one of them.
    """
    rng = random.Random(2244)
    operands = [
        0,
        1,
        -1,
        2,
        19,
        2**53,
        2**53 + 1,
        2**62 + 1,
        2**63 - 1,
        -(2**63),
        True,
        False,
        0.0,
        -0.0,
        1.0,
        0.5,
        -0.5,
        1.5,
        1e-17,
        5e-324,
        2.2e-16,
        0.1 + 0.2 - 0.3,
        1e300,
        -1e300,
        float(2**53),
        float("inf"),
        float("-inf"),
        float("nan"),
        Decimal("0"),
        Decimal("1"),
        Decimal("1.0"),
        Decimal("-1"),
        Decimal("0.5"),
        Decimal("1E-30"),
        Decimal("19.99"),
        "1",
        "0",
        "True",
        "a",
        None,
        [0, 1],
        (1,),
    ]
    for _ in range(25):
        operands.append(rng.randint(-1000, 1000))
        operands.append(rng.uniform(-1000.0, 1000.0))
        operands.append(float(rng.randint(-3, 3)))
        operands.append(Decimal(rng.randint(-1000, 1000)) / Decimal(rng.choice([1, 2, 4])))
    return operands


def test_a_bool_answers_exactly_as_its_integer_does() -> None:
    """The invariant the fix establishes, swept over the full cross product.

    `bool` subclasses `int`, so Python answers identically for `True` and `1`
    and for `False` and `0` — for EVERY operator and EVERY other operand, with
    no exceptions. After the fix djust does too, because it substitutes the
    integer and re-enters the same arms.

    This subsumes a Django table and outlives it: the cases where djust still
    disagrees with Django (NaN ordering, sub-epsilon Decimal equality, the
    `>=`/`<=`-on-incomparables answer) are `(Integer, *)` divergences that
    predate #2244 and are out of its scope (#1079). A bool inherits them
    verbatim, so when they are fixed for integers they are fixed for bools and
    this test needs no edit. Before the fix it failed in the great majority of
    cases; see `test_the_equivalence_is_what_the_fix_established` for the
    in-suite gate-off sibling.
    """
    operands = _sweep_operands()
    checked = 0
    mismatches = []

    for op in OPS:
        for template in BOTH_ORDERS_VARS:
            source = template.format(op=op)
            for flag, integer in ((True, 1), (False, 0)):
                for other in operands:
                    checked += 1
                    as_bool = djust_says(source, {"x": flag, "y": other})
                    as_int = djust_says(source, {"x": integer, "y": other})
                    if as_bool != as_int:
                        mismatches.append((source, flag, other, as_bool, as_int))

    assert checked > 2000, f"sweep degenerated to {checked} cases"
    assert not mismatches, (
        f"{len(mismatches)}/{checked} bool answers differ from the same "
        f"integer's: {mismatches[:10]}"
    )


def test_the_equivalence_is_what_the_fix_established() -> None:
    """The in-suite gate-off sibling for the equivalence above (#1468/#2135).

    The equivalence test asserts a property that is now everywhere true, which
    on its own says nothing about whether the property is hard to satisfy. This
    asserts the same property holds for the specific operands that USED to break
    it — `bool` against a number, in both directions and every operator — so
    that removing either substitution turns both tests red rather than only the
    Django ones. Before the fix these are exactly the cases that disagreed.
    """
    broke_before = (0, 1, -1, 0.0, 1.0, 0.5, Decimal("0"), Decimal("1"), True, False)
    disagreements = 0
    for op in OPS:
        for template in BOTH_ORDERS_VARS:
            source = template.format(op=op)
            for flag, integer in ((True, 1), (False, 0)):
                for other in broke_before:
                    disagreements += 1
                    assert djust_says(source, {"x": flag, "y": other}) == djust_says(
                        source, {"x": integer, "y": other}
                    ), (source, flag, other)
    assert disagreements == 6 * 2 * 2 * len(broke_before)


def test_differential_against_django_over_bool_numeric_pairs() -> None:
    """Every bool × numeric-operand × operator × operand-order, vs Django.

    Restricted to the operand types Django and djust already agree on for an
    INTEGER, since the equivalence test above is what covers the rest — this is
    the parity half, and the exclusions are derived by MEASURING the integer's
    own agreement rather than hand-listed, so it cannot silently drift into
    excusing a new bool-only divergence.
    """
    operands = _sweep_operands()
    checked = 0
    skipped_int_already_diverges = 0
    mismatches = []

    for op in OPS:
        for template in BOTH_ORDERS_VARS:
            source = template.format(op=op)
            compiled = DjangoTemplate(source)
            for flag, integer in ((True, 1), (False, 0)):
                for other in operands:
                    int_ctx = {"x": integer, "y": other}
                    if djust_says(source, int_ctx) != compiled.render(DjangoContext(int_ctx)):
                        # A pre-existing `(Integer, *)` divergence (#1079).
                        skipped_int_already_diverges += 1
                        continue
                    ctx = {"x": flag, "y": other}
                    checked += 1
                    if djust_says(source, ctx) != compiled.render(DjangoContext(ctx)):
                        mismatches.append((source, flag, other))

    assert checked > 2000, f"sweep degenerated to {checked} cases"
    assert not mismatches, (
        f"{len(mismatches)}/{checked} disagree with Django "
        f"({skipped_int_already_diverges} skipped where the integer also "
        f"disagrees): {mismatches[:10]}"
    )


def test_bool_literals_against_numeric_context_values() -> None:
    """The literal side of the sweep: `{% if x == True %}` on a number.

    `True`/`False` reach the comparison as `Value::Bool` from the parser rather
    than from the Python converter, so this exercises a different entry point to
    the same arms.
    """
    values = [0, 1, -1, 2, 0.0, 1.0, 0.5, True, False, Decimal("0"), Decimal("1")]
    checked = 0
    mismatches = []
    for op in OPS:
        for template in BOTH_ORDERS:
            for lit in ("True", "False"):
                source = template.format(op=op, lit=lit)
                compiled = DjangoTemplate(source)
                for value in values:
                    ctx = {"x": value}
                    checked += 1
                    if djust_says(source, ctx) != compiled.render(DjangoContext(ctx)):
                        mismatches.append((source, value))
    assert checked == 6 * 2 * 2 * len(values)
    assert not mismatches, f"{len(mismatches)}/{checked} disagree with Django: {mismatches[:10]}"


# ---------------------------------------------------------------------------
# The divergences a bool now INHERITS, pinned as inherited rather than correct.
# ---------------------------------------------------------------------------


def test_a_bool_inherits_the_integer_arms_nan_answer() -> None:
    """A bool answers whatever its integer answers against a NaN — now `False`.

    #2244 pinned this as an INHERITED divergence: the `(Integer, Float)` arm's
    epsilon comparison yielded 1 for any NaN pair, so `>` and `>=` were true
    where Django says false, for `1` exactly as for `True`. It said "pinned so
    that the day the integer arm is fixed, this test says so".

    #2349 fixed the integer arm — `order_floats` returns `None` for a NaN,
    which makes all four ordering operators false — so the second half of this
    test inverts and now asserts AGREEMENT.

    The first half is unchanged and is the part worth keeping either way: the
    bool substitution must land a bool on the SAME arm as its integer, whatever
    that arm answers. That is #2244's actual claim, and it holds before and
    after the NaN fix.
    """
    nan = float("nan")
    for flag, integer in ((True, 1), (False, 0)):
        for op in OPS:
            source = "{%% if x %s y %%}T{%% else %%}F{%% endif %%}" % op
            assert djust_says(source, {"x": flag, "y": nan}) == djust_says(
                source, {"x": integer, "y": nan}
            ), (op, flag)
    source = "{% if x > y %}T{% else %}F{% endif %}"
    assert django_says(source, {"x": True, "y": nan}) == "F"
    assert djust_says(source, {"x": True, "y": nan}) == "F", (
        "the bool path has diverged from the integer path again — #2349 made "
        "every NaN ordering false through `order_floats`"
    )


def test_a_bool_inherits_the_decimal_arms_epsilon() -> None:
    """`False == Decimal('1E-30')` is true here and false in Django — as `0` is.

    The `is_decimal_pair` arm compares within `f64::EPSILON` (#2214), and a bool
    now reaches it by substitution. `test_decimal_equality_is_untouched` in
    `test_float_int_equality_2243.py` pins the same answer for the integer.
    """
    tiny = Decimal("1E-30")
    source = "{% if x == y %}T{% else %}F{% endif %}"
    assert djust_says(source, {"x": False, "y": tiny}) == djust_says(source, {"x": 0, "y": tiny})
    assert djust_says(source, {"x": False, "y": tiny}) == "T"
    assert django_says(source, {"x": False, "y": tiny}) == "F"
