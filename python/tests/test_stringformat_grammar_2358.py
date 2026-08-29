"""`stringformat` is CPython's `%`-format grammar, not a last-character switch (#2358).

The bug
-------
Django's filter body is::

    if isinstance(value, tuple):
        value = str(value)
    try:
        return ("%" + str(arg)) % value
    except (ValueError, TypeError):
        return ""

So the argument is not a conversion character — it is the TAIL of a printf
format string. djust dispatched on ``spec.chars().last()`` and fell to
``_ => value.to_string()`` for every character it had no arm for. That single
arm held two disjoint groups and was wrong for both, and a third group was
wrong inside an arm that *was* implemented.

**Group 1 — specs CPython rejects, where Django answers ``""``.** djust was
MORE PERMISSIVE than Django on every row: it rendered where Django renders
nothing.

===========  ==========================================  =======  ==============
spec         CPython                                     Django   djust (before)
===========  ==========================================  =======  ==============
``"5"``      ``ValueError: incomplete format``           ``''``   ``'42'``
``"."``      ``ValueError: incomplete format``           ``''``   ``'42'``
``"-"``      ``ValueError: incomplete format``           ``''``   ``'42'``
``"0"``      ``ValueError: incomplete format``           ``''``   ``'42'``
``".2"``     ``ValueError: incomplete format``           ``''``   ``'42'``
``"l"``      ``ValueError: incomplete format``           ``''``   ``'42'``
``"%"``      ``TypeError: not all arguments converted``   ``''``   ``'42'``
bare ``True``  ``ValueError: unsupported format 'T'``    ``''``   ``'5.000000e0'``
===========  ==========================================  =======  ==============

**Group 2 — conversions CPython supports and djust did not implement**:
``x``, ``X``, ``o``, ``c``, ``r``, ``a``, ``g``, ``G``, ``u``, plus the
trailing LITERAL (``"ss"`` is ``%s`` followed by the letter ``s``, so Django
answers ``'42s'``).

**Group 3 — an implemented arm with the wrong output format**: ``%e`` writes
its exponent with a sign and at least two digits (``'4.200000e+01'``); Rust's
``{:e}`` writes neither (``'4.200000e1'``).

Why the fix is a grammar and not another arm
---------------------------------------------
Turning the catch-all into ``""`` fixes group 1 and BREAKS group 2; leaving it
fixes neither. Value-by-value patching of that arm is exactly the
non-convergence CLAUDE.md's #2129 rule names. So the shape is the scanner
itself, in ``crates/djust_templates/src/stringformat.rs``: scan ``"%" + spec``
the way CPython scans a format string, and let each conversion's own argument
rule decide.

Four rules a randomised sweep found that reading the docs would not have
--------------------------------------------------------------------------
1. ``%%`` is an early-out checked BEFORE the flags — ``"%+%"`` is
   ``unsupported format character '%'``, not a flagged literal percent.
2. A **list** suppresses the unconsumed-argument check exactly as a dict does,
   because CPython's guard is ``PyMapping_Check`` (has ``mp_subscript``) and a
   list has one.
3. The mapping key resolves immediately after it is parsed, before the flags —
   ``"%()" % {'a': 1}`` is a ``KeyError``, not ``incomplete format``.
4. Python's ``0`` flag is not C's: ``"%08.5d" % 42`` is ``'00000042'``
   (zero-padding survives a precision) and ``"%05.2f" % inf`` is ``'00inf'``
   (it reaches a non-finite).

The bounded residue
-------------------
Two shapes make Django **raise a 500** where djust renders ``""`` — a ``*``
width over a value larger than a machine integer, and ``%d``/``%c`` on a value
CPython cannot make an integer of. Both directions render ``""``, which is
strictly less permissive than a raise, and both predate this change. Pinned in
``TestTheRaiseResidueIsNamed``.
"""

from __future__ import annotations

import itertools
import random
from decimal import Decimal

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:  # pragma: no cover - import-time bootstrap
    settings.configure(
        DEBUG=False,
        USE_I18N=False,
        USE_TZ=False,
        TIME_ZONE="UTC",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"context_processors": []},
            }
        ],
        INSTALLED_APPS=[],
    )
    django.setup()

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

SRC = "{{ p|stringformat:q }}"
XSS = "<img src=x onerror=alert(1)>"


def both(value, spec: str) -> tuple[str, str]:
    ctx = {"p": value, "q": spec}
    try:
        d = DjangoTemplate(SRC).render(DjangoContext(ctx))
    except BaseException as exc:  # noqa: BLE001
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        r = _rust.render_template(SRC, ctx)
    except BaseException as exc:  # noqa: BLE001
        r = f"<<EXC {type(exc).__name__}>>"
    return d, r


def assert_agrees(value, spec: str) -> None:
    d, r = both(value, spec)
    assert r == d, f"{spec!r} on {value!r}: django={d!r} djust={r!r}"


# ===========================================================================
# The three groups the issue measured.
# ===========================================================================


class TestGroupOneSpecsCPythonRejects:
    """Django answers ``""``; djust echoed the value. The permissiveness half."""

    @pytest.mark.parametrize("spec", ["5", ".", "-", "0", ".2", "l", "%", "True"])
    def test_renders_nothing(self, spec: str) -> None:
        d, r = both(42, spec)
        assert d == "", f"Django moved: {spec!r} is now {d!r}"
        assert r == "", f"{spec!r} renders {r!r}, where Django renders nothing"

    def test_the_empty_spec_still_renders_nothing(self) -> None:
        """#2343's row, which used to PANIC. Kept reachable from here."""
        d, r = both(42, "")
        assert d == ""
        assert r == ""


class TestGroupTwoConversionsCPythonSupports:
    @pytest.mark.parametrize(
        ("value", "spec", "expected"),
        [
            (42, "x", "2a"),
            (255, "X", "FF"),
            (42, "o", "52"),
            (65, "c", "A"),
            ("a", "r", "&#x27;a&#x27;"),
            # The trailing LITERAL: `%s` followed by the letter `s`.
            (42, "ss", "42s"),
            # The other conversions the catch-all swallowed.
            (42, "u", "42"),
            (42, "g", "42"),
            (1000000, "G", "1E+06"),
            ("héllo", "a", "&#x27;h\\xe9llo&#x27;"),
        ],
    )
    def test_conversion(self, value, spec: str, expected: str) -> None:
        d, r = both(value, spec)
        assert d == expected, f"Django moved: {spec!r} on {value!r} is now {d!r}"
        assert r == expected, f"{spec!r} on {value!r}: django={d!r} djust={r!r}"


class TestGroupThreeTheExponentFormat:
    """CPython always writes the exponent's sign and at least two digits."""

    @pytest.mark.parametrize(
        ("value", "spec", "expected"),
        [
            (42, "e", "4.200000e+01"),
            (42, "E", "4.200000E+01"),
            (1e300, "e", "1.000000e+300"),
            (1e-300, "e", "1.000000e-300"),
            (0.0, "e", "0.000000e+00"),
            (42, ".0e", "4e+01"),
        ],
    )
    def test_exponent(self, value, spec: str, expected: str) -> None:
        d, r = both(value, spec)
        assert d == expected, f"Django moved: {spec!r} on {value!r} is now {d!r}"
        assert r == expected


# ===========================================================================
# The four grammar rules the sweep found, each with its own case.
# ===========================================================================


class TestTheFourGrammarRulesTheSweepFound:
    def test_percent_percent_is_an_early_out_above_the_flags(self) -> None:
        """``%+%`` is an unsupported conversion, not a flagged literal."""
        # `%%` immediately after the `%` — and the argument goes unconsumed,
        # which for an int is `not all arguments converted`.
        assert both(42, "%")[1] == ""
        # A LIST suppresses that check, so the literal survives (rule 2).
        assert both([1, 2], "%")[1] == "%"
        # With a flag in between, it is no longer the early-out.
        assert both([1, 2], "+%")[1] == ""
        assert_agrees(42, "%")
        assert_agrees([1, 2], "%")
        assert_agrees([1, 2], "+%")

    def test_a_list_suppresses_the_unconsumed_argument_check_like_a_dict(self) -> None:
        """``PyMapping_Check`` is "has ``mp_subscript``", which a list has."""
        # `"%" + "%abc"` is `"%%abc"` — a literal percent, then `abc`. A str
        # is in the `!PyUnicode_Check` exclusion, so it does NOT suppress.
        for value, expect in [([1, 2], "%abc"), ({"a": 1}, "%abc"), (42, ""), ("s", "")]:
            d, r = both(value, "%abc")
            assert d == expect, f"Django moved for {value!r}: {d!r}"
            assert r == expect, f"{value!r}: django={d!r} djust={r!r}"

    def test_the_mapping_key_resolves_before_the_flags(self) -> None:
        assert_agrees({"a": 7}, "(a)d")
        assert_agrees({"a": 7}, "(a)5d")
        # Not a mapping -> `format requires a mapping`, caught, "".
        assert both(42, "(a)d") == ("", "")

    @pytest.mark.parametrize(
        ("value", "spec", "expected"),
        [
            # Python zero-pads THROUGH a precision, where C ignores the flag.
            (42, "08.5d", "00000042"),
            (42, "8.5d", "   00042"),
            (-42, "05d", "-0042"),
            (42, "+05d", "+0042"),
            # ...and reaches a non-finite, which C also declines to do.
            (float("inf"), "05.2f", "00inf"),
            (float("inf"), "5.2f", "  inf"),
            # The `0` flag is IGNORED for `%s`.
            ("ab", "05s", "   ab"),
        ],
    )
    def test_pythons_zero_flag_is_not_cs(self, value, spec: str, expected: str) -> None:
        d, r = both(value, spec)
        assert d == expected, f"Django moved: {spec!r} on {value!r} is now {d!r}"
        assert r == expected


class TestTheTupleIsStringifiedFirst:
    """``if isinstance(value, tuple): value = str(value)``, load-bearing for ``%r``."""

    def test_repr_of_a_tuple_is_the_repr_of_its_string(self) -> None:
        d, r = both((1, 2), "r")
        assert d == "&#x27;(1, 2)&#x27;", f"Django moved: {d!r}"
        assert r == d

    def test_str_of_a_tuple_is_unchanged_by_that(self) -> None:
        assert_agrees((1, 2), "s")


# ===========================================================================
# Exactness on the numeric conversions, past what a f64 can hold.
# ===========================================================================


class TestTheIntegerConversionsAreExactPastF64:
    """A saturating cast is the #2265 class: a fabricated constant, silently."""

    @pytest.mark.parametrize("spec", ["d", "i", "u", "x", "X", "o"])
    def test_two_to_the_seventy(self, spec: str) -> None:
        assert_agrees(2**70, spec)
        assert_agrees(-(2**70), spec)

    def test_a_value_past_two_to_the_fiftythree_keeps_every_digit(self) -> None:
        """The #2265 boundary: a double holds ~15 digits and this has 19."""
        assert_agrees(9007199254740993, "d")
        assert_agrees(9007199254740993, "x")

    @pytest.mark.parametrize(
        ("value", "spec"),
        [(1.5, "x"), (Decimal("2.5"), "x"), ("12", "d"), (None, "d"), ([1], "d")],
    )
    def test_the_argument_rule_refuses_what_cpython_refuses(self, value, spec) -> None:
        """``%x`` takes an INT only — ``"%x" % 1.5`` is a ``TypeError``.

        And a numeric STRING is not an int to ``%d``, which is what keeps
        ``{{ "12"|stringformat:"d" }}`` empty rather than rendering ``12``.
        """
        d, r = both(value, spec)
        assert d == "", f"Django moved: {spec!r} on {value!r} is now {d!r}"
        assert r == ""


class TestTheAlternateFlagAndTheGeneralFormat:
    @pytest.mark.parametrize(
        ("value", "spec", "expected"),
        [
            (42, "#x", "0x2a"),
            (255, "#X", "0XFF"),
            (42, "#o", "0o52"),
            (42, "#08x", "0x00002a"),
            (42, "#.5x", "0x0002a"),
            # `#` on `%g` keeps the trailing zeros the strip would remove...
            (42, "#g", "42.0000"),
            (42, "g", "42"),
            # ...and reaches the sub-format's own alternate point.
            (0, "#.g", "0."),
            (1e-300, "#.G", "1.E-300"),
            (1.0, "#.0f", "1."),
            (5.0, "#.0e", "5.e+00"),
        ],
    )
    def test_alternate(self, value, spec: str, expected: str) -> None:
        d, r = both(value, spec)
        assert d == expected, f"Django moved: {spec!r} on {value!r} is now {d!r}"
        assert r == expected


class TestPrecisionMeansDifferentThingsPerConversion:
    @pytest.mark.parametrize(
        ("value", "spec", "expected"),
        [
            # A minimum DIGIT COUNT for the integers, which never empties.
            (-7, ".2i", "-07"),
            (255, ".5x", "000ff"),
            (0, ".0d", "0"),
            (0, ".3d", "000"),
            # A truncation for the text conversions...
            ("abc", ".1s", "a"),
            ("abc", ".2r", "&#x27;a"),
            # ...but NOT for `%c`, alone among them.
            (65, ".0c", "A"),
            (65, ".c", "A"),
        ],
    )
    def test_precision(self, value, spec: str, expected: str) -> None:
        d, r = both(value, spec)
        assert d == expected, f"Django moved: {spec!r} on {value!r} is now {d!r}"
        assert r == expected


class TestTheWidthAndPrecisionLIMITSDifferFromEachOther:
    """Width is a ``Py_ssize_t`` and precision an ``int`` — bisected in #2294."""

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("9223372036854775808s", ""),
            (".2147483648s", ""),
            (".2147483647s", "abcdef"),
            ("00000000000000000000010s", "    abcdef"),
            # PAST 19 digits, which is where #2294's `len() > 19` guard used
            # to answer and where `parse::<u64>` now does. Gating that guard
            # off changed nothing the suite could see, because no case
            # reached it — a redundant mechanism, deleted rather than tested
            # around, with the behaviour covered here instead.
            ("99999999999999999999s", ""),
            ("100000000000000000000s", ""),
            # Leading zeros still do not count toward the limit, so a
            # 30-character field is width 9.
            ("00000000000000000000000000009s", "   abcdef"),
        ],
    )
    def test_limits(self, spec: str, expected: str) -> None:
        d, r = both("abcdef", spec)
        assert d == expected, f"Django moved: {spec!r} is now {d!r}"
        assert r == expected

    def test_a_width_that_would_allocate_the_heap_is_in_the_raise_residue(self) -> None:
        """``"%9223372036854775807s"`` is a ``MemoryError`` in Django.

        Which its filter does not catch, so Django 500s. Rust's default OOM
        handler ABORTS the process — worse than either — so `pad` degrades to
        the unpadded body via `try_reserve`. Named here rather than left as a
        silent shrug.
        """
        d, r = both("abcdef", "9223372036854775807s")
        assert d == "<<EXC MemoryError>>", f"Django moved: {d!r}"
        assert r == "abcdef"


# ===========================================================================
# The residue, named rather than silent.
# ===========================================================================


class TestTheRaiseResidueIsNamed:
    """Django raises a 500 here; djust renders ``""``.

    Strictly LESS permissive, and it predates #2358 — the old catch-all
    reached ``""`` on the same inputs by a different route. Pinned so it
    cannot be mistaken for something this change was supposed to have closed,
    and so it goes red the day either engine moves.
    """

    @pytest.mark.parametrize(
        ("value", "spec"),
        [
            # `*` reads its width from the argument list; a value past `isize`
            # overflows the read before the conversion char is even checked.
            (2**70, "*d"),
            (-(2**70), "*s"),
            # `%d` cannot make an integer of an infinity.
            (float("inf"), "d"),
            (float("-inf"), "i"),
            # `%c` needs a code point, and this is past `0x10FFFF`.
            (10_000_000, "c"),
            (2**70, "c"),
        ],
    )
    def test_django_raises_and_djust_renders_empty(self, value, spec: str) -> None:
        d, r = both(value, spec)
        assert d.startswith("<<EXC "), f"Django no longer raises for {spec!r}: {d!r}"
        assert r == "", f"{spec!r} on {value!r} now renders {r!r}"

    def test_a_nan_is_NOT_in_the_residue(self) -> None:
        """``%d`` on a NaN is a ``ValueError``, which Django DOES catch.

        Without this row the class above would read as "every non-finite
        raises", and the two halves really do differ.
        """
        d, r = both(float("nan"), "d")
        assert d == "", f"Django moved: {d!r}"
        assert r == ""

    def test_a_missing_mapping_key_is_in_the_residue(self) -> None:
        d, r = both({"a": 1}, "(zzz)s")
        assert d.startswith("<<EXC "), f"Django no longer raises: {d!r}"
        assert r == ""


# ===========================================================================
# The sweep. A curated table samples the axis you noticed.
# ===========================================================================

_VALUES = {
    "int": 42,
    "negint": -7,
    "zero": 0,
    "bigint": 2**70,
    "negbig": -(2**70),
    "float": 1.5,
    "negfloat": -1.5,
    "floatzero": 0.0,
    "bigfloat": 1e300,
    "smallfloat": 1e-300,
    "str": "abc",
    "numstr": "12",
    "empty": "",
    "none": None,
    "true": True,
    "false": False,
    "list": [1, 2],
    "dict": {"a": 1},
    "tuple": (1, 2),
    "dec": Decimal("2.5"),
    "inf": float("inf"),
    "ninf": float("-inf"),
    "nan": float("nan"),
    "uni": "héllo→",
    "chr": 65,
    "big_chr": 10_000_000,
    "xss": XSS,
}
_ALPHABET = list("sdioxXeEfFgGcra%*.-+ #0123456789hlLbnzT()")


def _is_raise_residue(django_out: str) -> bool:
    """The one excused class: Django 500s and djust renders ``""``.

    Computed from Django's own outcome rather than from a spec pattern, so a
    NEW raising shape is excused automatically and a WRONG VALUE never is.
    """
    return django_out.startswith("<<EXC ")


class TestARandomisedDifferentialAgainstLiveDjango:
    """The curated tables above sample the axis; this samples the space.

    Its own preconditions are asserted: a sweep where almost every Django
    answer is empty reports the same zero as one that agrees everywhere.
    """

    def test_thirty_thousand_random_specs(self) -> None:
        rng = random.Random(2358)
        names = list(_VALUES)
        nonempty = 0
        residue = 0
        mismatches: list[str] = []
        for _ in range(30000):
            spec = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 6)))
            vname = rng.choice(names)
            d, r = both(_VALUES[vname], spec)
            if d and not d.startswith("<<EXC"):
                nonempty += 1
            if d == r:
                continue
            if _is_raise_residue(d) and r == "":
                residue += 1
                continue
            mismatches.append(f"spec={spec!r} value={vname} django={d!r} djust={r!r}")
        assert nonempty >= 3000, (
            f"only {nonempty} of 30000 cells produced a non-empty Django answer — "
            "the sweep is not reaching the surface it claims to measure"
        )
        assert residue >= 50, (
            f"the raise-residue exclusion fired {residue} times — if it is 0 the "
            "sweep never builds the shape it excuses, and the exclusion is "
            "unfalsifiable rather than bounded"
        )
        assert not mismatches, f"{len(mismatches)} of 30000 cells diverge; first 10:\n" + "\n".join(
            mismatches[:10]
        )

    def test_every_spec_of_length_two_over_the_whole_alphabet(self) -> None:
        """Exhaustive, not sampled: 41² specs × 27 values."""
        checked = 0
        mismatches: list[str] = []
        for a, b in itertools.product(_ALPHABET, repeat=2):
            spec = a + b
            for vname, value in _VALUES.items():
                checked += 1
                d, r = both(value, spec)
                if d == r or (_is_raise_residue(d) and r == ""):
                    continue
                mismatches.append(f"spec={spec!r} value={vname} django={d!r} djust={r!r}")
        assert checked == len(_ALPHABET) ** 2 * len(_VALUES)
        assert not mismatches, (
            f"{len(mismatches)} of {checked} cells diverge; first 10:\n"
            + "\n".join(mismatches[:10])
        )

    def test_no_spec_makes_djust_emit_markup_django_escaped(self) -> None:
        """The direction constraint, over the same space."""
        rng = random.Random(23582)
        leaks: list[str] = []
        for _ in range(8000):
            spec = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 5)))
            for value in (XSS, [XSS], {"a": XSS}, (XSS,)):
                d, r = both(value, spec)
                if "<img" in r and "<img" not in d:
                    leaks.append(f"spec={spec!r} value={value!r} django={d!r} djust={r!r}")
        assert not leaks, "djust emitted live markup Django escaped:\n" + "\n".join(leaks[:10])
