"""``{% if a >= b %}`` on a pair Python cannot order (#2338).

The divergence
--------------
``compare_values`` returned ``-1 | 0 | 1`` and collapsed "these two cannot be
ordered" into ``0``. For ``>`` and ``<`` that reproduced Django exactly —
Python raises ``TypeError``, ``smart_if``'s operator lets it propagate,
``IfNode`` catches it and the branch resolves False, and ``0`` makes both of
those false. ``>=`` and ``<=`` read the same ``0`` as *equal* and answered
**True**::

    {% if p >= q %}Y{% else %}N{% endif %}   p = "a", q = 1
      django  'N'
      djust   'Y'

Per-pair, not per-type: a string against an int, a list against a tuple, a
dict against anything, two ``None``s, an absent variable against another. And
it fails in the permissive direction — a ``{% if x >= threshold %}`` gate
opens on operands that have no ordering at all.

The fix
-------
``compare_values`` is gone; ``try_compare(a, b) -> Option<i32>`` replaced it
and all four operator arms consume the ``Option`` via ``is_some_and``. There
is deliberately no ``i32`` wrapper left: #2335 briefly carried one and removed
it before merge precisely because, with every caller reading only the ``i32``,
the ``Option`` was observationally equivalent to ``0`` — a second mechanism
shadowing the first (CLAUDE.md v1.1.1-2, "when two mechanisms overlap, delete
the redundant one").

Two shapes a curated table would plausibly get wrong, and which the randomised
sweep below settles by running Django:

* **Both directions.** Fixing ``>=`` to False and leaving ``<=`` answering
  True is the same bug mirrored (#1646). Every cell here is swept for all six
  operators, so neither arm can be fixed alone.
* **Per-element propagation.** ``[[], 'a', ('b',)] >= [1]`` is False in
  Python: the walk reaches an unequal, unorderable pair and the whole
  comparison raises — the length never enters it. Reading that element as a
  *tie* and continuing falls through to the length tie-break and answers True,
  which is exactly the bug #2335's first draft shipped on the ``>`` side. So
  ``try_compare`` propagates ``None`` out of the whole walk rather than
  returning ``Some(0)`` for it.

``==`` / ``!=`` are a **separate question** and are unchanged: Python answers
those for any pair (``"a" == 1`` is False, no raise), they route through
``values_equal`` rather than ``try_compare``, and ``Missing``/``None`` are
deliberately EQUAL there — Django's ``ignore_failures`` resolves an absent
variable to ``None``, so ``{% if a == b %}`` over two undefined names is True
on both engines. The two functions disagree about that pair on purpose, and
``test_equality_is_not_collateral`` pins it.

Method
------
The load-bearing assertion is a randomised differential against LIVE Django,
not the curated table: the curated cells are doc-claim pins, one per sentence
above. A curated table over an N-shape corpus samples one axis and blinds you
on the next.
"""

from __future__ import annotations

import copy
import itertools
import random
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

#: Every operator that reaches a comparison, so a fix to one arm cannot be
#: mistaken for a fix to its mirror.
OPS = ("==", "!=", "<", ">", "<=", ">=")

#: The ordering operators specifically — the four `try_compare` feeds.
ORDER_OPS = ("<", ">", "<=", ">=")


def both(op: str, p, q) -> tuple[str, str]:
    """Both engines on ``{% if p <op> q %}``, a raise recorded as an outcome.

    ``q`` is deep-copied so the two operands are never the same object:
    Python's ``==`` answers True on identity alone for a list, and a corpus
    that could only compare a value to itself would not be able to tell a
    structural comparison from an identity one.
    """
    src = "{%% if p %s q %%}Y{%% else %%}N{%% endif %%}" % op
    ctx = {"p": p, "q": copy.deepcopy(q)}
    try:
        d = DjangoTemplate(src).render(DjangoContext(dict(ctx)))
    except Exception as exc:  # noqa: BLE001
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        r = _rust.render_template(src, dict(ctx))
    except Exception as exc:  # noqa: BLE001
        r = f"<<EXC {type(exc).__name__}>>"
    return d, r


def assert_agrees(op: str, p, q) -> None:
    d, r = both(op, p, q)
    assert r == d, f"{{% if p {op} q %}} on p={p!r} q={q!r}: django={d!r} djust={r!r}"


# ===========================================================================
# The issue's own cells
# ===========================================================================


class TestTheIssueTable:
    """Every pair #2338 quotes, verbatim, plus the two the brief added."""

    CITED = [
        ("a", 1),  # the issue's `>=` cell
        ([1], (1,)),  # the issue's `<=` cell
        ([], "a"),  # the brief's three
        ({"x": 1}, 3),
        ("s", 5),
    ]

    @pytest.mark.parametrize("p,q", CITED)
    @pytest.mark.parametrize("op", ORDER_OPS)
    def test_every_ordering_operator_agrees(self, op, p, q) -> None:
        assert_agrees(op, p, q)

    @pytest.mark.parametrize("p,q", CITED)
    def test_ge_and_le_both_answer_false(self, p, q) -> None:
        # The named claim, spelled out rather than left implicit in the sweep:
        # BOTH directions are False, so neither arm was fixed alone.
        for op in (">=", "<="):
            d, r = both(op, p, q)
            assert (d, r) == ("N", "N"), f"{op} on {p!r} / {q!r}: django={d!r} djust={r!r}"

    def test_the_reverse_order_is_false_too(self) -> None:
        """Swapping the operands cannot make an unorderable pair orderable."""
        for p, q in self.CITED:
            for op in ORDER_OPS:
                assert_agrees(op, q, p)


class TestPerElementPropagation:
    """An incomparable ELEMENT makes the whole sequence comparison false.

    The shape #2335's first draft got wrong on the ``>`` side, and which a
    ``Some(0)`` return would have re-opened one operator over: reading the
    element as a tie continues the walk and falls through to the length
    tie-break, so a 3-element list "beats" a 1-element one.
    """

    def test_an_unorderable_element_is_not_a_tie(self) -> None:
        p, q = [[], "a", ("b",)], [1]
        for op in ORDER_OPS:
            d, r = both(op, p, q)
            assert (d, r) == ("N", "N"), f"{op}: django={d!r} djust={r!r}"
        # The length tie-break is what a tie would have fallen through to, and
        # `p` is the longer list — so a `>=` reading the element as equal
        # answers Y. Django answers N.

    def test_an_EQUAL_unorderable_element_still_continues_the_walk(self) -> None:
        """The other half of Python's rule, and the one a blunt "any dict
        element means incomparable" fix would break: two dicts never need to
        be ORDERED when they are equal, so the walk moves past them.
        """
        for op in OPS:
            assert_agrees(op, [{}, 1], [{}, 2])
            assert_agrees(op, [{"a": 1}, 1], [{"a": 1}, 2])

    def test_a_prefix_is_still_smaller(self) -> None:
        for op in OPS:
            assert_agrees(op, [1], [1, 2])
            assert_agrees(op, [1, 2], [1])
            assert_agrees(op, [1, 2], [1, 2])

    def test_a_nested_incomparable_pair_propagates_from_any_depth(self) -> None:
        for op in ORDER_OPS:
            assert_agrees(op, [[1, "a"]], [[1, 2]])
            assert_agrees(op, [[[{}]]], [[[1]]])


class TestTheNullPairs:
    """``None`` / absent variables, the pair most likely to look like a tie.

    Python's ``None < None`` RAISES, so Django answers False for all four
    ordering operators — but ``values_equal`` calls them EQUAL, because
    Django's ``ignore_failures`` resolution makes ``{% if a == b %}`` over two
    undefined names True. ``try_compare`` and ``values_equal`` disagree about
    this pair on purpose.
    """

    def test_two_nones_cannot_be_ordered(self) -> None:
        for op in ORDER_OPS:
            d, r = both(op, None, None)
            assert (d, r) == ("N", "N"), f"{op} on None/None: django={d!r} djust={r!r}"

    def test_two_absent_variables_cannot_be_ordered(self) -> None:
        for op in ORDER_OPS:
            src = "{%% if nope_a %s nope_b %%}Y{%% else %%}N{%% endif %%}" % op
            d = DjangoTemplate(src).render(DjangoContext({}))
            r = _rust.render_template(src, {})
            assert (d, r) == ("N", "N"), f"{op} on two absent names: django={d!r} djust={r!r}"

    def test_none_against_a_number(self) -> None:
        for op in ORDER_OPS:
            assert_agrees(op, None, 1)
            assert_agrees(op, 1, None)

    def test_equality_is_not_collateral(self) -> None:
        """``==`` over the null pair stays True — the arm this PR did NOT
        touch, and the one a blunt "None is incomparable" fix would break.
        """
        assert both("==", None, None) == ("Y", "Y")
        src = "{% if nope_a == nope_b %}Y{% else %}N{% endif %}"
        assert DjangoTemplate(src).render(DjangoContext({})) == "Y"
        assert _rust.render_template(src, {}) == "Y"


class TestTheOrderableOperandsStillOrder:
    """``None`` must not swallow the pairs that DO compare — the direction a
    fix to an incomparable-pair bug is most likely to overshoot in.
    """

    #: Every numeric operand here is EXACTLY representable in ``f64``, and
    #: deliberately so. djust widens a ``Decimal`` / ``BigInt`` through ``f64``
    #: with an ``f64::EPSILON`` tolerance, so ``Decimal('19.99') == 19.99`` and
    #: ``2**70 == 2**70 + 1`` both answer True where Django answers False.
    #: Those are pre-existing precision divergences with their own issues
    #: (#2214, #2260) and their own tests — measured identically on the
    #: pre-#2338 and post-#2338 builds — and putting one in this table would
    #: make it fail for a reason that has nothing to do with #2338. ``1.5`` and
    #: ``2**70`` are exact; ``19.99`` and ``2**70 + 1`` are not.
    ORDERED = [
        (1, 2),
        (2, 1),
        (1, 1),
        (1, 1.0),
        (Decimal("1.5"), 1.5),
        (Decimal("1.5"), 2),
        (2**70, 2**71),
        (2**70, 1),
        (True, 0),
        (True, 1),
        (False, True),
        ("a", "b"),
        ("b", "b"),
        ([1], [2]),
        ((1,), (2,)),
        ([1, 2], [1, 2]),
    ]

    @pytest.mark.parametrize("p,q", ORDERED)
    @pytest.mark.parametrize("op", OPS)
    def test_agrees(self, op, p, q) -> None:
        assert_agrees(op, p, q)

    def test_at_least_one_ge_still_answers_true(self) -> None:
        """Non-vacuity: if the fix had made ``>=`` unconditionally False, every
        assertion above would still be an agreement for the pairs that answer
        N — so pin that a real ``>=`` still fires.
        """
        assert both(">=", 2, 1) == ("Y", "Y")
        assert both(">=", 1, 1) == ("Y", "Y")
        assert both("<=", [1, 2], [1, 2]) == ("Y", "Y")


class TestThroughEveryOperandChannel:
    """The operator arms read `get_value`, so a literal and a filtered
    expression reach ``try_compare`` the same way a bare variable does.
    """

    def test_a_literal_operand(self) -> None:
        for src, ctx in (
            ("{% if p >= 5 %}Y{% else %}N{% endif %}", {"p": "a"}),
            ('{% if p >= "a" %}Y{% else %}N{% endif %}', {"p": 5}),
            ("{% if p <= 5 %}Y{% else %}N{% endif %}", {"p": [1]}),
        ):
            d = DjangoTemplate(src).render(DjangoContext(dict(ctx)))
            r = _rust.render_template(src, dict(ctx))
            assert (d, r) == ("N", "N"), f"{src} on {ctx!r}: django={d!r} djust={r!r}"

    def test_a_filtered_operand(self) -> None:
        src = "{% if p|length >= q %}Y{% else %}N{% endif %}"
        ctx = {"p": [1, 2], "q": "a"}
        d = DjangoTemplate(src).render(DjangoContext(dict(ctx)))
        r = _rust.render_template(src, dict(ctx))
        assert (d, r) == ("N", "N"), f"django={d!r} djust={r!r}"
        # ...and still answers when the pair IS orderable, so the assertion
        # above is not passing because the filter path is broken outright.
        ctx = {"p": [1, 2], "q": 1}
        assert DjangoTemplate(src).render(DjangoContext(dict(ctx))) == "Y"
        assert _rust.render_template(src, dict(ctx)) == "Y"


# ===========================================================================
# The load-bearing assertion
# ===========================================================================


class TestRandomisedAgainstLiveDjango:
    """A randomised differential, not a table.

    The curated cells above are doc-claim pins. This is the assertion that
    would catch a variant nobody wrote down — which is how #2335's tie-break
    bug was found, in 27 of 28,500 cells with no curated case of the shape.
    """

    #: Exactly-representable numerics only, for the reason
    #: ``TestTheOrderableOperandsStillOrder.ORDERED`` gives: the ``f64``
    #: widening's epsilon is a pre-existing divergence (#2214, #2260) that
    #: would redden this sweep for a reason unrelated to #2338. Non-finite
    #: floats are excluded for the same reason and pinned at #2349.
    ATOMS = [
        0,
        1,
        -1,
        2,
        2**70,
        0.0,
        1.0,
        2.5,
        Decimal("1.5"),
        True,
        False,
        None,
        "",
        "a",
        "b",
        "1",
        "<img src=x onerror=alert(1)>",
    ]

    @classmethod
    def _value(cls, rng: random.Random, depth: int = 0):
        if depth >= 2 or rng.random() < 0.5:
            return rng.choice(cls.ATOMS)
        n = rng.randint(0, 3)
        kind = rng.choice(["list", "tuple", "dict"])
        if kind == "dict":
            keys = rng.sample(["a", "b", "c", "0"], k=min(n, 4))
            return {k: cls._value(rng, depth + 1) for k in keys}
        items = [cls._value(rng, depth + 1) for _ in range(n)]
        return items if kind == "list" else tuple(items)

    def test_random_comparisons_agree_with_django(self) -> None:
        rng = random.Random(2338)
        cells, bad = 0, []
        for _ in range(1200):
            p, q = self._value(rng), self._value(rng)
            for op in OPS:
                cells += 1
                d, r = both(op, p, q)
                if d != r:
                    bad.append((op, p, q, d, r))
        assert cells == 1200 * len(OPS)
        assert not bad, f"{len(bad)}/{cells} disagree, first three: {bad[:3]!r}"

    def test_the_sweep_actually_reaches_incomparable_pairs(self) -> None:
        """Non-vacuity for the sweep above (#1468 / #1859): a differential that
        never constructs the shape under test is green for the wrong reason.

        Counts, on Django alone, how many of the same cells are pairs Python
        refuses to order — asserting the corpus reaches them at all, and in
        quantity rather than once by luck.
        """
        rng = random.Random(2338)
        unorderable = 0
        for _ in range(1200):
            p, q = self._value(rng), self._value(rng)
            try:
                p >= q  # noqa: B015 — the probe IS the expression
            except TypeError:
                unorderable += 1
        assert unorderable > 100, (
            f"only {unorderable}/1200 sampled pairs are unorderable — the "
            "randomised sweep is barely exercising the bug it exists for"
        )


# ===========================================================================
# Structural pins
# ===========================================================================


class TestTheOptionIsTheOnlyMechanism:
    """#1859: a pin is decorative unless it is load-bearing.

    The ``Option`` only buys anything if the operators CONSUME it. A
    ``compare_values(a, b) -> i32`` wrapper — or any ``unwrap_or(0)`` at a
    call site — silently restores the collapse while every test above stays
    green for ``>`` and ``<`` and goes red only for ``>=`` / ``<=``. This pins
    the shape so the regression is a source-level failure too.
    """

    RENDERER = (
        Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "renderer.rs"
    )

    def test_no_i32_returning_comparison_helper_exists(self) -> None:
        src = self.RENDERER.read_text()
        assert "fn try_compare(a: &Value, b: &Value) -> Option<i32>" in src
        assert "fn compare_values" not in src, (
            "an i32-returning comparison helper is back — it is the mechanism "
            "#2338 removed, and it shadows the Option (v1.1.1-2 retro)"
        )

    def test_no_call_site_re_collapses_the_option(self) -> None:
        src = self.RENDERER.read_text()
        for bad in ("try_compare(&left, &right).unwrap_or(", "try_compare(x, y).unwrap_or("):
            assert bad not in src, f"{bad!r} re-collapses None into a tie"

    def test_all_four_ordering_arms_consume_the_option(self) -> None:
        """A count pin, not a floor (#1125): exactly four operator arms feed
        ``try_compare``, and each reads it through ``is_some_and``. A fifth
        arm added without the ``Option`` would be the #1646 drift.
        """
        src = self.RENDERER.read_text()
        arms = [
            "try_compare(&left, &right).is_some_and(|c| c >= 0)",
            "try_compare(&left, &right).is_some_and(|c| c <= 0)",
            "try_compare(&left, &right).is_some_and(|c| c > 0)",
            "try_compare(&left, &right).is_some_and(|c| c < 0)",
        ]
        for arm in arms:
            assert src.count(arm) == 1, f"expected exactly one {arm!r}"
        assert src.count("try_compare(&left, &right)") == len(arms), (
            "an operator arm calls try_compare without going through one of "
            "the four is_some_and forms above"
        )

    def test_the_walk_propagates_rather_than_returning_a_tie(self) -> None:
        src = self.RENDERER.read_text()
        start = src.index("fn try_compare(")
        body = src[start : src.index("\n}\n", start)]
        assert "return try_compare(x, y);" in body, (
            "the per-element walk no longer propagates the element's own "
            "answer — including its None, which is what stops an incomparable "
            "element being read as a tie"
        )
        assert "(Value::Missing, Value::Missing) => Some(0)" not in body
        assert "(Value::Missing, Value::Missing) => 0" not in body


class TestTheCorpusMeasuresBothArms:
    """The differential script's comparison axis must sweep ``<=`` and ``>=``.

    It already listed them (#2335 added the axis with all seven operators) —
    this pins that they stay, because the axis is what measures this fix and a
    corpus gap is silent by construction.
    """

    SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"

    def test_the_comparison_axis_carries_every_operator(self) -> None:
        src = self.SCRIPT.read_text()
        for op in (*OPS, "in"):
            assert f'"{op}"' in src, f"the comparison axis is missing {op!r}"


# ===========================================================================
# Scope discipline
# ===========================================================================


class TestKnownAdjacentDivergenceNotFixedHere:
    """#1079: found while fixing #2338, measured, filed — not fixed."""

    def test_non_finite_floats_still_diverge(self) -> None:
        """The epsilon idiom is undefined for a non-finite float (#2349).

        A DIFFERENT mechanism from #2338 and out of scope for it: a NaN is not
        a pair Python refuses to order — ``float("nan") >= float("nan")``
        raises nothing, it evaluates to ``False`` — so ``try_compare`` is never
        asked the question this PR taught it to answer. Both operands reach the
        ``(Float, Float)`` arm and get a real ordering back, because
        ``(a - b).abs() < f64::EPSILON`` is false for NaN and the chain falls
        through its ``else`` to "greater".

        The same idiom is why ``inf == inf`` is False: ``(inf - inf)`` is NaN,
        so the equality test answers "not equal" for two values Python calls
        equal. Six sites across ``try_compare`` and ``values_equal`` spell it.

        Measured pre-existing rather than assumed: the same probe against the
        pre-#2338 build reports 28 divergent non-finite cells to this build's
        26, and the 26 are identical. The 2 this PR closed are ``nan`` against
        a ``str`` — a genuinely incomparable pair, asserted below.

        Delete this test when #2349 is fixed.
        """
        nan = float("nan")
        for op in (">", ">="):
            d, r = both(op, nan, nan)
            assert (d, r) == ("N", "Y"), (
                f"NaN's `{op}` now agrees with Django — the float arms have "
                "been fixed, so delete this test and close #2349"
            )
        d, r = both("==", float("inf"), float("inf"))
        assert (d, r) == ("Y", "N"), "`inf == inf` now agrees with Django — close #2349"
        # `<` and `<=` already agree for two NaNs, which is what makes this a
        # fallthrough in two arms rather than a whole missing mechanism.
        for op in ("<", "<=", "!="):
            assert_agrees(op, nan, nan)

    def test_the_incomparable_nan_pairs_ARE_fixed_here(self) -> None:
        """The line between #2338 and #2349, asserted rather than described.

        A NaN against a STRING is a pair Python refuses to order — it raises,
        exactly like ``"a" >= 1`` — so it is #2338's bug and is fixed. A NaN
        against a NUMBER is not, and is #2349's.
        """
        nan = float("nan")
        for op in ORDER_OPS:
            assert_agrees(op, nan, "a")
            assert_agrees(op, "a", nan)


class TestTheOldPinIsGone:
    """#2338's own pin named itself as the thing to delete."""

    PINNED_IN = (
        Path(__file__).resolve().parent / "test_dict_iteration_and_sequence_equality_2334_2335.py"
    )

    def test_the_2335_pin_no_longer_asserts_the_divergence(self) -> None:
        src = self.PINNED_IN.read_text()
        assert "test_an_incomparable_pair_still_answers_true_for_le_and_ge" not in src, (
            "#2335's pin still asserts the divergence exists — it names itself "
            "as the thing to delete when #2338 is fixed"
        )


# A cheap guard against the file drifting into a single-operator test.
def test_this_module_sweeps_both_directions() -> None:
    assert set(ORDER_OPS) == {"<", ">", "<=", ">="}
    assert set(OPS) >= set(ORDER_OPS) | {"==", "!="}
    assert len(list(itertools.product(OPS, repeat=1))) == 6
