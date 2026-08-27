"""`floatformat` and `add` are decimal arithmetic, not float formatting (#2253).

#2253 was filed against four cells the #2239 differential measured, and named
one cause for all four: ``Value::Decimal``'s digit string being parsed through
``f64``. Reproducing it first (the four cells are pinned verbatim below) and
then widening the differential to the whole surface corrected the premise twice:

* **``floatformat`` was wrong on far more than ``Decimal``.** A sweep of 21
  argument forms x 475 values found **302 divergent cells of 825** on the
  curated half alone, and the ``Decimal`` precision loss is one of *four*
  independent causes. The other three are about every input type: Rust's
  ``{:.n$}`` rounds a binary double half-to-EVEN where Django quantizes the
  decimal digits half-UP (``2.675|floatformat:2`` is ``2.68``, not ``2.67``);
  Django's default argument is ``-1`` and a negative argument means "at most",
  which ``"-3".parse::<usize>()`` could not express so every negative argument
  silently became one place; and the ``g`` suffix was stripped and then ignored.
  Fixing only the two cited cells was not possible without the ``p <= 0``
  branch and exact quantization, which is most of the algorithm.

* **``add``'s cited cell is not the ``f64`` parse.** ``12345678901234567890``
  does not fit an ``i64`` however exactly it is computed, so the sum overflowed
  ``checked_add`` and the filter returned its input unchanged. The ``f64`` parse
  IS a second, real defect — it loses a digit from 2^53 upward
  (``Decimal('9007199254740993')|add:1`` gave back 9007199254740993) — but
  widening the truncation alone would not have closed the reported cell. Both
  are fixed: the truncation is exact and the arithmetic is arbitrary-precision,
  with a sum outside ``i64`` carried as exact digits. (#2253 shipped this as an
  ``i128`` carrying ``Value::Decimal``; #2260 removed the width and moved the
  carrier to ``Value::BigInt``, which is what an ``int`` + ``int`` returns.)

Every assertion here is a **differential against real Django**, and the curated
table is paired with a randomized sweep, because a table samples the axis you
thought of (v1.1.1-2 retro). Django is importable in this suite, so there is no
reason to hand-write an expectation.

What is deliberately still divergent is enumerated in
``TestKnownRemainingDivergences`` rather than being left for a reader to
discover.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.test import override_settings  # noqa: E402

from djust import _rust  # noqa: E402
from djust.render_env import apply_number_format  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402


@pytest.fixture(autouse=True)
def _push_number_format():
    """The real render path pushes the active locale's number format to Rust.

    Without it `localize_number_forced` is a no-op and the `g` suffix cannot be
    observed at all — the assertions below would pass for the wrong reason.
    """
    apply_number_format()


def render_both(source: str, value: Any) -> tuple[str, str]:
    """`(django, djust)` for one cell, rendering the SAME value through both."""
    django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
    djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
    return django_out, djust_out


def assert_agrees(source: str, value: Any) -> None:
    django_out, djust_out = render_both(source, value)
    assert djust_out == django_out, (
        f"{source} on {value!r}: django={django_out!r} djust={djust_out!r}"
    )


#: 29 significant digits — the value from #2239/#2253, where a double holds ~15.
HUGE = Decimal("12345678901234567890.123456789")

#: The four cells #2253 reported, verbatim from its table.
REPORTED = [
    ("{{ p|floatformat }}", HUGE, "12345678901234567890.1"),
    ("{{ p|floatformat:2 }}", HUGE, "12345678901234567890.12"),
    ("{{ p|add:1 }}", HUGE, "12345678901234567891"),
    ("{{ p|floatformat }}", Decimal("0.00"), "0"),
]

#: Every argument form Django's own docstring names, plus the ones that were
#: silently mis-parsed. Quoted and unquoted both, because `int(arg)` behaves
#: differently on a string than on a float literal.
ARGS = [
    None,
    '"0"',
    '"1"',
    '"2"',
    '"3"',
    '"5"',
    '"-1"',
    '"-2"',
    '"-3"',
    '"u"',
    '"2u"',
    '"g"',
    '"2g"',
    '"-2g"',
    '"2gu"',
    '"2ug"',
    '"x"',
    "0",
    "2",
    "-2",
]

#: Shapes chosen so that at least one exercises each branch of the algorithm:
#: the `p <= 0` drop, a half-way tie in both directions, a carry that grows the
#: integer part, a sign that rounds away, exponent forms, the 200-digit cut-off,
#: the non-finite forms, and the non-numeric give-ups.
VALUES = [
    Decimal("19.99"),
    Decimal("0.00"),
    Decimal("-3.5"),
    HUGE,
    Decimal("34.23234"),
    Decimal("34.00000"),
    Decimal("34.26000"),
    Decimal("0.5"),
    Decimal("1.5"),
    Decimal("2.5"),
    Decimal("2.675"),
    Decimal("-0.5"),
    Decimal("-2.5"),
    Decimal("-0.04"),
    Decimal("0.005"),
    Decimal("-0.005"),
    Decimal("0.004999"),
    Decimal("6666.6666"),
    Decimal("10000"),
    Decimal("1E+3"),
    Decimal("1E-3"),
    Decimal("-0.0"),
    Decimal("0"),
    Decimal("100"),
    Decimal("999.999"),
    Decimal("-999.999"),
    Decimal("9.995"),
    Decimal("1.05"),
    Decimal("1.15"),
    Decimal("1.25"),
    Decimal("9007199254740993"),
    Decimal("-9007199254740993"),
    Decimal("1E+250"),
    Decimal("1E-250"),
    Decimal("1E+400"),
    Decimal("0E-30"),
    Decimal("-1.000"),
    19.99,
    0.0,
    -0.0,
    -3.5,
    2.675,
    0.5,
    2.5,
    0.125,
    1e20,
    1e-7,
    0,
    1,
    -1,
    10000,
    123456789,
    2**62,
    -(2**62),
    9223372036854775807,
    "34.23234",
    "abc",
    "",
    "1.5",
    "-2",
    "1e5",
    "  7  ",
    True,
    False,
    None,
    [1, 2, 3],
    {"a": 1},
]


def _templates() -> list[str]:
    return ["{{ p|floatformat }}" if a is None else "{{ p|floatformat:%s }}" % a for a in ARGS]


class TestTheReportedCells:
    """#2253's own table, reproduced before anything was widened."""

    @pytest.mark.parametrize(
        "source,value,expected", REPORTED, ids=[r[0] + "|" + str(r[1]) for r in REPORTED]
    )
    def test_the_reported_cell_now_matches_django(
        self, source: str, value: Decimal, expected: str
    ) -> None:
        django_out, djust_out = render_both(source, value)
        # Both halves asserted: that Django really says this (the issue's table
        # is a claim, not a fact) and that djust now says it too.
        assert django_out == expected
        assert djust_out == expected


class TestFloatformatMatchesRealDjango:
    """The whole argument x value grid, as a differential."""

    @pytest.mark.parametrize("source", _templates())
    def test_every_argument_form_agrees_across_every_value(self, source: str) -> None:
        divergent = []
        for value in VALUES:
            django_out, djust_out = render_both(source, value)
            if django_out != djust_out:
                divergent.append((repr(value), django_out, djust_out))
        assert not divergent, f"{source}: {divergent}"

    def test_a_randomized_sweep_agrees(self) -> None:
        """3,000 cells the curated table did not choose.

        PR #2231's parity table enumerated every format code and still missed
        three defects that a randomized sweep found in seconds; the same
        pairing applies here, over the argument x magnitude x scale space.
        """
        rng = random.Random(22530)
        sources = _templates()
        divergent = []
        for _ in range(3000):
            source = rng.choice(sources)
            digits = rng.randint(1, 32)
            scale = rng.randint(0, 15)
            sign = "-" if rng.random() < 0.4 else ""
            raw = "".join(str(rng.randint(0, 9)) for _ in range(digits))
            value: Any = Decimal(f"{sign}{raw}E-{scale}")
            if rng.random() < 0.25:
                value = float(value) if abs(value) < Decimal("1E300") else 1.0
            elif rng.random() < 0.15 and abs(value) < 2**62:
                # Bounded to i64 on purpose: a Python `int` past that loses its
                # digits crossing into `Value::Integer`, which `{{ p }}` alone
                # already shows. See
                # `test_a_python_int_past_i64_is_lossy_before_any_filter_runs`.
                value = int(value)
            django_out, djust_out = render_both(source, value)
            if django_out != djust_out:
                divergent.append((source, repr(value), django_out, djust_out))
        assert not divergent, f"{len(divergent)} divergent cells, first 5: {divergent[:5]}"

    def test_the_four_causes_each_have_a_cell(self) -> None:
        """Named so a future reader can tell which mechanism a red line broke.

        Each row is a value+argument pair that the PREVIOUS implementation got
        wrong for exactly one reason, so removing any one of the four
        mechanisms turns exactly this test red on that row.
        """
        # 1. half-up on the digits, not half-even on the double
        assert_agrees("{{ p|floatformat:2 }}", Decimal("2.675"))
        assert_agrees("{{ p|floatformat:2 }}", 2.675)
        # 2. the default argument is -1, so an integral value drops its fraction
        assert_agrees("{{ p|floatformat }}", Decimal("0.00"))
        assert_agrees("{{ p|floatformat }}", 10000)
        # 3. the g suffix groups
        assert_agrees('{{ p|floatformat:"2g" }}', Decimal("6666.6666"))
        # 4. exact digits past what a double holds
        assert_agrees("{{ p|floatformat:2 }}", HUGE)

    def test_a_negative_argument_is_at_most_that_many_places(self) -> None:
        """The three cases from Django's own docstring."""
        for value, expected in [
            (Decimal("34.23234"), "34.232"),
            (Decimal("34.00000"), "34"),
            (Decimal("34.26000"), "34.260"),
        ]:
            django_out, djust_out = render_both('{{ p|floatformat:"-3" }}', value)
            assert django_out == expected
            assert djust_out == expected

    def test_string_and_bool_inputs_are_coerced_as_django_coerces_them(self) -> None:
        """`Decimal(str(text))`, then `Decimal(str(float(text)))`, then `""`.

        The previous implementation matched none of these — it returned any
        non-numeric `Value` unchanged.
        """
        for value, expected in [
            ("34.23234", "34.2"),
            ("  7  ", "7"),
            ("1e5", "100000"),
            (True, "1"),
            ("abc", ""),
            (None, ""),
            ([1, 2, 3], ""),
        ]:
            django_out, djust_out = render_both("{{ p|floatformat }}", value)
            assert django_out == expected, f"Django changed for {value!r}"
            assert djust_out == expected, f"djust diverges for {value!r}"


class TestAddMatchesRealDjango:
    """`int(value) + int(arg)` on exact digits, in i128."""

    @pytest.mark.parametrize(
        "value",
        [
            Decimal("9007199254740993"),
            Decimal("-9007199254740993"),
            Decimal("9223372036854775807"),
            HUGE,
            Decimal("19.99"),
            Decimal("-19.99"),
            Decimal("1E+3"),
            Decimal("1E-3"),
            Decimal("0.00"),
            9007199254740993,
            1e20,
            "9007199254740993",
        ],
        ids=str,
    )
    def test_add_one_agrees(self, value: Any) -> None:
        assert_agrees("{{ p|add:1 }}", value)

    def test_the_two_causes_each_have_a_cell(self) -> None:
        # The f64 truncation: exact from 2^53 up, where a double is not.
        assert_agrees("{{ p|add:1 }}", Decimal("9007199254740993"))
        # The i64 width: the sum does not fit, and used to be discarded.
        assert_agrees("{{ p|add:1 }}", Decimal("9223372036854775807"))

    def test_a_sum_past_i64_stays_exact_when_chained(self) -> None:
        """The out-of-range sum is a real value, not a rendered string.

        `Value::Decimal` carries it, so another `add` truncates it exactly and
        `floatformat` formats it exactly — which is what Django's `int` does.
        """
        assert_agrees("{{ p|add:1|add:1 }}", HUGE)
        assert_agrees('{{ p|add:1|floatformat:"2" }}', HUGE)

    def test_a_randomized_sweep_agrees(self) -> None:
        rng = random.Random(22531)
        divergent = []
        for _ in range(2000):
            digits = rng.randint(1, 24)
            scale = rng.randint(0, 10)
            sign = "-" if rng.random() < 0.4 else ""
            raw = "".join(str(rng.randint(0, 9)) for _ in range(digits))
            value = Decimal(f"{sign}{raw}E-{scale}")
            arg = rng.choice(["1", "0", "-1", "2", "1000000000000000000"])
            source = "{{ p|add:%s }}" % arg
            django_out, djust_out = render_both(source, value)
            if django_out != djust_out:
                divergent.append((source, repr(value), django_out, djust_out))
        assert not divergent, f"{len(divergent)} divergent, first 5: {divergent[:5]}"


class TestKnownRemainingDivergences:
    """Stated, not left to be discovered.

    Each is either a Django behaviour djust deliberately does not reproduce, or
    a gap that belongs to a different filter. Asserted as facts so that closing
    one turns this file red, which is the signal to prune the entry (#1125).

    It worked: three entries went red when #2258/#2260/#2265 landed. They are
    kept, inverted to assert AGREEMENT and named after what closed them, rather
    than deleted — a gap that was stated and then closed is worth a pin, and
    deleting it would leave nothing to go red if the fix regressed.
    """

    def test_python_ints_are_unbounded_and_add_is_too(self) -> None:
        """CLOSED by #2260 — kept as a pin, per this class's own contract.

        This entry read "past i128, `add` returns its input rather than
        guessing … matching Django needs arbitrary-precision arithmetic, which
        this fix does not add." #2260 added it: `add` computes on digit strings,
        so the only remaining give-up is an operand `int()` itself refuses.
        """
        django_out, djust_out = render_both("{{ p|add:1 }}", Decimal("1E+250"))
        assert len(django_out) == 251
        assert djust_out == django_out

        # The give-up that DOES remain, one width further out: past CPython's
        # `sys.get_int_max_str_digits()` Django raises `ValueError` (its
        # `except (ValueError, TypeError)` is around the `%` in `stringformat`,
        # not around `add`'s `int()`), and djust renders the input rather than
        # 500ing. Never a fabricated number.
        assert (
            _rust.render_template(
                "{{ p|add:1 }}", normalize_django_value({"p": Decimal("1E+5000")})
            )
            == "1e+5000"
        )
        with pytest.raises(ValueError):
            DjangoTemplate("{{ p|add:1 }}").render(DjangoContext({"p": Decimal("1E+5000")}))

    def test_add_still_returns_the_value_rather_than_djangos_empty_string(self) -> None:
        """Django's third branch is `return ""`; djust returns the input.

        Predates #2253 and is documented in the filter itself: turning a
        rendered value into silent emptiness on upgrade is the failure class
        this engine keeps having to fix.
        """
        django_out, djust_out = render_both('{{ p|add:"a" }}', Decimal("19.99"))
        assert django_out == ""
        assert djust_out == "19.99"

    def test_an_empty_floatformat_argument_raises_in_django_and_not_here(self) -> None:
        """`arg[-1]` on `""` is an IndexError in Django 5.2 — a Django bug.

        djust does not reproduce crashes; the empty argument is treated as an
        absent one.
        """
        with pytest.raises(IndexError):
            DjangoTemplate('{{ p|floatformat:"" }}').render(DjangoContext({"p": Decimal("1.55")}))
        assert (
            _rust.render_template(
                '{{ p|floatformat:"" }}', normalize_django_value({"p": Decimal("1.55")})
            )
            # Treated as the absent argument, i.e. Django's `p = -1`.
            == "1.6"
        )

    def test_a_python_int_past_i64_survives_the_value_boundary(self) -> None:
        """CLOSED by #2260 — this entry named where the fix belonged.

        It read: "a Python `int` wider than an `i64` does not survive the
        crossing into `Value::Integer`; it arrives as a double … the fix
        belongs at the value boundary, not in a filter." `Value::BigInt` is
        that boundary fix, and `floatformat` needed no change: it was already
        faithful to the value it was handed, which is now the exact one.
        """
        big = -17475672789612459955425
        bare_django, bare_djust = render_both("{{ p }}", big)
        assert bare_django == "-17475672789612459955425"
        assert bare_djust == bare_django, "the int must survive `{{ p }}` exactly"
        # The filter is still faithful to the value it receives — which is the
        # point: the digits it formats are the real ones now.
        ff_django, ff_djust = render_both("{{ p|floatformat }}", big)
        assert ff_djust == ff_django

    def test_a_float_nan_no_longer_has_a_display_gap(self) -> None:
        """CLOSED by #2258 — this entry named the change that would close it.

        It read: "`Value::Float`'s `Display` writes Rust's `NaN` where Python
        writes `nan` … widening it to `Display` would change every render of a
        NaN and belongs to its own change." #2258 is that change: `Display`
        routes through `python_float_repr` and then Django's own
        `numberformat.format` rules, so the bare render agrees too.
        """
        assert_agrees("{{ p }}", float("nan"))
        assert_agrees("{{ p|floatformat }}", float("nan"))
        # The other two shapes #2258 named, in the same place.
        assert_agrees("{{ p }}", 1e300)
        assert_agrees("{{ p }}", float("inf"))

    def test_the_u_suffix_now_honours_overridden_number_settings(self) -> None:
        """CLOSED by #2266 — this entry was a divergence and is now agreement.

        It used to assert the gap: with `DECIMAL_SEPARATOR="!"`, Django gave
        `6666!67` for `{{ p|floatformat:"2u" }}` and djust gave `6666.67`,
        because only the LOCALIZED number format was pushed to Rust while
        Django's `u` (i.e. `use_l10n=False`) re-reads the RAW settings. It did
        its job: closing the gap turned it red rather than letting it pass
        unnoticed, and it is kept here — flipped to the agreeing direction —
        rather than deleted, so the four measured cells stay pinned.

        `render_env.apply_number_format` now pushes BOTH triples and
        `floatformat::finish` selects on `use_l10n`. The localized rows were
        already correct and are re-asserted so the fix is shown not to have
        moved them.

        The number format is a Rust thread-local, so it is restored
        unconditionally: leaking an overridden separator into the rest of the
        worker is a real incident from this repo's history.
        """
        try:
            with override_settings(
                DECIMAL_SEPARATOR="!", THOUSAND_SEPARATOR="_", NUMBER_GROUPING=3
            ):
                apply_number_format()
                value = Decimal("6666.6666")
                for source, expected in (
                    ('{{ p|floatformat:"2u" }}', "6666!67"),
                    ('{{ p|floatformat:"2gu" }}', "6_666!67"),
                ):
                    d, u = render_both(source, value)
                    assert d == expected, f"Django changed for {source}: {d!r}"
                    assert u == expected, f"{source} regressed: {u!r}"
                # The localized forms, which were never affected — asserting
                # them keeps the fix bounded to `u` rather than being a general
                # change to how numbers render.
                for source in ('{{ p|floatformat:"2" }}', '{{ p|floatformat:"2g" }}'):
                    assert_agrees(source, value)
        finally:
            apply_number_format()

    def test_a_string_value_is_still_float_coerced_by_add(self) -> None:
        """`int("34.2")` raises in Python, so Django concatenates and then `""`.

        djust's `add` allows a float coercion on the VALUE side regardless of
        its type. Predates #2253 (it is #2203's `float_ok` rule) and is
        untouched by it; the divergence is about `str`, not about `Decimal`.
        """
        django_out, djust_out = render_both("{{ p|add:1 }}", "34.23234")
        assert django_out == ""
        assert djust_out == "35"
