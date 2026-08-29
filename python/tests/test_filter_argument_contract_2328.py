"""A filter argument that is unparseable or unresolvable RAISES (#2328).

What was there
--------------
TWELVE dispatch arms read their argument as a number, through FOUR different
parsers — six inline copies of::

    let width = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(N);

each with its own ``N``, plus ``wordwrap``'s seventh spelling of the same
thing, the ``truncate_arg`` helper serving four more, and
``floatformat::parse_int_like`` in its own module,

and one more in ``apply_filter_full_safe`` fell back to the argument's RAW TEXT
when a bare identifier did not resolve. Both are silent-wrong-output: a typo'd
width wrapped at 75, and ``{{ n|pluralize:es }}`` rendered the literal word
``es``.

The measurement that scoped it
------------------------------
Django has **29** argument-taking built-ins (``inspect.getfullargspec`` minus
the injected ``autoescape``; :func:`django_argument_filters` recomputes it here
rather than hard-coding a list). Against Django 5.2, with an unparseable quoted
literal, 16 already agreed, **8** raised in Django and not in djust, and 5
differed for reasons that are not argument PARSING. With an unresolvable bare
identifier, Django raised for **all 29** and djust for none.

Why one helper and not twelve fixes
-----------------------------------
Twelve filters fixed in twelve places is the parallel-path drift this
codebase has paid for repeatedly (CLAUDE.md #1646), and the thirteenth — the
next filter anybody adds — would not have got the fix. So the argument is
parsed at one chokepoint, ``filters::filter_int_arg``, which also carries
``int()``'s spellings (``" 5 "``, ``"+5"``, ``"1_0"``) that the scattered
``parse::<usize>`` calls all refused.

The chokepoint takes a POLICY, because Django's own source has two shapes::

    def center(value, arg):  return value.center(int(arg))   # ValueError escapes
    def truncatechars(value, arg):
        try:    length = int(arg)
        except ValueError:  return value                     # caught

:class:`TestChokepointIsTheOnlyParser` pins it mechanically; a comment would
not (#1859).

What is deliberately NOT closed here
------------------------------------
* ``timesince`` / ``timeuntil`` ignored their argument ENTIRELY when this was
  written — Django uses it as the comparison instant — so making an unparseable
  one raise while a VALID one was still discarded would have been a half-fix,
  and the whole argument was left to #2344. **#2344 closed it**, the pin below
  went red exactly as intended, and the sweep now covers all **29**. Their rows
  are gone rather than relaxed, which is the point of having had them.
* ``add``, ``stringformat`` and ``yesno`` produce a different STRING for an
  unparseable argument, for reasons that are not ``int()`` — a three-branch
  fallback chain, an invalid printf spec and an arity check respectively. They
  DO agree on the raise bit, so they stay in the sweep. ``stringformat`` with an
  EMPTY argument additionally PANICS, on main and here alike: #2343.
* Three more argument-axis divergences with non-``int()`` causes were #2346
  (``urlizetrunc``'s ellipsis, ``divisibleby``'s zero divisor, ``floatformat``'s
  empty argument) — **closed**, see
  ``python/tests/test_argument_axis_divergences_2346.py``. The missing
  ``True``/``False``/``None`` context builtins are #2347.
* A MISSING argument is a ``TemplateSyntaxError`` at Django's PARSE time
  (arity, before any filter runs). Different mechanism, different issue.

The first bullet is :data:`RAISE_BIT_NOT_CLOSED` and the second
:data:`OUTPUT_DIVERGES_FOR_ANOTHER_REASON`, kept apart deliberately: an earlier
draft of this file put all five in one exemption list, which would have
excused three filters from a sweep they actually pass — the shape by which a
stale exemption hides a regression. Each dict has a test asserting its rows are
still divergent, so a row that gets fixed elsewhere fails rather than lingers.
"""

from __future__ import annotations

import datetime
import inspect
import pathlib
import re
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template import defaultfilters as django_defaultfilters  # noqa: E402

from djust import _rust  # noqa: E402

FILTERS_RS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "crates"
    / "djust_templates"
    / "src"
    / "filters.rs"
)


def django_argument_filters() -> list[str]:
    """Django's built-ins that take a TEMPLATE argument, computed not listed.

    ``needs_autoescape=True`` filters get an ``autoescape`` kwarg injected by
    the engine; that is not a template argument, so it is excluded — the same
    exclusion ``FilterExpression.args_check`` makes.
    """
    names = []
    for name, fn in sorted(django_defaultfilters.register.filters.items()):
        spec = inspect.getfullargspec(inspect.unwrap(fn))
        args = [a for a in spec.args if a != "autoescape"]
        if len(args) >= 2:
            names.append(name)
    return names


#: A value each filter is happy with, so the sweep measures the ARGUMENT axis
#: and not a type error in the value. Anything absent gets `TEXT`.
TEXT = "the quick brown fox jumps over the lazy dog and keeps running onward"
_NOW = datetime.datetime(2020, 1, 1, 12, 0, 0)
_DICTS = [{"k": 2, "n": "b"}, {"k": 1, "n": "a"}]
VALUES: dict[str, Any] = {
    "date": _NOW,
    "time": _NOW,
    "timesince": _NOW,
    "timeuntil": _NOW,
    "dictsort": _DICTS,
    "dictsortreversed": _DICTS,
    "join": ["a", "b", "c"],
    "add": 5,
    "divisibleby": 10,
    "floatformat": 3.14159,
    "get_digit": 123456,
    "pluralize": 2,
    "slice": [1, 2, 3, 4, 5],
    "stringformat": 42,
    "yesno": True,
    "default": "",
    "default_if_none": None,
}

#: EMPTY since #2344, and kept as a named place rather than deleted: an
#: exemption is how a filter leaves this sweep, and a reviewer adding one should
#: find the mechanism (and :meth:`TestTheMeasurementThatScopedTheIssue.
#: test_every_exemption_is_still_divergent`, which fails the moment a row here
#: becomes true) rather than inventing a second one.
#:
#: It held ``timesince`` and ``timeuntil`` until #2344, for a reason that was not
#: a parsing reason: the argument was not read at all, so making an UNPARSEABLE
#: one raise while a VALID one was still discarded would have been a half-fix —
#: strictly worse than the honest "the argument does nothing", because it would
#: have looked handled.
RAISE_BIT_NOT_CLOSED: dict[str, str] = {}

#: Three filters agree with Django on the raise bit and still produce a
#: different STRING, because their unparseable-argument behaviour is governed
#: by something other than `int(arg)`. Listed separately because conflating
#: them with the row above would have exempted them from the raise sweep they
#: actually pass — which is how a stale exemption hides a regression.
OUTPUT_DIVERGES_FOR_ANOTHER_REASON = {
    # `add` was here until #2359 gave its third branch Django's `""`.
    # `"notanumber"` reaches that branch for the `TEXT` value, so the row was
    # true and is now closed — removed rather than relaxed, as this file's own
    # message demands ("now agrees - remove its row").
    "stringformat": "an invalid printf spec, not int(arg) (#2343)",
    "yesno": "an arity check on a comma-separated argument, not int(arg)",
}


def raises_django(source: str, value: Any) -> bool:
    try:
        DjangoTemplate(source).render(DjangoContext({"p": value}))
    except Exception:  # noqa: BLE001
        return True
    return False


def raises_djust(source: str, value: Any) -> bool:
    try:
        _rust.render_template(source, {"p": value})
    except Exception:  # noqa: BLE001
        return True
    return False


class TestTheMeasurementThatScopedTheIssue:
    """The counts in the module docstring, recomputed rather than quoted."""

    def test_django_has_twenty_nine_argument_taking_builtins(self) -> None:
        assert len(django_argument_filters()) == 29

    def test_an_unparseable_quoted_literal_agrees_on_the_raise_bit(self) -> None:
        """All 29 since #2344, including the 8 that used to be
        Django-raises-only and the 2 that were exempt until the argument was
        read at all."""
        disagreed = []
        for name in django_argument_filters():
            if name in RAISE_BIT_NOT_CLOSED:
                continue
            value = VALUES.get(name, TEXT)
            source = '{{ p|%s:"notanumber" }}' % name
            dj, du = raises_django(source, value), raises_djust(source, value)
            if dj != du:
                disagreed.append(f"{name}: django_raises={dj} djust_raises={du}")
        assert not disagreed, "\n".join(disagreed)

    def test_the_sweep_covers_every_argument_taking_builtin(self) -> None:
        """29 of 29 since #2344. Derived, so a Django release that adds an
        argument-taking filter moves this rather than leaving it stale."""
        assert len(django_argument_filters()) == 29
        assert not RAISE_BIT_NOT_CLOSED, (
            f"{sorted(RAISE_BIT_NOT_CLOSED)} are exempt from the raise sweep. That is "
            "allowed, but the count above has to move with it and the exemption needs "
            "a reason that is not 'it disagrees'."
        )
        assert len(django_argument_filters()) - len(RAISE_BIT_NOT_CLOSED) == 29

    def test_every_exemption_is_still_divergent(self) -> None:
        """Non-vacuity: a stale exemption is a hole in the sweep above, so if
        one is fixed elsewhere the row must go rather than linger.

        This is the test #2344 was written to fail, and it did: the moment
        ``timesince``/``timeuntil`` started reading their argument, the two rows
        stopped being true and had to be deleted rather than relaxed. It is
        vacuous today because the dict is empty, and that is the correct
        resting state — it is the guard for the next row, not a pin on the two
        that are gone. The property it enforces is covered from the other side
        by the sweep above, which now includes both names.
        """
        still_divergent = [
            name
            for name in RAISE_BIT_NOT_CLOSED
            if raises_django('{{ p|%s:"notanumber" }}' % name, VALUES[name])
            != raises_djust('{{ p|%s:"notanumber" }}' % name, VALUES[name])
        ]
        assert sorted(still_divergent) == sorted(RAISE_BIT_NOT_CLOSED), (
            "a filter in RAISE_BIT_NOT_CLOSED now agrees with Django — remove its row"
        )

    def test_the_output_divergences_are_not_raise_divergences(self) -> None:
        """These three pass the raise sweep and still render differently. The
        distinction is the point: exempting them from the sweep — which an
        earlier draft of this file did — would have hidden a real regression in
        three filters that this issue genuinely fixed the raise bit for.
        """
        for name in OUTPUT_DIVERGES_FOR_ANOTHER_REASON:
            value = VALUES.get(name, TEXT)
            source = '{{ p|%s:"notanumber" }}' % name
            assert not raises_django(source, value), name
            assert not raises_djust(source, value), name
            assert _rust.render_template(source, {"p": value}) != DjangoTemplate(source).render(
                DjangoContext({"p": value})
            ), f"{name} now agrees — remove its row"

    def test_an_unresolvable_bare_identifier_raises_for_all_twenty_nine(self) -> None:
        """The half that has no exemptions: the miss is decided before dispatch,
        so it does not matter what the filter would have done with the text."""
        for name in django_argument_filters():
            value = VALUES.get(name, TEXT)
            source = "{{ p|%s:missingvar }}" % name
            assert raises_django(source, value), f"{name}: Django no longer raises"
            assert raises_djust(source, value), f"{name}: djust did not raise"


class TestALiteralArgumentIsNotALookup:
    """Django resolves some bare arguments without touching the context, and
    raising for those would break `{{ p|add:7 }}`.

    Three mechanisms, all enumerated in `is_literal_filter_arg`: the numeric
    branch of `Variable.__init__`, the `True`/`False`/`None` context builtins,
    and the `_("…")` translation marker.
    """

    @pytest.mark.parametrize(
        "arg",
        ["7", "-3", "+3", "7.5", "-7.5", ".5", "1e3", "1_0", "07", "True", "False", "None"],
    )
    def test_a_literal_argument_does_not_raise(self, arg: str) -> None:
        source = "{{ p|add:%s }}" % arg
        assert not raises_djust(source, 5), f"{arg} must not be treated as a lookup"
        assert not raises_django(source, 5), f"{arg} is not a Django lookup either"

    @pytest.mark.parametrize("arg", ["7.", "0x10", "nan", "inf", "es", "a.b"])
    def test_a_non_literal_argument_raises(self, arg: str) -> None:
        source = "{{ p|add:%s }}" % arg
        assert raises_djust(source, 5), f"{arg} is a lookup and must raise"
        assert raises_django(source, 5), f"{arg} is a Django lookup too"

    def test_a_resolvable_identifier_still_resolves(self) -> None:
        assert _rust.render_template("{{ p|add:n }}", {"p": 5, "n": 3}) == "8"

    def test_a_quoted_argument_is_never_looked_up(self) -> None:
        # Even when a context key of that name exists — `arg_was_quoted`
        # short-circuits resolution entirely (#2202).
        assert _rust.render_template('{{ p|cut:"x" }}', {"p": "axb", "x": "b"}) == "ab"


class TestTheTwoPolicies:
    """`Raise` and `ReturnInput` are the two shapes Django's source has, and
    both arms must be independently reachable or one is decorative.
    """

    RAISE = ["center", "ljust", "rjust", "wordwrap", "urlizetrunc", "divisibleby"]
    RETURN_INPUT = [
        "truncatechars",
        "truncatewords",
        "truncatechars_html",
        "truncatewords_html",
        "get_digit",
        "floatformat",
    ]

    @pytest.mark.parametrize("name", RAISE)
    def test_the_raise_arm(self, name: str) -> None:
        value = VALUES.get(name, TEXT)
        with pytest.raises(RuntimeError, match="needs an integer argument"):
            _rust.render_template('{{ p|%s:"notanumber" }}' % name, {"p": value})

    @pytest.mark.parametrize("name", RETURN_INPUT)
    def test_the_return_input_arm(self, name: str) -> None:
        value = VALUES.get(name, TEXT)
        source = '{{ p|%s:"notanumber" }}' % name
        assert _rust.render_template(source, {"p": value}) == DjangoTemplate(source).render(
            DjangoContext({"p": value})
        )

    def test_the_two_arms_disagree_on_the_same_input(self) -> None:
        """Non-vacuity for the policy parameter itself: if both arms behaved
        alike, every test above would pass with the parameter ignored."""
        with pytest.raises(RuntimeError):
            _rust.render_template('{{ p|center:"nope" }}', {"p": "ab"})
        assert _rust.render_template('{{ p|truncatechars:"nope" }}', {"p": "ab"}) == "ab"


class TestIntSpellingsTheScatteredParsesRefused:
    """`int()` is not `str::parse`, and every difference below reached a
    template. These agreed with Django at NO site before the chokepoint.
    """

    CASES = [
        ('{{ p|center:" 5 " }}', "ab"),
        ('{{ p|ljust:" 5 " }}', "ab"),
        ('{{ p|rjust:" 5 " }}', "ab"),
        ('{{ p|center:"1_0" }}', "ab"),
        ('{{ p|ljust:"1_0" }}', "ab"),
        ('{{ p|rjust:"1_0" }}', "ab"),
        ('{{ p|get_digit:" 2 " }}', 123456),
        ('{{ p|get_digit:"1_0" }}', 123456),
        ('{{ p|truncatechars:"1_0" }}', "abcdefghijklmno"),
        ('{{ p|truncatechars_html:"1_0" }}', "<p>abcdefghijklmno</p>"),
        ('{{ p|floatformat:"1_0" }}', 3.14159),
        ("{{ p|truncatechars:2.7 }}", "abcdefghij"),
        ("{{ p|truncatewords:2.7 }}", "one two three four"),
        ("{{ p|get_digit:2.7 }}", 123456),
        ("{{ p|wordwrap:2.7 }}", "one two three four"),
    ]

    @pytest.mark.parametrize("source,value", CASES)
    def test_agrees_with_django(self, source: str, value: Any) -> None:
        assert _rust.render_template(source, {"p": value}) == DjangoTemplate(source).render(
            DjangoContext({"p": value})
        )

    def test_urlizetrunc_truncates_for_a_negative_limit(self) -> None:
        """`parse::<usize>` refused a negative and produced `None`, which meant
        NO truncation — the URL rendered in full where Django's
        `trim_url` keeps nothing.

        This was NOT a parity assertion when it was written, because djust
        still spelled the ellipsis `...` where Django uses `…` — a separate
        pre-existing bug, closed by #2346. Now that both are fixed it asserts
        parity outright, which is the stronger claim the caveat was standing
        in for. Found by the gate-off, which showed this branch had no covering
        test.
        """
        url = "http://example.com/aaaaaaaaaaaaaaaa"
        full = _rust.render_template('{{ p|urlizetrunc:"999" }}', {"p": url})
        assert url in full, "a large limit should leave the URL text intact"
        for limit in ('"-3"', '"0"', "-3"):
            source = "{{ p|urlizetrunc:%s }}" % limit
            out = _rust.render_template(source, {"p": url})
            assert url not in out.split("</a>")[0].split(">")[-1], (
                f"limit {limit} must truncate the displayed text, got {out!r}"
            )
            assert out == DjangoTemplate(source).render(DjangoContext({"p": url})), (
                f"limit {limit}: the ellipsis divergence is closed (#2346), so this "
                "agrees with Django outright now"
            )

    def test_a_quoted_float_raises_where_a_bare_one_truncates(self) -> None:
        """The quoting term, which decides between `int(2.7)` and `int("2.7")`.

        One character of template syntax separates truncation from a 500, so
        this is the load-bearing half of `python_int_arg`.
        """
        assert _rust.render_template("{{ p|wordwrap:2.7 }}", {"p": "aa bb"}) == "aa\nbb"
        with pytest.raises(RuntimeError, match="needs an integer argument"):
            _rust.render_template('{{ p|wordwrap:"2.7" }}', {"p": "aa bb"})


class TestErrorsSurfaceUsefully:
    """A Rust `Err` must reach Python naming the filter and the argument, from
    every render position — not a bare panic and not a swallowed error.
    """

    POSITIONS = [
        '{{ p|center:"nope" }}',
        '{% if p|center:"nope" %}y{% endif %}',
        '{% for x in p|center:"nope" %}{{ x }}{% endfor %}',
        '{% firstof p|center:"nope" %}',
        '{% with q=p|center:"nope" %}{{ q }}{% endwith %}',
        '{{ p|center:"nope"|upper }}',
        '{% if 1 %}{{ p|center:"nope" }}{% endif %}',
    ]

    @pytest.mark.parametrize("source", POSITIONS)
    def test_every_render_position_propagates(self, source: str) -> None:
        with pytest.raises(RuntimeError) as raised:
            _rust.render_template(source, {"p": "ab"})
        assert "center" in str(raised.value)
        assert "nope" in str(raised.value)

    def test_the_message_names_the_filter_and_the_argument(self) -> None:
        with pytest.raises(RuntimeError) as raised:
            _rust.render_template('{{ p|wordwrap:"seventy" }}', {"p": "a b"})
        message = str(raised.value)
        assert "wordwrap" in message
        assert "seventy" in message

    def test_the_resolve_miss_message_names_the_identifier(self) -> None:
        with pytest.raises(RuntimeError) as raised:
            _rust.render_template("{{ p|wordwrap:width }}", {"p": "a b"})
        message = str(raised.value)
        assert "wordwrap" in message
        assert "width" in message
        assert "does not resolve" in message

    #: Rust's format spec holds its width in a `u16`, so `format!("{s:<width$}")`
    #: panics at exactly one past `u16::MAX`. Pinned as a BOUNDARY, not a single
    #: point: a lone `70000` case says nothing about where the edge is, and it
    #: is what made this hard for a reviewer to reproduce.
    FORMATTER_WIDTH_CAP = 65535

    @pytest.mark.parametrize("name", ["ljust", "rjust"])
    @pytest.mark.parametrize("width", [1, 100, FORMATTER_WIDTH_CAP, FORMATTER_WIDTH_CAP + 1, 70000])
    def test_no_panic_at_any_width(self, name: str, width: int) -> None:
        """`format!("{s:<width$}")` panicked past the cap with a
        `PanicException` — whose MRO is `BaseException` directly, so it does not
        inherit from `Exception` and escapes the consumer's `except Exception`,
        killing the session rather than the render. Both pad filters build the
        padding explicitly now, as `center` always did.
        """
        out = _rust.render_template('{{ p|%s:"%d" }}' % (name, width), {"p": "ab"})
        assert len(out) == max(width, 2)

    def test_a_width_below_the_cap_still_just_pads(self) -> None:
        """The boundary that made the pre-fix panic hard to reproduce.

        On `main` the width had to PARSE for `format!` to see it, and every
        argument a reviewer reaches for first landed on the silent side:
        `"x"`, `"-5"`, `"0"`, `""` and a 21-digit number all failed
        `parse::<usize>()` or clamped, giving width 0 and no panic. The panic
        needed a width that parses AND exceeds `u16::MAX` — `"65536"` is the
        smallest, and `usize::MAX` (20 digits, one shorter than the 21 that
        looks bigger) also qualified.

        Here, a negative or zero width is still just the string back; the
        unparseable ones raise, which is this PR's subject.
        """
        assert len(str(2**64 - 1)) == 20, "usize::MAX is 20 digits"
        for arg in ('"-5"', '"0"'):
            out = _rust.render_template("{{ p|ljust:%s }}" % arg, {"p": "ab"})
            assert out == "ab", f"{arg} clamps to 0 and pads nothing"
        for arg in ('"x"', '""'):
            with pytest.raises(RuntimeError, match="needs an integer argument"):
                _rust.render_template("{{ p|ljust:%s }}" % arg, {"p": "ab"})

    @pytest.mark.parametrize("width", ["1000001", "9" * 20, "9" * 21, "18446744073709551615"])
    def test_an_unallocatable_width_raises_instead_of_aborting(self, width: str) -> None:
        """#2328's own regression, found by the boundary test above.

        `python_int` SATURATES past `isize` rather than failing — right for
        `slice`, where a magnitude past `isize` selects the same elements, and
        wrong here. Routing the pad filters through it turned a 21-digit width
        from a harmless width-0 no-op (the old `parse::<usize>()` simply failed)
        into a request for `isize::MAX` spaces, which is not a catchable Rust
        error at all: the allocator ABORTS the process. That is strictly worse
        than the panic this PR set out to remove.

        Python's own answers here are `MemoryError` and, past `ssize_t`,
        `OverflowError` — both fail the render, both catchable. So does this.
        """
        for name in ("ljust", "rjust", "center"):
            with pytest.raises(RuntimeError, match="past djust's"):
                _rust.render_template('{{ p|%s:"%s" }}' % (name, width), {"p": "ab"})

    def test_the_cap_admits_every_width_a_page_could_want(self) -> None:
        # Non-vacuity for the cap: a bound that refused ordinary widths would
        # pass the test above for the wrong reason.
        out = _rust.render_template('{{ p|ljust:"1000000" }}', {"p": "ab"})
        assert len(out) == 1_000_000

    def test_no_other_filter_allocates_from_a_saturating_width(self) -> None:
        """`center`/`ljust`/`rjust` are capped — but are they the whole set?

        `python_int` saturates past `isize`, so a 21-digit argument becomes
        `isize::MAX` for EVERY numeric filter, not only the three that were
        capped. Any other filter that turned that into an allocation would abort
        the same way, and the argument-axis differential's corpus has no
        huge-width case at all, so it never exercised this. Enumerating the
        surface rather than trusting the three (v1.0.0rc4 finding #1).

        Caveat worth stating: an allocator abort kills the interpreter, so a
        regression here takes pytest down with it rather than failing a case.
        That is a loud failure, just an ugly one — and far better than the
        silence of not checking. The subprocess-per-filter version that proved
        this the first time is too slow for the suite.
        """
        values: dict[str, Any] = dict(VALUES)
        values.setdefault("urlizetrunc", "see http://example.com/aaaa now")
        for name in django_argument_filters():
            value = values.get(name, TEXT)
            for width in ("9" * 21, "18446744073709551615", "999999999"):
                source = '{{ p|%s:"%s" }}' % (name, width)
                try:
                    _rust.render_template(source, {"p": value})
                except RuntimeError:
                    pass  # A raise is fine; an abort is what this rules out.

    def test_urlizetrunc_is_not_capped(self) -> None:
        """Its limit is a comparison bound, never an allocation, so the pad cap
        would be a divergence for nothing. A huge limit means "do not
        truncate", which is Django's answer too."""
        url = "see http://example.com/aaaa now"
        source = '{{ p|urlizetrunc:"999999999999" }}'
        assert "example.com/aaaa" in _rust.render_template(source, {"p": url})

    def test_center_never_had_the_bug(self) -> None:
        """The control: `center` built its padding explicitly all along, which
        is why only the two `format!` filters panicked. Without this the
        boundary test above could pass for the wrong reason."""
        out = _rust.render_template('{{ p|center:"%d" }}' % 70000, {"p": "ab"})
        assert len(out) == 70000


class TestTheTwoRegressionsTheDifferentialCaught:
    """Both were introduced by the first pass of this fix and found only by a
    two-build sweep of the ARGUMENT axis — 508 regressed cells that the whole
    unit suite, and `scripts/filter-parity-differential.py`, were green over.

    That script's own corpus gives every filter ONE VALID argument
    (`FILTER_ARGS`), so it reported literally zero moved cells in either
    direction: it structurally cannot see a change about arguments that do not
    parse or do not resolve. Its docstring names that failure mode ("the tool is
    only ever as good as the shapes it builds"); this is another instance, and
    widening its corpus is #2345.
    """

    def test_a_bool_argument_is_an_int(self) -> None:
        """`bool` IS an `int` in Python, and Django's `Context.builtins`
        resolve a bare `True`/`False` to the real objects — so
        `{{ p|center:True }}` is `"ab".center(1)`, not an error.

        The first pass exempted `True`/`False` from the resolve-miss raise and
        stopped there, which just moved the failure into the numeric parse.
        """
        for name, value in (
            ("center", "ab"),
            ("ljust", "ab"),
            ("rjust", "ab"),
            ("wordwrap", "aa bb cc"),
            ("truncatechars", "abcdef"),
            ("get_digit", 123456),
            ("floatformat", 3.14159),
        ):
            for arg in ("True", "False"):
                source = "{{ p|%s:%s }}" % (name, arg)
                if raises_django(source, value):
                    # `wordwrap:False` is width 0, which is `textwrap`'s own
                    # guard rather than `int()`'s — a raise on both sides.
                    assert raises_djust(source, value), source
                    continue
                assert _rust.render_template(source, {"p": value}) == DjangoTemplate(source).render(
                    DjangoContext({"p": value})
                ), source

    def test_a_none_argument_raises_past_the_caught_valueerror(self) -> None:
        """`int(None)` is a **TypeError**, and no filter's `except ValueError`
        catches it — so `None` raises even on the `ReturnInput` filters."""
        for name, value in (
            ("center", "ab"),
            ("wordwrap", "aa bb"),
            ("truncatechars", "abcdef"),
            ("get_digit", 123456),
            ("floatformat", 3.14159),
        ):
            assert raises_django("{{ p|%s:None }}" % name, value), f"{name}: Django changed"
            assert raises_djust("{{ p|%s:None }}" % name, value), name

    def test_a_quoted_none_is_the_string_and_takes_the_normal_policy(self) -> None:
        # `int("None")` IS a ValueError, so the caught path applies.
        assert _rust.render_template('{{ p|truncatechars:"None" }}', {"p": "abcdef"}) == "abcdef"
        with pytest.raises(RuntimeError, match="needs an integer argument"):
            _rust.render_template('{{ p|center:"None" }}', {"p": "ab"})

    def test_floatformat_parses_the_value_before_the_argument(self) -> None:
        """The ordering rule, which the `None` raise has to respect.

        Django's `floatformat` returns `""` for a value it cannot parse WITHOUT
        ever running `int(arg)`. A first pass put the `None` guard in the
        dispatch arm, ahead of that, and the differential reported 36 cells
        where djust raised for a dict or a datetime value.
        """
        for value in ("abc", {"a": 1}, [1, 2], datetime.datetime(2020, 1, 1)):
            source = "{{ p|floatformat:None }}"
            assert _rust.render_template(source, {"p": value}) == "", (
                f"{value!r}: the value parse decides first"
            )
        # And with a usable value, it does raise.
        with pytest.raises(RuntimeError, match="TypeError"):
            _rust.render_template("{{ p|floatformat:None }}", {"p": 1.5})

    def test_only_the_if_tag_swallows_a_resolution_failure(self) -> None:
        """`django.template.defaulttags.IfNode.render` wraps its condition in
        `except VariableDoesNotExist: match = None`. No other construct does,
        and it does NOT catch the `ValueError` from an unparseable argument.
        """
        shapes = {
            "var": "{{ p|center:missingvar }}",
            "for": "{% for x in p|center:missingvar %}[{{ x }}]{% endfor %}",
            "with": "{% with q=p|center:missingvar %}[{{ q }}]{% endwith %}",
        }
        for label, source in shapes.items():
            assert raises_django(source, "ab"), f"{label}: Django changed"
            assert raises_djust(source, "ab"), f"{label} must propagate"

        for source in (
            "{% if p|center:missingvar %}Y{% else %}N{% endif %}",
            "{% if p|center:no.such.path %}Y{% else %}N{% endif %}",
        ):
            assert not raises_django(source, "ab"), "Django changed"
            assert _rust.render_template(source, {"p": "ab"}) == "N", source

    def test_the_if_catch_is_narrow(self) -> None:
        """It must NOT swallow the unparseable-argument error, or `{% if %}`
        becomes a place where real failures go quiet — which is the bug class
        this whole issue is about, reintroduced one construct over."""
        source = '{% if p|center:"nope" %}Y{% else %}N{% endif %}'
        assert raises_django(source, "ab"), "Django changed"
        with pytest.raises(RuntimeError, match="needs an integer argument"):
            _rust.render_template(source, {"p": "ab"})


class TestChokepointIsTheOnlyParser:
    """The mechanical pin. A comment is not a guard (#1859).

    If a future filter reintroduces `arg.and_then(|s| s.parse::<usize>().ok())`
    this goes red, which is the whole point: the filters agreeing with
    Django is the consequence of the structure, and the structure is what needs
    protecting.
    """

    #: Every `int_arg!` call site, as `(default_for_missing, policy)`. A SET,
    #: not a floor (#1125) — deleting a site fails this too.
    EXPECTED_SITES = sorted(
        [
            ("10", "BadArg::ReturnInput"),  # truncatewords
            ("20", "BadArg::ReturnInput"),  # truncatechars
            ("1", "BadArg::Raise"),  # divisibleby
            ("75", "BadArg::Raise"),  # wordwrap
            ("0", "BadArg::Raise"),  # ljust
            ("0", "BadArg::Raise"),  # rjust
            ("0", "BadArg::Raise"),  # center
            ("0", "BadArg::ReturnInput"),  # get_digit
            ("0", "BadArg::Raise"),  # urlizetrunc
            ("20", "BadArg::ReturnInput"),  # truncatechars_html
            ("10", "BadArg::ReturnInput"),  # truncatewords_html
        ]
    )

    #: `.parse::<..>()` calls in `filters.rs` that are applied to something
    #: derived from the ARGUMENT and legitimately do NOT go through the
    #: chokepoint. Each needs a reason; an unlisted one fails the test.
    JUSTIFIED_ARGUMENT_PARSES = {
        "sort_key.parse::<usize>().ok()": (
            "dictsort asks 'was the Python object an int', not int(arg) — an "
            "unquoted numeric argument is an index and a quoted one is a key"
        ),
        "let width = prefix.parse::<usize>().unwrap_or(0);": (
            "stringformat's printf WIDTH FIELD, a sub-part of an already-"
            "extracted spec, not the filter argument. stringformat's spec "
            "handling is its own surface with its own divergences — see "
            "OUTPUT_DIVERGES_FOR_ANOTHER_REASON"
        ),
    }

    #: Every `int_arg!` call site, capturing its `(missing, policy)`.
    #:
    #: WHITESPACE-TOLERANT on purpose. #2366 added a sixth macro argument and
    #: `cargo fmt` then split every one of these calls across six lines, so a
    #: single-line regex found ZERO sites and both pins below went red — while
    #: reporting "0 dispatch arms reach the chokepoint", which reads like the
    #: chokepoint was deleted rather than like the formatter moved a newline.
    #: The property these pins are about is the SET of call sites and their
    #: policies, not the line layout, so the pattern says so.
    CALL_SITE_RE = (
        r"int_arg!\(\s*filter_name,\s*arg,\s*arg_was_quoted,"
        r"\s*arg_int_is_type_error,\s*(\d+),\s*(BadArg::\w+),?\s*\)"
    )

    @staticmethod
    def source() -> str:
        return FILTERS_RS.read_text(encoding="utf-8")

    @staticmethod
    def code_lines(text: str) -> list[str]:
        """Lines with the comments dropped.

        The pins below search for the pre-fix SHAPE, and this file's own
        doc-comments quote that shape verbatim to explain what was wrong with
        it. Scanning comments would make the pin fire on its own documentation
        — green only while nobody explains the bug.
        """
        return [
            stripped
            for stripped in (line.strip() for line in text.splitlines())
            if stripped and not stripped.startswith(("//", "/*", "*"))
        ]

    def test_the_customer_count_is_mechanical(self) -> None:
        """Every count in this file's prose, recomputed from the source.

        The first draft said "fourteen sites", which was a guess and was wrong;
        the real figure is TWELVE arms through FOUR parsers. This repo has
        shipped a wrong prose count more than once, so the number is derived
        rather than written down.
        """
        arms = re.findall(self.CALL_SITE_RE, self.source())
        assert len(arms) == 11, f"{len(arms)} dispatch arms reach the chokepoint"
        # Plus `floatformat`, which reaches `filter_int_arg` directly from its
        # own module rather than through the macro.
        floatformat_rs = FILTERS_RS.with_name("floatformat.rs").read_text(encoding="utf-8")
        assert floatformat_rs.count("crate::filters::filter_int_arg(") == 1
        # One parser, where there were four.
        assert self.source().count("fn python_int_arg(") == 1
        assert "fn truncate_arg(" not in self.source(), (
            "`truncate_arg` was one of the four parsers; it must not come back"
        )
        assert "fn parse_int_like" in floatformat_rs, (
            "kept as a named shim so `parse_arg` reads the same; it must DELEGATE"
        )

    def test_every_allocating_width_goes_through_the_cap(self) -> None:
        """`ljust`/`rjust`/`center` are the three filters that turn the argument
        into an allocation, and all three must ask `pad_width` — which refuses
        past `MAX_PAD_WIDTH`. A fourth pad filter that skipped it could abort
        the process on a template-supplied width, so the count is pinned.

        `urlizetrunc` is deliberately NOT here: its limit is a comparison bound,
        never an allocation.
        """
        source = self.source()
        assert len(re.findall(r"pad_width!\(", source)) == 3
        assert "const MAX_PAD_WIDTH" in source
        # The cap must be consulted in `pad_width` itself, not at a call site,
        # or a fourth caller would silently miss it.
        assert re.search(r"fn pad_width\([^)]*\)[^{]*\{[^}]*MAX_PAD_WIDTH", source, re.S)

    def test_the_call_site_set_is_pinned(self) -> None:
        found = sorted(re.findall(self.CALL_SITE_RE, self.source()))
        assert found == self.EXPECTED_SITES

    def test_both_policies_are_actually_used(self) -> None:
        policies = {policy for _, policy in self.EXPECTED_SITES}
        assert policies == {"BadArg::Raise", "BadArg::ReturnInput"}

    def test_no_bare_parse_and_default_on_a_filter_argument(self) -> None:
        """The exact pre-#2328 shape, at any of the seven sites that had it."""
        offenders = [
            line
            for line in self.code_lines(self.source())
            if re.search(r"\barg\b[^;]*\.parse::<", line)
        ]
        assert not offenders, (
            "a filter argument is being parsed outside `filter_int_arg`:\n" + "\n".join(offenders)
        )

    def test_every_argument_parse_is_justified(self) -> None:
        """Catches the shape the line-local regex above cannot see: a parse of
        a variable that was assigned FROM `arg` on an earlier line.
        """
        unjustified = []
        for line in self.code_lines(self.source()):
            if ".parse::<" not in line:
                continue
            if any(justified in line for justified in self.JUSTIFIED_ARGUMENT_PARSES):
                continue
            # Not an argument parse at all unless it names one of the
            # argument-shaped locals the dispatch table uses.
            if re.search(r"\b(arg|limit|width|sort_key|element_id)\b", line):
                unjustified.append(line)
        assert not unjustified, (
            "new argument parse outside the chokepoint — route it through "
            "`filter_int_arg` or add it to JUSTIFIED_ARGUMENT_PARSES with a "
            "reason:\n" + "\n".join(unjustified)
        )

    def test_floatformat_shares_the_chokepoint(self) -> None:
        """The second customer, with the OPPOSITE policy — which is what keeps
        the policy parameter load-bearing rather than decorative."""
        floatformat_rs = FILTERS_RS.with_name("floatformat.rs").read_text(encoding="utf-8")
        code = self.code_lines(floatformat_rs)
        assert any("filter_int_arg(" in line for line in code)
        assert any("BadArg::ReturnInput" in line for line in code)
        assert not [line for line in code if "parse::<i64>" in line]


class TestTheValueAxisIsUntouched:
    """The argument is one half of `int(value) % int(arg)`; the value keeps its
    own fail-soft answer, or this fix would have changed two things at once.
    """

    def test_divisibleby_still_fails_soft_on_an_unparseable_value(self) -> None:
        assert _rust.render_template('{{ p|divisibleby:"2" }}', {"p": "notanumber"}) == "False"

    def test_a_parsed_width_of_zero_still_raises_for_its_own_reason(self) -> None:
        """`wordwrap:0` is `textwrap._wrap_chunks`'s guard, not `int()`'s, and
        the two must stay distinguishable in the message (#2293)."""
        with pytest.raises(RuntimeError, match="invalid width 0"):
            _rust.render_template('{{ p|wordwrap:"0" }}', {"p": "a b"})

    def test_decimal_values_still_reach_the_filters(self) -> None:
        assert _rust.render_template('{{ p|floatformat:"2" }}', {"p": Decimal("2.675")}) == "2.68"
