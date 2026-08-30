"""Regression: ``|safe`` STRINGIFIES, for a scalar too (#2303).

Django's ``mark_safe(obj)`` is ``SafeString(str(obj))`` — it does not merely
mark the value, it changes its TYPE before the rest of the chain sees it. So
``{{ n|safe }}`` with ``n = 42`` hands the next filter the string ``'42'``, and
``{{ n|safe|probe }}`` reports ``('SafeString', True)`` where djust reported
``('int', False)``.

The CONTAINER half of this landed in #2283 (``{{ l|safe|slice:":3" }}`` is
``['<`` in Django — three characters of the list's repr, not a three-element
list). The scalar half is this issue: the same edit one variant over (#1646).

Two spellings it has to get right, and NEITHER is djust's ``Display``:

* ``str()``, not the RENDER form. ``Display`` is what ``{{ p }}`` emits, which
  Django reaches through ``numberformat.format()`` — that expands an exponent,
  so ``1e20`` renders ``100000000000000000000`` while ``str(1e20)`` is
  ``1e+20``. Same split for ``Decimal("1E-9")``: ``0.000000001`` rendered,
  ``1E-9`` stringified. ``Value::py_str`` is the one definition.
* ``Value::Missing`` is ``""``, not ``"None"``. Django substitutes
  ``string_if_invalid`` for an ABSENT variable BEFORE the filter chain runs, so
  what ``mark_safe`` sees there is the empty string. A blanket stringify would
  put the literal text ``None`` on the page — strictly worse than the
  pass-through it replaces, which is why this row gets its own class below.

The two-build differential (``scripts/filter-parity-differential.py``) reports
208 newly agreeing / 0 regressions / 0 introduced live-payload leaks; a wider
scalar sweep carrying ``Decimal``/``BigInt``/exponent floats and the MISSING
variable reports 580 newly agreeing / 0 regressions. That sweep is what found
the one filter this type change bites — ``divisibleby``, which read the
``Value::Integer`` VARIANT where Django reads ``int(value)``;
:class:`TestDivisiblebyReadsTheValueNotTheType` covers it.

Every assertion is against **live Django**, not a transcription.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

pytest.importorskip("django")

from django import template  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402
from django.template.base import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register as _django_registry  # noqa: E402
from django.utils.safestring import SafeData  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

#: Deliberately un-guessable: the Rust filter registry is process-global and is
#: NOT cleared between tests, so a colliding name is a silent cross-test leak.
PROBE = "_dj2303_probe"
ECHO = "_dj2303_echo"

_library = template.Library()


@_library.filter(name=PROBE)
def _probe(value):
    """The issue's probe: what TYPE, and is it ``SafeData``."""
    return repr((type(value).__name__, isinstance(value, SafeData)))


@_library.filter(name=ECHO)
def _echo(value):
    """``repr`` of the value itself — the SPELLING half, which ``_probe``
    cannot see because it reports only the type."""
    return repr(value)


@pytest.fixture(scope="module", autouse=True)
def _registered():
    for name, fn in _library.filters.items():
        _rust.register_custom_filter(
            name,
            fn,
            bool(getattr(fn, "is_safe", False)),
            bool(getattr(fn, "needs_autoescape", False)),
        )
    yield
    for name in _library.filters:
        _rust.unregister_custom_filter(name)


_engine = Engine(libraries={}, builtins=[])
_engine.template_builtins.append(_library)


def django_render(src: str, ctx: dict) -> str:
    return DjangoTemplate(src, engine=_engine).render(DjangoContext(ctx))


def djust_render(src: str, ctx: dict) -> str:
    return _rust.render_template(src, normalize_django_value(ctx))


def assert_agrees(src: str, ctx: dict) -> None:
    d, r = django_render(src, ctx), djust_render(src, ctx)
    assert r == d, f"{src} on {ctx!r}: django={d!r} djust={r!r}"


#: The scalar variants ``|safe`` was a no-op for, one per ``Value`` arm.
SCALARS = {
    "int": 42,
    "int-zero": 0,
    "float": 1.5,
    "float-negative": -2.25,
    "bool-true": True,
    "bool-false": False,
    "none": None,
    "decimal": Decimal("1E-9"),
    "bigint": 12345678901234567890,
}


class TestTheReportedTable:
    """The issue's four-row table, run against live Django."""

    @pytest.mark.parametrize("value", [42, 1.5, True], ids=["int", "float", "bool"])
    def test_a_scalar_reaches_the_next_filter_as_a_SafeString(self, value) -> None:
        src = "{{ p|safe|%s }}" % PROBE
        assert_agrees(src, {"p": value})
        assert "SafeString" in djust_render(src, {"p": value})

    def test_an_absent_variable_reaches_it_as_an_empty_SafeString(self) -> None:
        """The row with a DIFFERENT mechanism: ``string_if_invalid`` runs before
        the chain, so Django's ``mark_safe`` there sees ``""`` and not ``None``."""
        src = "{{ absent|safe|%s }}" % PROBE
        assert_agrees(src, {})
        assert djust_render(src, {}) == "(&#x27;SafeString&#x27;, True)"

    def test_the_control_row_is_still_a_plain_unmarked_value(self) -> None:
        """Without ``|safe`` nothing is marked — otherwise the rows above prove
        only that djust marks everything safe (#1200)."""
        src = "{{ p|%s }}" % PROBE
        assert_agrees(src, {"p": 42})
        assert djust_render(src, {"p": 42}) == "(&#x27;int&#x27;, False)"


class TestEveryScalarVariant:
    """One row per ``Value`` arm, because the bug lived in whichever arm the
    table did not enumerate (v1.0.0rc4 finding #1)."""

    @pytest.mark.parametrize("key", sorted(SCALARS))
    def test_the_probe_sees_what_django_hands_it(self, key: str) -> None:
        assert_agrees("{{ p|safe|%s }}" % PROBE, {"p": SCALARS[key]})

    @pytest.mark.parametrize("key", sorted(SCALARS))
    def test_the_rendered_bytes_are_unchanged_by_the_stringify(self, key: str) -> None:
        """``{{ p|safe }}`` alone must still render what Django renders — the
        type change is for the rest of the chain, not for the page."""
        assert_agrees("{{ p|safe }}", {"p": SCALARS[key]})


class TestItIsStrAndNotTheRenderForm:
    """``py_str`` is neither ``Display`` nor ``py_repr``, and the two variants
    that carry an exponent are where that matters."""

    @pytest.mark.parametrize(
        "value,rendered,stringified",
        [
            (1e20, "100000000000000000000", "1e+20"),
            (Decimal("1E-9"), "0.000000001", "1E-9"),
        ],
        ids=["float", "decimal"],
    )
    def test_the_bare_render_expands_the_exponent_and_safe_does_not(
        self, value, rendered: str, stringified: str
    ) -> None:
        """Pins BOTH sides against Django, so a change to either reddens here.

        ``{{ p }}`` goes through ``numberformat.format()`` and expands;
        ``mark_safe`` is ``str()`` and does not. Using ``Display`` for the
        stringify would have silently made ``{{ p|safe|probe }}`` report the
        expanded form, which no Django ever produces.
        """
        assert django_render("{{ p }}", {"p": value}) == rendered
        assert_agrees("{{ p }}", {"p": value})
        expected = repr(stringified).replace("'", "&#x27;")
        assert django_render("{{ p|safe|%s }}" % ECHO, {"p": value}) == expected
        assert_agrees("{{ p|safe|%s }}" % ECHO, {"p": value})
        assert rendered != stringified, "the two spellings must differ or this row proves nothing"

    def test_a_nested_decimal_still_uses_the_repr_form(self) -> None:
        """``py_repr`` is a third spelling again — ``Decimal('1E-9')`` inside a
        container. Untouched by this fix, and asserted so a future edit that
        collapses the three spellings into one has to come here."""
        assert_agrees("{{ p }}", {"p": [Decimal("1E-9")]})
        assert "Decimal(&#x27;1E-9&#x27;)" in djust_render("{{ p }}", {"p": [Decimal("1E-9")]})


class TestTheAbsentVariableIsEmptyAndNotNone:
    """The row a blanket stringify gets MORE wrong than the pass-through did.

    Django's ``string_if_invalid`` (default ``""``) is substituted at variable
    resolution, before any filter runs, so every cell here is Django comparing
    against ``""`` — never against ``None``.
    """

    @pytest.mark.parametrize(
        "tail", ["", "|add:'1'", "|length", "|upper", "|default:'D'", "|floatformat", "|make_list"]
    )
    def test_absent_then_safe_agrees_with_django(self, tail: str) -> None:
        assert_agrees("{{ absent|safe" + tail + " }}", {})

    def test_the_literal_text_None_never_reaches_the_page(self) -> None:
        """The failure mode named in the issue, asserted directly."""
        for tail in ["", "|upper", "|add:'1'", "|make_list", "|linenumbers", "|title"]:
            out = djust_render("{{ absent|safe" + tail + " }}", {})
            assert "None" not in out, f"{tail}: {out!r}"

    def test_a_real_python_None_still_says_None(self) -> None:
        """The other half of the ``Missing``/``None`` split (#2203): Python
        ``None`` really does stringify to ``"None"`` in Django, so collapsing
        the two variants would break this row instead."""
        assert_agrees("{{ p|safe }}", {"p": None})
        assert djust_render("{{ p|safe }}", {"p": None}) == "None"
        assert_agrees("{{ p|safe|%s }}" % ECHO, {"p": None})
        assert djust_render("{{ p|safe|%s }}" % ECHO, {"p": None}) == "&#x27;None&#x27;"
        assert djust_render("{{ absent|safe|%s }}" % ECHO, {}) == "&#x27;&#x27;"


class TestDivisiblebyReadsTheValueNotTheType:
    """The one filter the type change bit, found by the differential.

    Django is ``int(value) % int(arg) == 0``, so a numeric STRING has always
    worked there; djust matched ``Value::Integer`` alone. Without this,
    ``{{ n|safe|divisibleby:"2" }}`` — ``True`` before the stringify and in
    Django — would have started answering ``False``.

    Django RAISES on anything ``int()`` rejects, and since #2435 so does djust
    — the fail-soft rows below became refusal rows.
    """

    @pytest.mark.parametrize("value", [42, 0, -7, "42", "-42", "+42", "  42  ", 41, "41"])
    def test_agrees_with_django_wherever_django_answers(self, value) -> None:
        assert_agrees("{{ p|divisibleby:'2' }}", {"p": value})
        assert_agrees("{{ p|safe|divisibleby:'2' }}", {"p": value})

    def test_the_safe_chain_is_the_cell_that_would_have_regressed(self) -> None:
        assert djust_render("{{ p|safe|divisibleby:'2' }}", {"p": 42}) == "True"
        assert djust_render("{{ p|safe|divisibleby:'2' }}", {"p": 41}) == "False"

    @pytest.mark.parametrize("value", ["abc", "", "4.0", "0x2a", [1, 2], {"k": 1}])
    def test_an_input_int_rejects_now_raises_as_django_does(self, value) -> None:
        """Django raises ``ValueError``/``TypeError`` here and 500s the page.

        djust answered ``False`` until #2435 routed the VALUE through the
        ``int(value)`` chokepoint. The widened parse above is what makes this
        non-vacuous: a rule that refused everything would fail
        ``test_agrees_with_django_wherever_django_answers``, which still
        includes ``"  42  "`` and ``"+42"``.
        """
        with pytest.raises(Exception):
            django_render("{{ p|divisibleby:'2' }}", {"p": value})
        with pytest.raises(RuntimeError, match="calls int\\(\\) on its value"):
            djust_render("{{ p|divisibleby:'2' }}", {"p": value})


class TestTheBuiltInChainIsNotWorseOff:
    """The issue's step 3: the built-in chain is where a type change bites.

    A randomised sweep rather than a table, because the failure mode is a
    filter that reads the type and was not on anybody's list — which is exactly
    how ``divisibleby`` was found. Every cell where Django ANSWERS must agree;
    cells where Django raises are counted and skipped, since there is no output
    to be compared against.
    """

    #: Filters that are nondeterministic between two calls, so no comparison is
    #: possible.
    NONDET = {"random", "timesince", "timeuntil"}

    ARGS = {
        "add": "'1'",
        "center": "'20'",
        "cut": "'2'",
        "date": "'Y-m-d'",
        "default": "'D'",
        "default_if_none": "'D'",
        "dictsort": "'k'",
        "dictsortreversed": "'k'",
        "divisibleby": "'2'",
        "floatformat": "'2'",
        "get_digit": "'1'",
        "join": "'<br>'",
        "json_script": "'i'",
        "length_is": "'2'",
        "ljust": "'20'",
        "pluralize": "'s'",
        "rjust": "'20'",
        "slice": "':3'",
        "stringformat": "'s'",
        "time": "'H:i'",
        "truncatechars": "'5'",
        "truncatechars_html": "'5'",
        "truncatewords": "'2'",
        "truncatewords_html": "'2'",
        "urlizetrunc": "'15'",
        "wordwrap": "'5'",
        "yesno": "'y,n,m'",
    }

    @classmethod
    def _tails(cls) -> list[str]:
        """EVERY filter in Django's live registry, read from the registry so a
        Django release that adds one is picked up instead of being missed."""
        return [
            "|%s:%s" % (n, cls.ARGS[n]) if n in cls.ARGS else "|%s" % n
            for n in sorted(_django_registry.filters)
            if n not in cls.NONDET
        ]

    #: The filters that STILL disagree on a scalar behind ``|safe``. **Empty
    #: since #2435**, and the two entries it held are why the pin is kept
    #: rather than replaced by a blanket "everything agrees":
    #:
    #: * ``add`` — Django's fallback is ``value + arg``, i.e. STRING
    #:   concatenation, so ``{{ 1.5|safe|add:"1" }}`` is ``1.51``; djust's was
    #:   numeric and gave ``2``, because its VALUE-side ``int()`` allowed a
    #:   float coercion Python's does not. Closed by the ``int(value)``
    #:   chokepoint, which makes ``int("1.5")`` the ValueError it is.
    #: * ``divisibleby`` — only for a value past ``i64``; the parse stopped
    #:   there and answered ``False``. Closed by the same chokepoint, whose
    #:   digits are arbitrary-precision.
    #:
    #: ``date``, ``time`` and ``pluralize`` were here until #2359 gave all
    #: three Django's failure answer; this pin is what reported them closed
    #: ("new residue [], closed ['date', 'pluralize', 'time']"), which is the
    #: half of a characterization pin that usually goes unexercised — and it
    #: reported these two the same way.
    KNOWN_RESIDUE: frozenset[str] = frozenset()

    def test_the_tail_set_covers_the_whole_live_registry(self) -> None:
        """Guards the sweep below against silently shrinking (#1859)."""
        tails = self._tails()
        assert len(tails) >= 50, tails
        assert "|divisibleby:'2'" in tails
        assert "|floatformat:'2'" in tails

    def test_the_residue_is_exactly_these_two_filters_and_no_others(self) -> None:
        """A characterization pin, not an aspiration.

        Every OTHER filter in the registry must agree on every scalar behind
        ``|safe``. Pinning the disagreeing set by NAME rather than asserting
        blanket agreement keeps the sweep honest about a residue that predates
        this fix, while still reddening the moment a sixth filter joins it —
        which is precisely how ``divisibleby`` was caught.
        """
        answered = 0
        residue = {}
        for key, value in SCALARS.items():
            for tail in self._tails():
                src = "{{ p|safe" + tail + " }}"
                try:
                    expected = django_render(src, {"p": value})
                except Exception:
                    continue  # Django 500s; there is nothing to compare against
                answered += 1
                got = djust_render(src, {"p": value})
                if got != expected:
                    residue.setdefault(tail.lstrip("|").split(":")[0], []).append(
                        (key, expected, got)
                    )
        assert answered > 300, f"only {answered} comparable cells — the sweep went vacuous"
        assert set(residue) == self.KNOWN_RESIDUE, (
            f"new residue {sorted(set(residue) - self.KNOWN_RESIDUE)}, "
            f"closed {sorted(self.KNOWN_RESIDUE - set(residue))}; "
            f"details { ({k: v[:2] for k, v in residue.items()}) }"
        )

    def test_divisibleby_past_i64_was_the_last_divisibleby_residue(self) -> None:
        """The cell the set-level pin above used to tolerate, now closed.

        ``12345678901234567890`` does not fit an ``i64``; djust answered
        ``False`` for EVERY divisor because its ``parse::<i64>()`` simply
        failed. #2435's chokepoint carries the digits, and the modulus is
        streamed over them, so the answer is exact at any length — asserted
        against BOTH parities so "always False" and "always True" are each
        ruled out.
        """
        big = 12345678901234567890
        assert_agrees("{{ p|safe|divisibleby:'2' }}", {"p": big})
        assert djust_render("{{ p|safe|divisibleby:'2' }}", {"p": big}) == "True"
        assert_agrees("{{ p|safe|divisibleby:'7' }}", {"p": big})
        assert djust_render("{{ p|safe|divisibleby:'7' }}", {"p": big}) == "False"
        for value in SCALARS.values():
            if type(value) is int and abs(value) < 2**63:
                assert_agrees("{{ p|safe|divisibleby:'2' }}", {"p": value})


class TestTheStringifyIsComplete:
    """The invariant the whole fix amounts to, swept randomly.

    ``mark_safe(p)`` is ``SafeString(str(p))``, so behind ``|safe`` **no filter
    can tell the value from its own string**::

        {{ p|safe|F }}  ==  {{ str(p)|safe|F }}

    Django satisfies this by construction — which is asserted alongside, so the
    property is Django's semantics and not djust agreeing with itself — and it
    is immune to the unrelated per-filter residue pinned above, because both
    sides of the identity go through the same filter.

    A randomised sweep rather than a table: the failure mode is a variant whose
    ``str()`` spelling nobody wrote down (an exponent float, a ``Decimal``, a
    value past ``i64``), and Django is one call away.
    """

    @staticmethod
    def _values(rng: random.Random, n: int) -> list:
        out = []
        for _ in range(n):
            out.append(
                rng.choice(
                    [
                        rng.randint(-(10**6), 10**6),
                        rng.randint(2**63, 2**80),  # BigInt
                        rng.random() * 10 ** rng.randint(-12, 12),  # exponent floats
                        rng.choice([True, False, None, 0, 0.0]),
                        Decimal(str(rng.randint(0, 10**8))).scaleb(rng.randint(-12, 12)),
                    ]
                )
            )
        return out

    def test_no_filter_can_tell_a_scalar_from_its_str_behind_safe(self) -> None:
        rng = random.Random(20303)
        tails = TestTheBuiltInChainIsNotWorseOff._tails()
        compared = 0
        bad = []
        for value in self._values(rng, 600):
            tail = rng.choice(tails)
            direct = "{{ p|safe" + tail + " }}"
            through_str = "{{ q|safe" + tail + " }}"
            ctx = {"p": value, "q": str(value)}
            # Django first: if the identity does not hold THERE, the cell says
            # nothing about djust (a filter that inspects `type` directly).
            try:
                if django_render(direct, ctx) != django_render(through_str, ctx):
                    continue
            except Exception:
                continue
            compared += 1
            a, b = djust_render(direct, ctx), djust_render(through_str, ctx)
            if a != b:
                bad.append((value, tail, a, b))
        assert compared > 300, f"only {compared} comparable cells — the sweep went vacuous"
        assert not bad, f"{len(bad)}/{compared} differ, first three: {bad[:3]}"

    def test_the_identity_probe_can_actually_fail(self) -> None:
        """Gate-off for the sweep above: WITHOUT ``|safe`` the two spellings do
        diverge, so the assertion is discriminating and not trivially true."""
        ctx = {"p": 42, "q": "42"}
        assert djust_render("{{ p|add:'1' }}", ctx) == djust_render("{{ q|add:'1' }}", ctx)
        differ = [
            tail
            for tail in TestTheBuiltInChainIsNotWorseOff._tails()
            if djust_render("{{ p" + tail + " }}", ctx) != djust_render("{{ q" + tail + " }}", ctx)
        ]
        assert differ, "no filter distinguishes 42 from '42' — the sweep proves nothing"
