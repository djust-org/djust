"""`date` / `time` / `add` / `pluralize` answer for a value Django gives up on (#2359).

What the issue measured
-----------------------
Ten cells about the VALUE being a ``bool`` or ``None`` — not about how that
value was spelled, proved by a bound control: replacing the literal with a
context variable holding the same Python object diverges identically.

======================  =========  ==============
template                Django     djust (before)
======================  =========  ==============
``{{ True|date }}``     ``''``     ``'True'``
``{{ None|date }}``     ``''``     ``'None'``
``{{ True|time }}``     ``''``     ``'True'``
``{{ None|add:"1" }}``  ``''``     ``'None'``
``{{ True|pluralize }}``  ``''``   ``'s'``
======================  =========  ==============

The blast radius is much wider than the bools
----------------------------------------------
Measuring the three mechanisms across 20 value shapes rather than the three
the issue names put the count at **100+ divergent cells, not 10** — and every
one had djust rendering something where Django renders nothing:

* ``date`` / ``time`` echoed the value for **every** non-date: a string, an
  int, a float, a list, a dict, a tuple, a ``Decimal``.
* ``add``'s third branch echoed for ``None``, every list, every tuple and
  every dict.
* ``pluralize`` had an ``Integer`` arm, a sequence arm and ``_ => suffix``,
  which is three of Django's four answers and never the empty one. It also had
  no comma form at all, so ``{{ n|pluralize:"y,ies" }}`` rendered the literal
  text ``y,ies``.

So the fix is per-MECHANISM and not per-value, which is the shape CLAUDE.md's
#2129 rule asks for: each filter gets Django's own failure answer, and the
bool and ``None`` rows fall out of that rather than being special-cased.

The direction this moves
-------------------------
All three previously rendered the **unfiltered input** where Django renders
nothing, which is the more permissive direction — a `{{ p|date }}` over a
string put that string on the page. The counter-argument in the code was that
"turning a rendered value into silent emptiness on upgrade is the
silent-wrong-output class this engine keeps having to fix". Measuring it
inverted the argument: the values that reach these branches are exactly the
ones Django decided have no answer, and rendering a Python repr into a page
that asked for a date is not preserving anything. The diagnostic the echo was
defending survives in the ``tracing::debug!`` both date arms still emit.

Django's four ``pluralize`` answers, and why two `except` arms are not one
--------------------------------------------------------------------------
A ``ValueError`` — a string that is not a number — falls straight to ``""``
and does **not** try ``len()``. So ``{{ "abc"|pluralize }}`` is ``''`` while
``{{ l|pluralize }}`` on a 3-list is the plural suffix, though both are
"sized things that are not numbers". Reading the two ``pass`` es as one arm is
the obvious mistake, and it is measured here rather than inferred.

What this deliberately does NOT change
---------------------------------------
* **``{% for %}`` over a non-iterable** — Django raises ``TypeError`` (a 500);
  djust renders the ``{% empty %}`` branch. Making djust 500 is a real
  product decision with a blast radius far past the bools (an ``int``, a
  ``float`` and a ``Decimal`` all raise in Django too), and it is not the
  less-permissive direction in the sense the rest of this change is. Filed
  separately; pinned in ``TestIteratingANonIterableIsNamedNotFixed``.
* **The date wire residue** — ``Value`` has no date variant, so a Python
  ``date`` reaches this renderer as its ISO string. That is why djust parses
  date strings at all, and it is why ``{{ "2020-01-01"|date }}`` cannot be
  told apart from a real date here while Django tells them apart trivially.
  Pinned in ``TestTheDateWireResidueIsNamed``.
"""

from __future__ import annotations

import datetime
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

XSS = "<img src=x onerror=alert(1)>"


def both(src: str, ctx: dict) -> tuple[str, str]:
    try:
        d = DjangoTemplate(src).render(DjangoContext(ctx))
    except BaseException as exc:  # noqa: BLE001
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        r = _rust.render_template(src, ctx)
    except BaseException as exc:  # noqa: BLE001
        r = f"<<EXC {type(exc).__name__}>>"
    return d, r


def assert_agrees(src: str, ctx: dict) -> None:
    d, r = both(src, ctx)
    assert r == d, f"{src} on {ctx!r}: django={d!r} djust={r!r}"


# ===========================================================================
# The ten cells the issue measured, each with its bound control.
# ===========================================================================

_REPORTED = [
    ("{{ True|date }}", "{{ p|date }}", True),
    ("{{ False|date }}", "{{ p|date }}", False),
    ("{{ None|date }}", "{{ p|date }}", None),
    ("{{ True|time }}", "{{ p|time }}", True),
    ("{{ False|time }}", "{{ p|time }}", False),
    ("{{ None|time }}", "{{ p|time }}", None),
    ('{{ None|add:"1" }}', '{{ p|add:"1" }}', None),
    ("{{ True|pluralize }}", "{{ p|pluralize }}", True),
    ("{{ None|pluralize }}", "{{ p|pluralize }}", None),
]


class TestTheReportedCells:
    @pytest.mark.parametrize(("literal", "bound", "value"), _REPORTED)
    def test_both_spellings_now_render_djangos_empty(self, literal: str, bound: str, value) -> None:
        d, r = both(literal, {})
        assert d == "", f"Django moved: {literal} is now {d!r}"
        assert r == "", f"{literal} renders {r!r}, where Django renders nothing"
        # The bound control, which is what proved the bug was about the VALUE
        # and not about the literal. It must agree too, and for the same reason.
        assert_agrees(bound, {"p": value})

    def test_false_pluralize_still_answers_the_suffix(self) -> None:
        """Per-VALUE, not per-type: ``float(False)`` is ``0.0``, so it pluralizes.

        Without this row a fix keyed on "is it a bool" would look correct.
        """
        d, r = both("{{ False|pluralize }}", {})
        assert d == "s", f"Django moved: {d!r}"
        assert r == "s"


# ===========================================================================
# Mechanism 1 & 2 — the failure answer, across the whole blast radius.
# ===========================================================================

#: Every value shape that reaches the give-up branch, not the three the issue
#: named. Each renders NOTHING in Django.
_GIVE_UP_VALUES = {
    "true": True,
    "false": False,
    "none": None,
    "str": "abc",
    "int": 42,
    "zero": 0,
    "float": 1.5,
    "list": [1],
    "emptylist": [],
    "dict": {"a": 1},
    "emptydict": {},
    "tuple": (1,),
    "decimal": Decimal("2.5"),
    "xss": XSS,
}


class TestDateAndTimeRenderNothingForANonDate:
    """``except AttributeError: return ""`` — there is no value Django renders."""

    @pytest.mark.parametrize(
        "src", ["{{ p|date }}", '{{ p|date:"Y-m-d" }}', "{{ p|time }}", '{{ p|time:"H:i" }}']
    )
    @pytest.mark.parametrize("name", sorted(_GIVE_UP_VALUES))
    def test_renders_nothing(self, src: str, name: str) -> None:
        value = _GIVE_UP_VALUES[name]
        d, r = both(src, {"p": value})
        assert d == "", f"Django moved: {src} on {value!r} is now {d!r}"
        assert r == "", f"{src} on {value!r} renders {r!r}"

    @pytest.mark.parametrize(
        ("fmt", "expected"),
        [
            # No specifier anywhere: the formatter never touches the value, so
            # nothing raises and the literal text comes back.
            ("1", "1"),
            (",", ","),
            ("q", "q"),
            ("1-1", "1-1"),
            (" ", " "),
            # The FIRST specifier raises, discarding what came before it —
            # all-or-nothing, not a partial render.
            ("Y", ""),
            ("j", ""),
            ("abc", ""),
            ("1-Y", ""),
            ("Y-1", ""),
            # An ESCAPED specifier is literal, and the backslash is dropped.
            (r"\Y", "Y"),
            (r"1\Y2", "1Y2"),
            # ...and the test is POSITIONAL, not semantic: the lookbehind is
            # `(?<!\\)`, so a specifier preceded by an ESCAPED backslash is
            # still not a specifier. This is the one row a hand-rolled
            # unescape-then-scan gets wrong.
            ("\\\\Y", "\\Y"),
        ],
    )
    @pytest.mark.parametrize("name", ["true", "int", "str", "list", "dict"])
    def test_a_format_with_no_specifier_renders_its_literal_text(
        self, fmt: str, expected: str, name: str
    ) -> None:
        """Django's answer for a non-date is NOT unconditionally ``""``.

        Found by this file's randomised sweep, not by reading the source: the
        fix's first pass returned a flat ``""`` here and 296 of 4,000 cells
        disagreed.
        """
        value = _GIVE_UP_VALUES[name]
        for filt in ("date", "time"):
            src = "{{ p|%s:q }}" % filt
            d, r = both(src, {"p": value, "q": fmt})
            assert d == expected, f"Django moved: {filt}:{fmt!r} on {value!r} is {d!r}"
            assert r == expected, f"{filt}:{fmt!r} on {value!r}: django={d!r} djust={r!r}"

    @pytest.mark.parametrize("value", [None, ""])
    def test_none_and_empty_never_reach_the_formatter_at_all(self, value) -> None:
        """``if value in (None, "")`` is the filter's FIRST line.

        So they answer ``""`` even for a format the row above renders in full
        — which is what separates "no specifier" from "no value".
        """
        for filt in ("date", "time"):
            d, r = both("{{ p|%s:q }}" % filt, {"p": value, "q": "1-1"})
            assert d == "", f"Django moved: {filt} on {value!r} is {d!r}"
            assert r == ""

    def test_a_value_that_PARSES_still_formats(self) -> None:
        """The extension is untouched, and it is not optional.

        `Value` has no date variant, so a Python `date` reaches this renderer
        as its ISO string. Without the parse, every real date would render
        nothing — so this is the case that separates "give Django's failure
        answer" from "delete the feature".
        """
        assert_agrees('{{ p|date:"Y-m-d" }}', {"p": datetime.date(2020, 1, 2)})
        assert_agrees("{{ p|date }}", {"p": datetime.datetime(2020, 1, 2, 15, 30)})
        assert_agrees('{{ p|time:"H:i" }}', {"p": datetime.datetime(2020, 1, 2, 15, 30)})
        assert_agrees("{{ p|time }}", {"p": datetime.time(15, 30)})


class TestAddsThirdBranchRendersNothing:
    """``except Exception: return ""`` — after the sum and the concatenation."""

    @pytest.mark.parametrize("name", ["none", "list", "emptylist", "dict", "emptydict", "tuple"])
    def test_renders_nothing(self, name: str) -> None:
        value = _GIVE_UP_VALUES[name]
        d, r = both('{{ p|add:"1" }}', {"p": value})
        assert d == "", f"Django moved: add on {value!r} is now {d!r}"
        assert r == "", f"add on {value!r} renders {r!r}"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (42, "43"),
            (True, "2"),
            (False, "1"),
            (1.5, "2"),
            (Decimal("2.5"), "3"),
            ("abc", "abc1"),
            ("", "1"),
            ("4", "5"),
        ],
    )
    def test_the_first_two_branches_are_untouched(self, value, expected: str) -> None:
        """The sum and the concatenation must still win before the give-up.

        Without these a fix that returned `""` unconditionally would pass every
        test above.
        """
        d, r = both('{{ p|add:"1" }}', {"p": value})
        assert d == expected, f"Django moved: add on {value!r} is now {d!r}"
        assert r == expected


# ===========================================================================
# Mechanism 3 — pluralize, whole.
# ===========================================================================


class TestPluralizeIsDjangosFourAnswers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # `float(value) == 1` — the singular.
            (1, ""),
            (1.0, ""),
            (True, ""),
            (Decimal("1"), ""),
            ("1", ""),
            ("1.0", ""),
            # ...and its plural.
            (0, "s"),
            (2, "s"),
            (False, "s"),
            (Decimal("2.5"), "s"),
            ("2", "s"),
            # A ValueError — a string that is NOT a number — falls straight to
            # `""`. It does NOT try `len()`, which is what separates this row
            # from the list rows below.
            ("abc", ""),
            ("", ""),
            (XSS, ""),
            # A TypeError — not a string or a number — DOES try `len()`.
            ([1], ""),
            ([1, 2], "s"),
            ([], "s"),
            ((1,), ""),
            ((1, 2), "s"),
            ({"a": 1}, ""),
            ({"a": 1, "b": 2}, "s"),
            ({}, "s"),
            # Neither a `float()` nor a `len()`.
            (None, ""),
            (datetime.date(2020, 1, 2), ""),
        ],
    )
    def test_value(self, value, expected: str) -> None:
        d, r = both("{{ p|pluralize }}", {"p": value})
        assert d == expected, f"Django moved: pluralize on {value!r} is now {d!r}"
        assert r == expected, f"pluralize on {value!r}: django={d!r} djust={r!r}"

    def test_a_three_length_string_is_NOT_the_plural(self) -> None:
        """The sharpest form of "the two `except` arms are not one arm".

        If the `ValueError` branch fell through to `len()`, `"abc"` would
        answer the plural suffix. It answers `""`.
        """
        assert both("{{ p|pluralize }}", {"p": "abc"}) == ("", "")
        assert both("{{ p|pluralize }}", {"p": [1, 2, 3]}) == ("s", "s")

    @pytest.mark.parametrize(
        ("arg", "value", "expected"),
        [
            # The comma form, which was entirely unimplemented: this rendered
            # the literal text `y,ies`.
            ('"y,ies"', 1, "y"),
            ('"y,ies"', 2, "ies"),
            ('"y,ies"', 0, "ies"),
            # More than two bits is `""`, whatever the value.
            ('"a,b,c"', 1, ""),
            ('"a,b,c"', 2, ""),
            # A bare comma is two EMPTY suffixes.
            ('","', 2, ""),
            ('","', 1, ""),
            # The ordinary single-suffix form still works.
            ('"es"', 2, "es"),
            ('"es"', 1, ""),
            ('""', 2, ""),
        ],
    )
    def test_the_argument_is_a_suffix_PAIR(self, arg: str, value, expected: str) -> None:
        src = "{{ p|pluralize:%s }}" % arg
        d, r = both(src, {"p": value})
        assert d == expected, f"Django moved: {src} on {value!r} is now {d!r}"
        assert r == expected, f"{src} on {value!r}: django={d!r} djust={r!r}"


# ===========================================================================
# Named, not fixed.
# ===========================================================================


class TestIteratingANonIterableIsNamedNotFixed:
    """``{% for x in True %}``: Django 500s, djust renders ``{% empty %}``.

    Mechanism 4 of #2359, deliberately out of scope. Making djust raise is a
    product decision with a blast radius far past the bools — an ``int``, a
    ``float`` and a ``Decimal`` all raise in Django too — and it is the one
    row of this issue where djust rendering LESS is not the answer, because
    what Django renders is an error page. Filed separately; pinned here so it
    is a named limit rather than a silent one.
    """

    @pytest.mark.parametrize("value", [True, False, 42, 0, 1.5, Decimal("2.5")])
    def test_django_raises_and_djust_renders_the_empty_branch(self, value) -> None:
        src = "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"
        d, r = both(src, {"p": value})
        assert d == "<<EXC TypeError>>", f"Django moved for {value!r}: {d!r}"
        assert r == "E", f"{value!r}: djust now renders {r!r}"

    def test_none_is_NOT_in_this_class(self) -> None:
        """Django resolves a `None` operand to the empty branch rather than
        raising, so this cell AGREES — which is why the class is about
        non-iterables and not about falsiness."""
        assert both("{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}", {"p": None}) == (
            "E",
            "E",
        )


class TestTheArgumentTypeResidueIsNamed:
    """``{{ p|date:True }}``: Django raises, djust renders ``""``.

    Not a #2359 cell and not a #2347 one. The argument reaches the dispatch
    table as the STRING ``"True"`` — the wire between the template layer and
    the filter table is ``Option<&str>`` — so djust formats with a
    four-character format string whose characters are all ``date`` specifiers
    and gets ``""``. Django holds the real ``True`` and ``get_format(True)``
    raises ``TypeError``.

    It moved here from
    ``test_template_builtins_2347.py::TestOnlyAddWasBrokenByTheBareLiteral``
    when #2359 closed that class's numeric control (``date:"1"`` now agrees),
    which is precisely what that class's failure message asks for: "Re-measure
    and either fix it or move this row." It belongs to #2366's mechanism —
    the argument's Python TYPE, discarded at a string-valued boundary.
    """

    def test_the_bare_true_argument(self) -> None:
        d, r = both("{{ p|date:True }}", {"p": 5})
        assert d == "<<EXC TypeError>>", f"Django moved: {d!r}"
        assert r == ""

    def test_the_numeric_control_now_AGREES_which_is_why_the_row_moved(self) -> None:
        """``date:"1"`` and ``time:"1"`` are the controls that used to diverge.

        They diverged because a specifier-free format renders its literal text
        in Django and djust echoed the value instead — the bug #2359 closed.
        With them agreeing, the bare-literal class has nothing left to assert
        for these two filters.
        """
        assert both('{{ p|date:"1" }}', {"p": 5}) == ("1", "1")
        assert both('{{ p|time:"1" }}', {"p": 5}) == ("1", "1")
        assert both("{{ p|time:True }}", {"p": 5}) == ("", "")


class TestTheDateWireResidueIsNamed:
    """A Python `date` reaches this renderer as its ISO STRING.

    `Value` has no date variant, so `{{ "2020-01-01"|date }}` and
    `{{ real_date|date }}` are the same call here and different calls in
    Django. Every row below is that one fact, and none of them is closable
    without a typed `Value`.
    """

    @pytest.mark.parametrize(
        ("src", "value", "djust_says"),
        [
            ("{{ p|date }}", "2020-01-01", "Jan. 1, 2020"),
            ('{{ p|date:"Y-m-d" }}', "2020-01-01", "2020-01-01"),
            ('{{ p|time:"H:i" }}', "2020-01-01T15:30:00", "15:30"),
            # The same fact from the other side: a real `date` has no time in
            # Django, and here it is a string a time parser reads as midnight.
            ('{{ p|time:"H:i" }}', datetime.date(2020, 1, 2), "00:00"),
        ],
    )
    def test_django_renders_nothing_and_djust_formats_the_string(
        self, src: str, value, djust_says: str
    ) -> None:
        d, r = both(src, {"p": value})
        assert d == "", f"Django moved: {src} on {value!r} is now {d!r}"
        assert r == djust_says, f"{src} on {value!r}: djust now renders {r!r}"


# ===========================================================================
# A randomised differential over the four filters.
# ===========================================================================

_SWEEP_VALUES = [
    True,
    False,
    None,
    0,
    1,
    2,
    -1,
    1.0,
    1.5,
    Decimal("1"),
    Decimal("2.5"),
    "",
    "1",
    "abc",
    "2020-01-01",
    XSS,
    [],
    [1],
    [1, 2],
    (),
    (1,),
    (1, 2),
    {},
    {"a": 1},
    {"a": 1, "b": 2},
    datetime.date(2020, 1, 2),
    datetime.datetime(2020, 1, 2, 15, 30),
    datetime.time(15, 30),
]
_ARGS = [
    "",
    ':"s"',
    ':"es"',
    ':"y,ies"',
    ':"a,b,c"',
    ':","',
    ':""',
    ':"1"',
    ':"abc"',
    ':"Y-m-d"',
    ':"H:i"',
]
_FILTERS = ["date", "time", "add", "pluralize"]

#: The one excused class, computed from the VALUE's Python type rather than
#: from the outputs: a date-shaped string, or a real date/time reaching the
#: filters through the string wire.
_DATE_SHAPED = {"2020-01-01"}


def _is_wire_residue(value, name: str) -> bool:
    if isinstance(value, (datetime.date, datetime.time)):
        return True
    return isinstance(value, str) and value in _DATE_SHAPED and name in {"date", "time", "add"}


class TestARandomisedDifferentialOverTheFourFilters:
    """The tables above sample the axis; this samples the space."""

    def test_four_thousand_random_cells(self) -> None:
        rng = random.Random(2359)
        checked = 0
        nonempty = 0
        residue = 0
        mismatches: list[str] = []
        for _ in range(4000):
            name = rng.choice(_FILTERS)
            arg = rng.choice(_ARGS)
            if name == "add" and arg == "":
                arg = ':"1"'
            value = rng.choice(_SWEEP_VALUES)
            src = "{{ p|%s%s }}" % (name, arg)
            d, r = both(src, {"p": value})
            checked += 1
            if d not in ("", "<<EXC TypeError>>"):
                nonempty += 1
            if d == r:
                continue
            if _is_wire_residue(value, name):
                residue += 1
                continue
            mismatches.append(f"{src} on {value!r}: django={d!r} djust={r!r}")
        assert checked == 4000
        assert nonempty >= 800, (
            f"only {nonempty} of {checked} cells produced a non-empty Django "
            "answer — the sweep is not reaching the surface it claims to measure"
        )
        assert residue >= 20, (
            f"the date-wire-residue exclusion fired {residue} times — if it is 0 "
            "the sweep never builds the shape it excuses, and the exclusion is "
            "unfalsifiable rather than bounded"
        )
        assert not mismatches, (
            f"{len(mismatches)} of {checked} cells diverge; first 10:\n"
            + "\n".join(mismatches[:10])
        )

    def test_no_cell_emits_markup_django_escaped(self) -> None:
        """The direction constraint: these three filters used to ECHO."""
        rng = random.Random(23592)
        leaks: list[str] = []
        for _ in range(2000):
            name = rng.choice(_FILTERS)
            arg = rng.choice(_ARGS)
            if name == "add" and arg == "":
                arg = ':"1"'
            for value in (XSS, [XSS], {"k": XSS}, (XSS,)):
                src = "{{ p|%s%s }}" % (name, arg)
                d, r = both(src, {"p": value})
                if "<img" in r and "<img" not in d:
                    leaks.append(f"{src} on {value!r}: django={d!r} djust={r!r}")
        assert not leaks, "djust emitted live markup Django escaped:\n" + "\n".join(leaks[:10])
