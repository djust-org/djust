"""`timesince`/`timeuntil` measure against their ARGUMENT, not always now (#2344).

What was there
--------------
Django's argument is the comparison INSTANT::

    @register.filter("timesince", is_safe=False)
    def timesince_filter(value, arg=None):
        if not value:
            return ""
        try:
            if arg:
                return timesince(value, arg)
            return timesince(value)
        except (ValueError, TypeError):
            return ""

djust's two arms read the VALUE and discarded the argument entirely —
``format_timesince(&datetime_str)`` took no comparison instant — so

* ``{{ then|timesince:other }}`` silently answered "since now" whatever
  ``other`` was, and
* ``{{ then|timesince:"notanumber" }}`` rendered a duration where Django raises.

Both are the silent-wrong-output class, and the second is why #2328 exempted
these two from its raise sweep: making an *unparseable* argument raise while a
*valid* one was still discarded would have been a half-fix, and would have
looked handled. Closing this deletes both rows from ``RAISE_BIT_NOT_CLOSED``,
and the pin there went red exactly as designed.

The three outcomes, and where each comes from
---------------------------------------------
Django's own control flow has exactly three, and this file asserts each against
live Django 5.2 rather than from the source:

1. **falsy argument -> the wall clock.** ``if arg:`` in the filter, and
   ``if not now:`` inside ``timesince``. So ``{{ p|timesince:0 }}``,
   ``:None``, ``:False`` and ``:""`` all still mean "since now" — and
   ``{{ p|timesince:"0" }}`` does NOT, because a quoted argument is a non-empty
   ``str`` and every non-empty ``str`` is truthy. One character of template
   syntax between a duration and a 500, which is why ``arg_was_quoted`` is
   load-bearing here as it is for ``add`` and ``floatformat``.
2. **a date or datetime -> that instant.** Including a bare ``date``, which
   ``timesince`` truncates to midnight via
   ``datetime(now.year, now.month, now.day)``.
3. **anything else truthy -> AttributeError.** That line is where it comes
   from: ``now.year`` on an ``int``/``str``/``bool``. AttributeError is NOT in
   the filter's caught ``(ValueError, TypeError)`` pair, so it escapes.

And one more, which IS caught: an aware value against a naive argument (or the
reverse) makes ``now - d`` a **TypeError**, so Django renders the empty string.

Ordering
--------
The VALUE is read first, which is Django's order — ``timesince`` normalizes
``d`` before it touches ``now`` — and the same rule ``floatformat`` carries
(#2328): a value djust cannot read falls soft to the value unchanged and the
argument never gets to decide anything. :class:`TestTheValueDecidesFirst` is
that ordering, asserted from both sides.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
DIFFERENTIAL = REPO / "scripts" / "filter-parity-differential.py"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "python"), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    return env


FILTERS_RS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "crates"
    / "djust_templates"
    / "src"
    / "filters.rs"
)

NOON = datetime.datetime(2020, 1, 1, 12, 0, 0)
LATER = datetime.datetime(2020, 1, 1, 15, 30, 0)
EARLIER = datetime.datetime(2020, 1, 1, 9, 0, 0)
NEXT_DAY = datetime.datetime(2020, 1, 2, 0, 0, 0)
UTC_MINUS_4 = datetime.timezone(datetime.timedelta(hours=-4))


def django_render(source: str, ctx: dict[str, Any]) -> str:
    return DjangoTemplate(source).render(DjangoContext(ctx))


def djust_render(source: str, ctx: dict[str, Any]) -> str:
    return _rust.render_template(source, ctx)


def raises_django(source: str, ctx: dict[str, Any]) -> bool:
    try:
        django_render(source, ctx)
    except Exception:  # noqa: BLE001
        return True
    return False


def raises_djust(source: str, ctx: dict[str, Any]) -> bool:
    try:
        djust_render(source, ctx)
    except BaseException:  # noqa: BLE001 — a pyo3 panic is not an `Exception`
        return True
    return False


def assert_agrees(source: str, ctx: dict[str, Any]) -> None:
    """Both engines, on the same cell, measured rather than remembered."""
    if raises_django(source, ctx):
        assert raises_djust(source, ctx), (
            f"{source} on {ctx!r}: Django raises and djust does not — the silent-"
            "wrong-output direction this issue is about"
        )
        return
    expected = django_render(source, ctx)
    assert not raises_djust(source, ctx), f"{source} on {ctx!r}: djust raised, Django did not"
    assert djust_render(source, ctx) == expected, f"{source} on {ctx!r}"


class TestTheArgumentIsTheComparisonInstant:
    """Outcome 2: the whole point of the issue."""

    @pytest.mark.parametrize(
        ("value", "instant"),
        [
            (NOON, LATER),  # forwards
            (NOON, EARLIER),  # the argument is BEFORE the value
            (NOON, NOON),  # the same instant
            (NOON, NEXT_DAY),
            (NOON, datetime.date(2020, 1, 3)),  # a bare date, truncated to midnight
            (datetime.date(2020, 1, 1), datetime.date(2020, 1, 3)),  # both dates
            (datetime.datetime(2019, 3, 4, 1, 2, 3), datetime.datetime(2021, 7, 9, 4, 5, 6)),
        ],
    )
    @pytest.mark.parametrize("name", ["timesince", "timeuntil"])
    def test_it_measures_between_the_value_and_the_argument(
        self, name: str, value: Any, instant: Any
    ) -> None:
        assert_agrees("{{ p|%s:q }}" % name, {"p": value, "q": instant})

    def test_the_answer_is_the_gap_and_not_the_time_since_now(self) -> None:
        """The sharpest cell, and the one the pre-fix build got wrong.

        `2020-01-01 12:00` to `15:30` is `3 hours, 30 minutes`. Before the fix
        djust answered the years since 2020 — a plausible-looking duration,
        which is what made it silent.
        """
        out = djust_render("{{ p|timesince:q }}", {"p": NOON, "q": LATER})
        assert out == "3\xa0hours, 30\xa0minutes"
        assert out == django_render("{{ p|timesince:q }}", {"p": NOON, "q": LATER})

    def test_timeuntil_is_the_same_computation_reversed(self) -> None:
        """`timeuntil(d, now)` is `timesince(d, now, reversed=True)`, so the two
        answer each other's question on the same pair — and a value that is not
        in the future is `0 minutes` rather than a negative duration."""
        assert djust_render("{{ p|timeuntil:q }}", {"p": LATER, "q": NOON}) == (
            djust_render("{{ p|timesince:q }}", {"p": NOON, "q": LATER})
        )
        assert djust_render("{{ p|timeuntil:q }}", {"p": NOON, "q": LATER}) == "0\xa0minutes"
        assert_agrees("{{ p|timeuntil:q }}", {"p": NOON, "q": LATER})

    @pytest.mark.parametrize(
        "arg",
        ['"2020-01-01 15:30:00"', '"2020-01-01T15:30:00+00:00"', '"2020-01-03"'],
    )
    def test_a_quoted_datetime_literal_raises_as_django_does(self, arg: str) -> None:
        """A QUOTED argument is a `str`, and Django accepts NO string here —
        not even a date-shaped one. Measured, all three spellings:
        `AttributeError: 'SafeString' object has no attribute 'year'`.

        The rest of this fix reads a date-shaped string as a date, because a
        Python `datetime` crosses into Rust as a string and has no other
        spelling. A quoted literal never came from Python, so the convention
        has nothing to justify for it — which is what keeps the wire-format
        residue bounded rather than open-ended.
        """
        assert_agrees("{{ p|timesince:%s }}" % arg, {"p": NOON})
        assert raises_djust("{{ p|timesince:%s }}" % arg, {"p": NOON}), arg

    def test_no_argument_still_measures_from_now(self) -> None:
        """The base case, pinned so the fix cannot regress it. Compared as a
        SHAPE rather than a string, because both sides read the wall clock."""
        for name in ("timesince", "timeuntil"):
            out = djust_render("{{ p|%s }}" % name, {"p": NOON})
            assert re.fullmatch(r"\d+\xa0\w+(, \d+\xa0\w+)?", out), out


class TestAFalsyArgumentMeansNow:
    """Outcome 1: Django's `if arg:`, and the quoting term that decides it."""

    #: Every falsy spelling recoverable from the argument's text. Unquoted, so
    #: `Value`'s `Display` is what produced them.
    FALSY_LITERALS = ["0", "0.0", "-0", "None", "False", "0e0", ".0"]

    @pytest.mark.parametrize("arg", FALSY_LITERALS)
    @pytest.mark.parametrize("name", ["timesince", "timeuntil"])
    def test_a_falsy_literal_falls_through_to_the_wall_clock(self, name: str, arg: str) -> None:
        source = "{{ p|%s:%s }}" % (name, arg)
        assert not raises_django(source, {"p": NOON}), f"Django changed for {arg}"
        assert not raises_djust(source, {"p": NOON}), source
        # Both read the clock, so compare the SHAPE and against the no-argument
        # form rather than byte-for-byte across two `datetime.now()` calls.
        assert djust_render(source, {"p": NOON}) == djust_render("{{ p|%s }}" % name, {"p": NOON})

    @pytest.mark.parametrize(
        "value",
        ["", 0, False, None, [], {}, ()],
        ids=["str", "int", "bool", "none", "list", "dict", "tuple"],
    )
    def test_a_falsy_resolved_value_falls_through_too(self, value: Any) -> None:
        """The common real shape: an OPTIONAL comparison instant that is None.

        `{{ updated|timesince:published }}` where `published` is null must not
        become a 500.
        """
        source = "{{ p|timesince:q }}"
        assert not raises_django(source, {"p": NOON, "q": value}), f"Django changed for {value!r}"
        assert not raises_djust(source, {"p": NOON, "q": value}), repr(value)
        assert djust_render(source, {"p": NOON, "q": value}) == djust_render(
            "{{ p|timesince }}", {"p": NOON}
        )

    @pytest.mark.parametrize("arg", ['"0"', '"None"', '"False"', '"0.0"'])
    def test_the_same_text_QUOTED_is_a_truthy_string_and_raises(self, arg: str) -> None:
        """The quoting term, and the load-bearing half of the falsiness rule.

        `{{ p|timesince:0 }}` is the integer zero — falsy, so Django measures
        from now. `{{ p|timesince:"0" }}` is the string `"0"` — truthy, so
        Django reaches `now.year` and raises AttributeError. One character of
        template syntax apart, and a rule that ignored `arg_was_quoted` would
        get one of the two wrong whichever way it guessed.
        """
        source = "{{ p|timesince:%s }}" % arg
        assert raises_django(source, {"p": NOON}), f"Django changed for {arg}"
        with pytest.raises(RuntimeError, match="is not a date or datetime"):
            djust_render(source, {"p": NOON})

    def test_an_empty_quoted_argument_is_falsy_on_both_sides(self) -> None:
        """`""` is the one quoted spelling that IS falsy — an empty `str`."""
        assert not raises_django('{{ p|timesince:"" }}', {"p": NOON})
        assert djust_render('{{ p|timesince:"" }}', {"p": NOON}) == djust_render(
            "{{ p|timesince }}", {"p": NOON}
        )


class TestATruthyNonDateRaises:
    """Outcome 3: `now.year` on something with no `.year`.

    AttributeError is NOT in the filter's caught `(ValueError, TypeError)`, so
    it escapes — which is why this is a raise and not an empty string.
    """

    @pytest.mark.parametrize(
        "arg", ['"notanumber"', '"2020-13-45"', "1", "-3", "True", '"abc"', '"12:30"']
    )
    @pytest.mark.parametrize("name", ["timesince", "timeuntil"])
    def test_a_truthy_non_date_argument_raises_on_both_engines(self, name: str, arg: str) -> None:
        source = "{{ p|%s:%s }}" % (name, arg)
        assert raises_django(source, {"p": NOON}), f"Django changed for {arg}"
        assert raises_djust(source, {"p": NOON}), source

    @pytest.mark.parametrize("value", ["notadate", 5, True, ["a"], {"k": 1}])
    def test_a_truthy_non_date_RESOLVED_argument_raises_too(self, value: Any) -> None:
        source = "{{ p|timesince:q }}"
        assert raises_django(source, {"p": NOON, "q": value}), f"Django changed for {value!r}"
        assert raises_djust(source, {"p": NOON, "q": value}), repr(value)

    def test_the_message_names_the_filter_and_the_argument(self) -> None:
        with pytest.raises(RuntimeError) as raised:
            djust_render('{{ p|timesince:"whenever" }}', {"p": NOON})
        message = str(raised.value)
        assert "timesince" in message
        assert "whenever" in message
        assert "AttributeError" in message, "the message should name what Django does"

    @pytest.mark.parametrize(
        "source",
        [
            '{{ p|timesince:"nope" }}',
            '{% if p|timesince:"nope" %}y{% endif %}',
            '{% with q=p|timesince:"nope" %}{{ q }}{% endwith %}',
            '{% for x in p|timesince:"nope" %}{{ x }}{% endfor %}',
            '{{ p|timesince:"nope"|upper }}',
        ],
    )
    def test_every_render_position_propagates(self, source: str) -> None:
        """A raise that only escapes from `{{ }}` would be the same silent
        failure one construct over. `{% if %}` catches `VariableDoesNotExist`
        and nothing else, so it must NOT swallow this."""
        assert raises_djust(source, {"p": NOON}), source


class TestAwarenessMixing:
    """The fourth outcome, and the one Django DOES catch.

    `timesince` reconciles two AWARE operands with `now.astimezone(d.tzinfo)`
    and does nothing at all for a mixed pair, so `now - d` raises TypeError —
    which IS in the caught pair, so the filter renders the empty string.
    """

    AWARE_NOON = datetime.datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC_MINUS_4)
    AWARE_LATER = datetime.datetime(2020, 1, 1, 18, 0, 0, tzinfo=datetime.timezone.utc)

    def test_two_aware_operands_are_compared_in_the_values_offset(self) -> None:
        """`12:00-04:00` is `16:00Z`, so `18:00Z` is two hours later."""
        assert_agrees("{{ p|timesince:q }}", {"p": self.AWARE_NOON, "q": self.AWARE_LATER})
        assert (
            djust_render("{{ p|timesince:q }}", {"p": self.AWARE_NOON, "q": self.AWARE_LATER})
            == "2\xa0hours"
        )

    @pytest.mark.parametrize("name", ["timesince", "timeuntil"])
    def test_an_aware_value_with_a_naive_argument_is_the_empty_string(self, name: str) -> None:
        assert_agrees("{{ p|%s:q }}" % name, {"p": self.AWARE_NOON, "q": LATER})
        assert djust_render("{{ p|%s:q }}" % name, {"p": self.AWARE_NOON, "q": LATER}) == ""

    @pytest.mark.parametrize("name", ["timesince", "timeuntil"])
    def test_a_naive_value_with_an_aware_argument_is_the_empty_string(self, name: str) -> None:
        assert_agrees("{{ p|%s:q }}" % name, {"p": NOON, "q": self.AWARE_LATER})
        assert djust_render("{{ p|%s:q }}" % name, {"p": NOON, "q": self.AWARE_LATER}) == ""

    def test_the_empty_string_is_not_a_raise(self) -> None:
        """Non-vacuity for the `Incomparable` arm: it must be distinguishable
        from the `AttributeError` arm, which raises. A single "give up" answer
        for both would be wrong in one direction or the other."""
        assert djust_render("{{ p|timesince:q }}", {"p": NOON, "q": self.AWARE_LATER}) == ""
        with pytest.raises(RuntimeError):
            djust_render("{{ p|timesince:q }}", {"p": NOON, "q": "notadate"})


class TestTheValueDecidesFirst:
    """Django normalizes `d` before it touches `now`, and so does this."""

    @pytest.mark.parametrize("value", ["notadate", "", "abc"])
    def test_an_unreadable_value_falls_soft_before_the_argument_is_judged(self, value: str) -> None:
        """djust's fail-soft convention for an unreadable VALUE (#2227) is
        pre-existing and out of scope here. What #2344 must not do is let the
        ARGUMENT raise first and turn that fail-soft into a 500 — the ordering
        mistake #2328's `floatformat` pass made and had to undo.
        """
        assert djust_render('{{ p|timesince:"whenever" }}', {"p": value}) == (
            djust_render("{{ p|timesince }}", {"p": value})
        )

    def test_and_with_a_readable_value_the_argument_does_raise(self) -> None:
        """Non-vacuity for the ordering: if the argument never raised, the test
        above would pass with the whole argument rule removed."""
        with pytest.raises(RuntimeError, match="is not a date or datetime"):
            djust_render('{{ p|timesince:"whenever" }}', {"p": NOON})


class TestTheFalsinessResidueIsNamed:
    """What the string-level falsiness rule can and cannot recover.

    The argument reaches the dispatch table as a STRING — a quoted literal
    verbatim, or a resolved context value through `Value`'s `Display` — so
    Python's truthiness has to be recovered from the text plus the quoting hint.
    Enumerating that is the honest form; asserting "it is exact" would be
    false.
    """

    def test_the_legacy_render_mode_spells_False_differently_and_is_handled(self) -> None:
        """`Display` has TWO modes, and only one of them is the default.

        `django_value_repr` (on by default since #2203) spells a bool
        `True`/`False`; `legacy_display` — the flag's OFF path — spells it
        Rust's `true`/`false`, and renders `None` as the empty string. A
        falsiness rule that only knew the default would answer differently
        under a flag whose entire purpose is rendering parity, which is the
        parallel-path shape one mode over (#1646).

        Django is unaffected by the flag: `False` is falsy either way, so the
        expected answer is the same and only djust's input text changes.
        """
        _rust.set_django_value_repr(False)
        try:
            for value in (False, None, ""):
                out = djust_render("{{ p|timesince:q }}", {"p": NOON, "q": value})
                assert out == djust_render("{{ p|timesince }}", {"p": NOON}), repr(value)
            # And a truthy non-date still raises in legacy mode.
            assert raises_djust("{{ p|timesince:q }}", {"p": NOON, "q": "notadate"})
            assert raises_djust("{{ p|timesince:q }}", {"p": NOON, "q": True})
        finally:
            _rust.set_django_value_repr(True)
        # The default mode is restored, and still right.
        assert djust_render("{{ p|timesince:q }}", {"p": NOON, "q": False}) == djust_render(
            "{{ p|timesince }}", {"p": NOON}
        )

    def test_the_legacy_mode_cannot_tell_an_empty_sequence_from_a_full_one(self) -> None:
        """The one residue the two-mode rule does NOT close, stated.

        `legacy_display` renders EVERY sequence as the literal `[List]`, so an
        empty list is indistinguishable from a full one — and both are
        non-dates, so Django measures from now for the first and raises for the
        second. djust raises for both.

        In the DEFAULT mode there is no such gap: `[]` and `['a']` have
        different texts, and the first is in the falsy set. This is a
        legacy-flag residue, not a general one.
        """
        _rust.set_django_value_repr(False)
        try:
            assert raises_djust("{{ p|timesince:q }}", {"p": NOON, "q": []})
            assert not raises_django("{{ p|timesince:q }}", {"p": NOON, "q": []})
        finally:
            _rust.set_django_value_repr(True)
        # The default mode: no divergence, which is what bounds this to the flag.
        assert not raises_djust("{{ p|timesince:q }}", {"p": NOON, "q": []})
        assert djust_render("{{ p|timesince:q }}", {"p": NOON, "q": []}) == djust_render(
            "{{ p|timesince }}", {"p": NOON}
        )

    def test_every_display_arm_that_can_be_falsy_is_handled(self) -> None:
        """Mechanical, against `Value`'s `Display` (#1859: a pin that is not
        derived from the thing it pins is decorative).

        A new `Value` variant whose `Display` can produce a falsy Python object
        has to be considered here, and this fails until its spelling is either
        in the predicate or listed below with a reason.
        """
        core = (
            pathlib.Path(__file__).resolve().parents[2] / "crates" / "djust_core" / "src" / "lib.rs"
        ).read_text(encoding="utf-8")
        body = core.split("impl fmt::Display for Value {", 1)[1].split("\n}\n", 1)[0]
        # Variants only: `Value::py_repr` in the same body is a method call, and
        # a regex that swept it in would demand a falsy-text answer for a
        # function. Rust variants are UpperCamel by convention and this file's
        # are without exception.
        variants = {v for v in re.findall(r"Value::(\w+)", body) if v[:1].isupper()}
        assert len(variants) >= 10, f"the Display match did not parse: {variants}"

        #: The falsy Python object each variant can hold, and the text its
        #: `Display` produces for it. A variant with no falsy inhabitant is
        #: mapped to `None`.
        FALSY_TEXT = {
            "Missing": "",
            "None": "None",
            "Bool": "False",
            "Integer": "0",
            "Float": "0.0",
            "Decimal": "0",
            "BigInt": "0",
            "String": "",
            "List": "[]",
            "Tuple": "()",
            "Object": None,  # a serialized model map; never falsy in Python
            "Dict": "{}",
        }
        unmapped = variants - set(FALSY_TEXT)
        assert not unmapped, (
            f"{sorted(unmapped)} are `Value` variants this test has no falsy-text "
            "answer for. Decide whether each can hold a Python-falsy object, and if "
            "it can, make sure `timesince_arg_is_falsy` in filters.rs accepts its "
            "`Display` text — in the SAME commit."
        )

        predicate = FILTERS_RS.read_text(encoding="utf-8")
        predicate = predicate.split("fn timesince_arg_is_falsy(", 1)[1].split("\n}", 1)[0]
        for variant, text in FALSY_TEXT.items():
            if text is None:
                continue
            if text == "":
                assert "arg.is_empty()" in predicate, variant
                continue
            assert f'"{text}"' in predicate or "python_float" in predicate, (
                f"Value::{variant} displays a falsy object as {text!r} and "
                "`timesince_arg_is_falsy` does not accept it"
            )

    @pytest.mark.parametrize("text", ["0", "None", "False", "2020-01-01 15:30:00"])
    def test_the_residue_is_a_RESOLVED_STRING_that_reads_as_something_else(self, text: str) -> None:
        """The residue, measured and named rather than hoped away.

        A Python `datetime` crosses into Rust as a STRING and has no other
        spelling, so djust reads a date-shaped argument as a date — the same
        convention its VALUE side has carried since #2203, where
        `{{ "2020-01-01 12:00:00"|timesince }}` renders and Django's
        `'str' object has no attribute 'year'` does not.

        The cost is symmetric and is here: a resolved argument that is genuinely
        a `str` is indistinguishable from the object whose `str()` it matches.
        Django raises for all four of these (a `str` is truthy and has no
        `.year`); djust reads `"0"`/`"None"`/`"False"` as the falsy objects they
        spell and the fourth as the datetime it spells.

        This is the pre-existing wire-format convention rather than anything
        #2344 introduced, and it is bounded: a QUOTED argument never came from
        Python, so it gets Django's exact answer and raises — which is
        `test_a_quoted_datetime_literal_raises_as_django_does` above.
        """
        source = "{{ p|timesince:q }}"
        assert raises_django(source, {"p": NOON, "q": text}), (
            f"Django changed: a str argument {text!r} no longer raises"
        )
        assert not raises_djust(source, {"p": NOON, "q": text}), (
            f"{text!r} now raises in djust too. If the wire format grew a way to tell a "
            "str from the object it spells, this residue is closed — assert the "
            "agreement instead of the divergence."
        )

    def test_the_residue_does_not_extend_to_a_quoted_argument(self) -> None:
        """The bound on the residue above, and the reason it is a bound rather
        than an excuse: a quoted literal is authored template text that never
        crossed the wire, so the convention has nothing to justify for it."""
        for arg in ('"0"', '"2020-01-01 15:30:00"'):
            source = "{{ p|timesince:%s }}" % arg
            assert raises_django(source, {"p": NOON}), arg
            assert raises_djust(source, {"p": NOON}), arg


class TestTheManifestDemandsThisErrorBeReachable:
    """The reachability manifest (#2345) on this PR's new argument error.

    It picked the requirement up automatically — the set is recomputed from the
    Rust source, so the `argument` axis went 4 required to 5 with no edit — and
    then reported the error UNREACHABLE. Both halves of that were informative:

    * the report was RIGHT the first time. No input in the corpus was
      date-shaped, and this fix parses the VALUE before the argument (Django's
      own order), so every ``timesince`` / ``timeuntil`` cell took the
      unreadable-value branch and the argument logic was never reached. A
      corpus with no readable date cannot measure the two filters whose
      argument is the subject. ``s-datetime`` is what the manifest asked for.

    * the report was still RED afterwards, and that one was the manifest's own
      bug: ``_swept_argument_errors`` open-coded the corpus product as
      ``sorted(FILTER_ARGS) x ...`` while ``arg_cells`` had moved to
      ``django_argument_filters()``. ``timesince`` is one of the four names in
      the second set and not the first, so the axis measured a narrower corpus
      than it ships. It iterates ``arg_cells()`` itself now — two copies of one
      product is the drift this whole mechanism exists to make visible, and it
      had grown one inside itself.
    """

    @staticmethod
    def argument_axis() -> dict:
        proc = subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(DIFFERENTIAL), "--manifest", "--json"],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-3000:]
        data = json.loads(proc.stdout)
        return next(r for r in data["axes"] if r["axis"] == "argument")

    def test_the_new_error_is_required_and_reachable(self) -> None:
        row = self.argument_axis()
        assert row["missing"] == [], row["missing"]
        assert any("is not a date or datetime" in r for r in row["required"]), (
            "this PR's argument error is not in the requirement set, so nothing "
            "demands the corpus be able to reach it"
        )

    def test_the_corpus_carries_a_date_shaped_value(self) -> None:
        """What the manifest asked for, pinned. Without it the value parse
        fails first and no cell reaches the argument logic at all."""
        source = DIFFERENTIAL.read_text(encoding="utf-8")
        assert '"s-datetime"' in source, (
            "the date-shaped corpus input is gone, so every timesince/timeuntil "
            "cell takes the unreadable-value branch and this PR is unmeasured"
        )
        # And it must be on the chain axis too, which is where the argument
        # cells draw their values from.
        block = source.split("INPUTS_2 = [", 1)[1].split("]", 1)[0]
        assert '"s-datetime"' in block, block

    def test_the_axis_measures_the_corpus_it_ships(self) -> None:
        """The manifest's own drift, pinned structurally.

        The swept side must iterate the SAME generator that builds the cells.
        Re-deriving the product is what let the two halves disagree about which
        filters are swept — 25 against 29 — and the axis then reported an error
        unreachable that its own corpus reaches.
        """
        source = DIFFERENTIAL.read_text(encoding="utf-8")
        body = source.split("def _swept_argument_errors(", 1)[1].split("\ndef ", 1)[0]
        # CODE only. The docstring explains this very drift and names
        # `FILTER_ARGS` to do it, so a scan that included prose would fire on
        # its own documentation — green exactly while nobody explains the bug.
        body = body.split('"""', 2)[-1]
        assert "arg_cells()" in body, (
            "`_swept_argument_errors` re-derives the corpus product instead of "
            "iterating `arg_cells()`, so the two halves of the axis can disagree "
            "about what the corpus contains"
        )
        assert "FILTER_ARGS" not in body, (
            "the swept side is back on FILTER_ARGS, which is the ESCAPING axis's "
            "table and has 25 of the 29 argument-taking filters"
        )


class TestOneBodyForTwoFilters:
    """The structural half. Two filters implementing one argument rule is
    exactly the shape that drifts (#1646), and it is how these two got here:
    `format_timesince` and `format_timeuntil` were near-copies.
    """

    @staticmethod
    def source() -> str:
        return FILTERS_RS.read_text(encoding="utf-8")

    def test_the_two_arms_share_one_body(self) -> None:
        source = self.source()
        assert source.count("fn timesince_or_until(") == 1
        calls = re.findall(
            r"timesince_or_until\(filter_name, value, arg, arg_was_quoted, (\w+)\)", source
        )
        assert sorted(calls) == ["false", "true"], (
            f"expected exactly the two dispatch arms, one per direction: {calls}"
        )

    def test_the_old_two_body_shape_is_gone(self) -> None:
        """The pre-fix spelling, pinned so it cannot come back."""
        source = self.source()
        for gone in ("fn format_timesince(", "fn format_timeuntil("):
            assert gone not in source, (
                f"{gone} is back — it was the second copy of the argument rule, and "
                "both copies discarded the argument"
            )

    def test_one_definition_of_the_falsiness_rule(self) -> None:
        assert self.source().count("fn timesince_arg_is_falsy(") == 1

    def test_the_three_outcomes_are_an_enum_rather_than_two_bools(self) -> None:
        """`Now` / `At` / `Incomparable` are genuinely three answers — the
        third renders "" where the second renders a duration and a failure
        raises — so collapsing any two would be wrong in one direction."""
        source = self.source()
        body = source.split("enum ComparisonInstant {", 1)[1].split("\n}", 1)[0]
        assert set(re.findall(r"^\s{4}(\w+)", body, re.M)) == {"Now", "At", "Incomparable"}
