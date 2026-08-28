"""``inf`` and ``nan`` compare as Python compares them (#2349).

The bug
-------
Every arm that compared two floats spelled the ordering as::

    if (a - b).abs() < f64::EPSILON { 0 } else if a < b { -1 } else { 1 }

and that idiom is **undefined for a non-finite operand**. ``(inf - inf)`` is
NaN, every comparison against NaN is false, so the tolerance answered "not
equal" and the chain fell through its ``else`` to "greater". Consequences,
measured against Django 5.2.16 as 26 divergent cells:

* ``{% if inf == inf %}`` took the ``{% else %}`` branch. Django and Python say
  True, and ``float("inf")`` is an ordinary value a view can hold.
* every NaN pair answered **True** for ``>`` and ``>=``, where Python answers
  False for all four operators — including ``nan > 1`` and ``1 > nan``.

Why this is NOT #2338
---------------------
#2338 was about pairs Python **refuses to order**: ``"a" >= 1`` raises
``TypeError``, Django's ``{% if %}`` catches it, the branch resolves False, and
``try_compare`` answers ``None``. A NaN is not such a pair —
``float("nan") >= float("nan")`` raises nothing, it evaluates and returns
``False`` — so ``try_compare`` was never asked the question #2338 taught it to
answer. Same vehicle (``None`` means "all four operators are false", which IS
Python's answer for a NaN), different mechanism reaching it.

``is_nan``, not ``!is_finite``
------------------------------
``±inf`` orders NORMALLY in Python: ``-inf < 1 < inf`` are all True. A guard on
``!is_finite`` would trade 22 divergent cells for a different set.
:class:`TestInfinityStillOrders` is the half that would catch that.

The equality half was right only BY ACCIDENT
--------------------------------------------
``nan == nan`` is False, which the epsilon produced — but for the same
undefined-comparison reason that made ``inf == inf`` wrong. A future change to
the tolerance would have flipped the NaN answer silently, with no test failing.
``floats_equal`` makes it intentional: for a non-finite operand, IEEE ``==`` is
Python's answer.

Six sites, one statement each
-----------------------------
Four ordering arms (``(Float, Float)``, ``(Integer, Float)``,
``(Float, Integer)`` and the ``numeric_pair`` wildcard that a ``Decimal`` or a
``BigInt`` reaches) and two equality arms (``(Float, Float)`` and the
``is_decimal_pair`` wildcard). All six spelled the same idiom — the "N similar
sites need N tests" shape (#1104) — and all six now call ``order_floats`` or
``floats_equal``. :class:`TestOneStatementOfTheRule` pins the caller SET so a
seventh site cannot appear with a seventh copy (#1125).

Out of scope, and reported rather than fixed (#1079)
----------------------------------------------------
Whether the epsilon should exist for FINITE floats at all: ``0.1 + 0.2 == 0.3``
is True in djust and **False** in Python and Django. That is #2243, it is
pre-existing, and this PR keeps it byte for byte —
:class:`TestTheFiniteEpsilonIsUnchanged` asserts it, both because the scope
boundary should be executable and because a "fix" that quietly changed finite
behaviour would otherwise be invisible here.
"""

from __future__ import annotations

import itertools
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

INF = float("inf")
NINF = float("-inf")
NAN = float("nan")

OPS = ("==", "!=", "<", ">", "<=", ">=")

#: Every operand kind that can reach the six sites. The `Decimal` entries are
#: not decoration: a `Decimal` against a float takes the `numeric_pair`
#: WILDCARD rather than any typed arm, and that wildcard is one of the six —
#: without them two of the sites are unreachable from this file.
OPERANDS: dict[str, Any] = {
    "inf": INF,
    "-inf": NINF,
    "nan": NAN,
    "int": 1,
    "float": 1.0,
    "zero": 0,
    "neg": -1,
    "decimal": Decimal("1"),
    "bigint": 10**30,
    "dec-inf": Decimal("Infinity"),
    "dec-nan": Decimal("NaN"),
    "bool": True,
}

#: The pairs the matrix is about. A pair of ordinary finite numbers is #2243's
#: question, not this one.
_NON_FINITE_NAMES = {"inf", "-inf", "nan", "dec-inf", "dec-nan"}


def _both(op: str, p: Any, q: Any) -> tuple[str, str]:
    source = "{%% if p %s q %%}Y{%% else %%}N{%% endif %%}" % op
    try:
        django_out = DjangoTemplate(source).render(DjangoContext({"p": p, "q": q}))
    except Exception as exc:  # noqa: BLE001
        django_out = "<%s>" % type(exc).__name__
    try:
        djust_out = _rust.render_template(source, {"p": p, "q": q})
    except Exception as exc:  # noqa: BLE001
        djust_out = "<%s>" % type(exc).__name__
    return django_out, djust_out


def _assert_agrees(op: str, p: Any, q: Any) -> None:
    django_out, djust_out = _both(op, p, q)
    assert djust_out == django_out, f"{p!r} {op} {q!r}: django={django_out!r} djust={djust_out!r}"


class TestTheExactCellsTheIssueReported:
    """The sharpest cells, spelled out rather than only swept."""

    def test_inf_equals_inf(self) -> None:
        django_out, djust_out = _both("==", INF, INF)
        assert django_out == "Y", "Python's own answer changed; re-derive"
        assert djust_out == django_out

    def test_negative_inf_equals_itself(self) -> None:
        _assert_agrees("==", NINF, NINF)

    def test_inf_is_not_equal_to_negative_inf(self) -> None:
        _assert_agrees("==", INF, NINF)
        _assert_agrees("!=", INF, NINF)

    def test_nan_is_not_equal_to_itself(self) -> None:
        """Right before this PR too — but by accident, which is the point.

        The epsilon answered False for the same undefined-comparison reason
        that made ``inf`` wrong, so a change to the tolerance would have
        flipped this silently. It is now the explicit answer of an explicit
        branch.
        """
        django_out, djust_out = _both("==", NAN, NAN)
        assert django_out == "N", "Python's own answer changed; re-derive"
        assert djust_out == django_out

    @pytest.mark.parametrize("op", OPS)
    def test_every_operator_on_two_nans(self, op: str) -> None:
        _assert_agrees(op, NAN, NAN)

    @pytest.mark.parametrize("op", OPS)
    @pytest.mark.parametrize("other", [1, 1.0, 0, -1, INF, NINF, Decimal("1"), 10**30])
    def test_every_operator_with_one_nan_operand(self, op: str, other: Any) -> None:
        """Both directions: ``nan OP x`` and ``x OP nan``.

        Both are needed because the fix touches ``(Integer, Float)`` and
        ``(Float, Integer)`` as separate arms; a one-directional test covers
        one of them.
        """
        _assert_agrees(op, NAN, other)
        _assert_agrees(op, other, NAN)


class TestInfinityStillOrders:
    """The half a ``!is_finite`` guard would break.

    ``-inf < 1 < inf`` are all True in Python. If the fix had guarded on
    "non-finite" rather than "NaN", every one of these would answer False and
    the PR would have traded one set of wrong cells for another.
    """

    @pytest.mark.parametrize("op", OPS)
    @pytest.mark.parametrize("other", [1, 1.0, 0, -1, Decimal("1"), 10**30, True])
    def test_infinity_orders_against_a_finite_value(self, op: str, other: Any) -> None:
        _assert_agrees(op, INF, other)
        _assert_agrees(op, other, INF)
        _assert_agrees(op, NINF, other)
        _assert_agrees(op, other, NINF)

    @pytest.mark.parametrize("op", OPS)
    def test_the_two_infinities_order_against_each_other(self, op: str) -> None:
        _assert_agrees(op, INF, NINF)
        _assert_agrees(op, NINF, INF)


class TestTheFullNonFiniteMatrix:
    """Every non-finite pair x every operator, exhaustively.

    Not a sample: the operand table is small enough to enumerate, and the axis
    that hid this from the two-build differential was precisely that its corpus
    had no non-finite input at all.
    """

    def test_every_cell_agrees(self) -> None:
        mismatched, cells = [], 0
        for (an, a), (bn, b) in itertools.product(OPERANDS.items(), repeat=2):
            if an not in _NON_FINITE_NAMES and bn not in _NON_FINITE_NAMES:
                continue
            for op in OPS:
                cells += 1
                django_out, djust_out = _both(op, a, b)
                if django_out != djust_out:
                    mismatched.append(
                        f"p={an} {op} q={bn}: django={django_out!r} djust={djust_out!r}"
                    )
        # Mechanical: 12 operands, 5 of them non-finite, 6 operators.
        expected = sum(
            6
            for x, y in itertools.product(OPERANDS, repeat=2)
            if x in _NON_FINITE_NAMES or y in _NON_FINITE_NAMES
        )
        assert cells == expected, f"the matrix built {cells} cells, expected {expected}"
        assert not mismatched, "\n".join(mismatched[:30])


class TestNonFiniteInsideContainers:
    """The `numeric_pair` wildcard and the recursive sequence walk.

    A list comparison recurses through `values_equal` / `try_compare`
    element-wise, so a non-finite INSIDE a sequence reaches the same six sites
    by a different route — and `{% if [nan] == [nan] %}` is one of the cells the
    issue reported.

    Every NaN here is a FRESH `float("nan")`, deliberately: see
    :class:`TestPythonsIdentityShortcutIsNotModelled` for what happens when it
    is the same object, and why that is a different mechanism.
    """

    @pytest.mark.parametrize("op", OPS)
    @pytest.mark.parametrize("value_name", ["inf", "-inf", "nan"])
    def test_a_one_element_sequence(self, op: str, value_name: str) -> None:
        def fresh() -> float:
            return {"inf": INF, "-inf": NINF, "nan": float("nan")}[value_name]

        _assert_agrees(op, [fresh()], [fresh()])
        _assert_agrees(op, (fresh(),), (fresh(),))

    @pytest.mark.parametrize("op", OPS)
    def test_a_sequence_where_only_the_tail_is_non_finite(self, op: str) -> None:
        """The walk continues past an EQUAL prefix, so this reaches the arm
        through a different path than a one-element list does."""
        _assert_agrees(op, [1, INF], [1, INF])
        _assert_agrees(op, [1, float("nan")], [1, float("nan")])
        _assert_agrees(op, [1, INF], [1, NINF])

    def test_a_dict_value(self) -> None:
        _assert_agrees("==", {"k": INF}, {"k": INF})
        _assert_agrees("==", {"k": float("nan")}, {"k": float("nan")})


class TestPythonsIdentityShortcutIsNotModelled:
    """The remaining divergence, and why it is not this issue's (#1079).

    Python's container comparison is ``x is y or x == y`` — IDENTITY first. So
    ``[n] == [n]`` is **True** when both lists hold the SAME NaN object, and
    False when they hold two distinct ones, even though the two NaNs are
    bit-identical. ``in`` uses the same rule.

    djust cannot model this and it is not the epsilon: a Python float crossing
    the PyO3 boundary becomes an ``f64``, and a ``Value`` carries no object
    identity at all, so the two cases are indistinguishable by construction.
    djust answers as if the objects were always distinct — which is the answer
    Python gives for two separately-constructed NaNs, and the shape a view is
    far more likely to produce than an aliased one.

    Measured unchanged by this PR in both directions: the epsilon answered
    ``False`` for a NaN pair and ``floats_equal`` answers ``False`` for it too,
    so the container walk sees exactly what it saw before.

    ``inf`` is unaffected either way, because ``inf == inf`` is True on value
    alone and needs no identity shortcut — asserted below, because it is what
    bounds this to NaN.
    """

    @pytest.mark.parametrize("op,django_says", [("==", "Y"), ("!=", "N"), ("<=", "Y"), (">=", "Y")])
    def test_the_aliased_nan_cells_still_diverge(self, op: str, django_says: str) -> None:
        aliased = NAN
        django_out, djust_out = _both(op, [aliased], [aliased])
        assert django_out == django_says, "Python's own answer changed; re-derive"
        assert djust_out != django_out, (
            f"`[nan] {op} [nan]` with an ALIASED NaN now agrees — djust has "
            "gained object identity somewhere, which is a real change worth "
            "understanding; update this test and close the follow-up"
        )

    @pytest.mark.parametrize("op", ["<", ">"])
    def test_the_two_strict_operators_agree_even_when_aliased(self, op: str) -> None:
        """Not every operator diverges, which is what shows this is the
        identity rule rather than a blanket NaN failure: Python's ``<`` on two
        equal-by-identity elements falls through to the length tie-break and
        answers False, which is djust's answer too."""
        _assert_agrees(op, [NAN], [NAN])

    def test_distinct_nan_objects_agree_on_every_operator(self) -> None:
        """The half djust DOES model, and models correctly."""
        for op in OPS:
            _assert_agrees(op, [float("nan")], [float("nan")])

    def test_infinity_needs_no_identity_shortcut(self) -> None:
        """Bounds the gap to NaN: `inf == inf` is True on value alone."""
        aliased = INF
        for op in OPS:
            _assert_agrees(op, [aliased], [aliased])
            _assert_agrees(op, [float("inf")], [float("inf")])

    def test_the_in_operator_has_the_same_gap(self) -> None:
        """`in` is the third caller of `values_equal` and the same rule."""
        source = "{% if p in l %}Y{% else %}N{% endif %}"
        aliased = NAN
        assert (
            DjangoTemplate(source).render(DjangoContext({"p": aliased, "l": [aliased, 1]})) == "Y"
        )
        assert _rust.render_template(source, {"p": aliased, "l": [aliased, 1]}) == "N"
        # Distinct objects: both engines say no.
        distinct_ctx = {"p": float("nan"), "l": [float("nan"), 1]}
        assert DjangoTemplate(source).render(DjangoContext(dict(distinct_ctx))) == "N"
        assert _rust.render_template(source, dict(distinct_ctx)) == "N"


class TestRenderingIsUnchanged:
    """A non-finite float still renders as Python spells it."""

    @pytest.mark.parametrize("value", [INF, NINF, NAN])
    @pytest.mark.parametrize(
        "source",
        [
            "{{ p }}",
            "{{ p|floatformat }}",
            "{{ p|floatformat:3 }}",
            "{{ p|stringformat:'s' }}",
            "{% if p %}Y{% else %}N{% endif %}",
        ],
    )
    def test_rendering_agrees(self, value: Any, source: str) -> None:
        django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
        assert _rust.render_template(source, {"p": value}) == django_out


class TestTheFiniteEpsilonIsUnchanged:
    """The scope boundary, executable rather than described (#2243/#1079).

    The epsilon for FINITE floats is a separate and older question, and this PR
    keeps it byte for byte. These assertions record djust's CURRENT answer, not
    Django's — they diverge, deliberately — so that a change to the finite path
    made in the name of #2349 cannot pass unnoticed.
    """

    def test_a_float_residue_is_still_called_equal(self) -> None:
        django_out, djust_out = _both("==", 0.1 + 0.2, 0.3)
        assert django_out == "N", "Django's own answer changed"
        assert djust_out == "Y", (
            "the finite epsilon has changed — that is #2243, and if it was "
            "done deliberately this test is the thing to update"
        )

    def test_the_ordering_side_of_the_same_epsilon(self) -> None:
        django_out, djust_out = _both(">", 0.1 + 0.2, 0.3)
        assert (django_out, djust_out) == ("Y", "N")

    def test_two_genuinely_different_finite_floats_still_order(self) -> None:
        for op in OPS:
            _assert_agrees(op, 1.0, 2.0)
            _assert_agrees(op, 2.0, 1.0)
            _assert_agrees(op, 1.0, 1.0)


class TestRandomisedDifferential:
    """Python is importable, so ask it rather than curate a table."""

    #: FACTORIES, not values. Drawing the same object twice would trigger
    #: Python's identity shortcut inside a container and make this sweep
    #: measure `TestPythonsIdentityShortcutIsNotModelled`'s known gap instead
    #: of the epsilon — the reproduction-fidelity trap one level up.
    _POOL = [
        lambda: float("inf"),
        lambda: float("-inf"),
        lambda: float("nan"),
        lambda: 0,
        lambda: 1,
        lambda: -1,
        lambda: 2,
        lambda: 0.0,
        lambda: -0.0,
        lambda: 1.0,
        lambda: 1e308,
        lambda: -1e308,
        lambda: 5e-324,
        lambda: Decimal("1"),
        lambda: Decimal("0"),
        lambda: Decimal("Infinity"),
        lambda: Decimal("-Infinity"),
        lambda: Decimal("NaN"),
        lambda: 10**30,
        lambda: -(10**30),
        lambda: True,
        lambda: False,
    ]

    @staticmethod
    def _non_finite(value: Any) -> bool:
        if isinstance(value, float):
            return not (value == value and abs(value) != float("inf"))
        if isinstance(value, Decimal):
            return not value.is_finite()
        return False

    def test_randomised_sweep_against_django(self) -> None:
        rng = random.Random(2349)
        mismatched, checked, finite_only = [], 0, 0
        for _ in range(3000):
            p, q = rng.choice(self._POOL)(), rng.choice(self._POOL)()
            op = rng.choice(OPS)
            wrapped = rng.random() < 0.3
            lhs, rhs = ([p], [q]) if wrapped else (p, q)
            django_out, djust_out = _both(op, lhs, rhs)
            if django_out.startswith("<"):
                continue  # Django raises: #2338's question, not this one
            checked += 1
            if django_out == djust_out:
                continue
            if not (self._non_finite(p) or self._non_finite(q)):
                # Both operands finite: this is the epsilon question (#2243),
                # pinned as a deliberate divergence in
                # `TestTheFiniteEpsilonIsUnchanged` and out of scope here.
                finite_only += 1
                continue
            mismatched.append(f"{lhs!r} {op} {rhs!r}: django={django_out!r} djust={djust_out!r}")
        assert checked > 1500, f"corpus collapsed to {checked} comparable cells"
        assert not mismatched, "\n".join(sorted(set(mismatched))[:25])
        # The finite-only bucket must be non-empty, or the classifier above is
        # silently discarding nothing and the sweep is weaker than it reads.
        assert finite_only > 0, (
            "no finite-only divergence was sampled — either #2243 was fixed "
            "(update `TestTheFiniteEpsilonIsUnchanged`) or the pool no longer "
            "reaches it"
        )


class TestOneStatementOfTheRule:
    """Source pins: six sites, two helpers, no seventh copy (#1125/#1646)."""

    @staticmethod
    def _renderer() -> str:
        return (
            Path(__file__).resolve().parents[2] / "crates/djust_templates/src/renderer.rs"
        ).read_text()

    def test_both_helpers_exist(self) -> None:
        source = self._renderer()
        assert "fn order_floats(a: f64, b: f64) -> Option<i32>" in source
        assert "fn floats_equal(a: f64, b: f64) -> bool" in source

    def test_the_ordering_helper_has_four_call_sites(self) -> None:
        """The four arms the issue enumerated, counted rather than trusted."""
        source = self._renderer()
        # One definition plus four calls.
        assert source.count("order_floats(") == 5, (
            f"order_floats appears {source.count('order_floats(')} times "
            "(expected 1 definition + 4 call sites)"
        )

    def test_the_equality_helper_has_two_call_sites(self) -> None:
        source = self._renderer()
        assert source.count("floats_equal(") == 3, (
            f"floats_equal appears {source.count('floats_equal(')} times "
            "(expected 1 definition + 2 call sites)"
        )

    def test_no_arm_still_spells_the_idiom_inline(self) -> None:
        """A seventh site cannot appear with a seventh copy.

        The bare `(a - b).abs() < f64::EPSILON` may now occur ONLY inside the
        two helpers. Anywhere else it is the undefined-for-non-finite idiom
        coming back.

        Comment lines are excluded: `order_floats`'s own doc-comment QUOTES the
        idiom it replaced, and counting that would make the pin pass for the
        wrong reason — or fail when someone improves the prose.
        """
        code = [
            line
            for line in self._renderer().splitlines()
            if not line.lstrip().startswith(("//", "///"))
        ]
        occurrences = sum(line.count(".abs() < f64::EPSILON") for line in code)
        assert occurrences == 2, (
            f"`.abs() < f64::EPSILON` appears in {occurrences} CODE lines; it "
            "belongs only in order_floats and floats_equal (#1646)"
        )

    def test_the_guard_is_is_nan_and_not_is_finite(self) -> None:
        """The distinction the whole fix turns on.

        `±inf` must keep ordering normally, so the ORDERING guard is `is_nan`.
        The EQUALITY helper is the one that reads `is_finite`, because there
        IEEE `==` is the right answer for every non-finite operand.
        """
        source = self._renderer()
        start = source.index("fn order_floats(")
        body = source[start : source.index("\n}", start)]
        assert "is_nan()" in body
        assert "!a.is_finite() || !b.is_finite()" not in body, (
            "the ordering guard must be is_nan, not !is_finite — `-inf < 1` is True in Python"
        )
