"""Three argument-axis divergences whose cause is not ``int(arg)`` (#2346).

#2328 routed every built-in that reads its argument as a NUMBER through one
chokepoint, ``filters::filter_int_arg``, and made an unparseable or unresolvable
argument raise. These three were left alone by it and each for the same
structural reason: their divergence is not in the parse.

===================== ================================= ======================
 filter                django                            djust, before this
===================== ================================= ======================
``urlizetrunc:"5"``    ``http…``                         ``ht...``
``divisibleby:"0"``    ``ZeroDivisionError``             ``False``
``floatformat:""``     ``IndexError``                    ``3.1``
===================== ================================= ======================

Every value below is Django 5.2.16, run rather than remembered.

Why each is its own bug
-----------------------
1. **``urlizetrunc``'s ellipsis** is ``Urlizer.trim_url``, not ``Truncator``::

       def trim_url(self, x, *, limit):
           if limit is None or len(x) <= limit:
               return x
           return "%s…" % x[: max(0, limit - 1)]

   djust appended THREE ASCII dots and reserved THREE characters for them, so
   the divergence compounds: a wrong character *and* a wrong budget. Same
   ellipsis fix that landed for ``truncatechars`` in #2203, which never reached
   ``urlize`` — parallel-path drift on a CONSTANT (#1646). Every
   ``urlizetrunc`` cell in the differential's sweep differed for this reason
   alone.

2. **``divisibleby``'s zero divisor.** Django is ``int(value) % int(arg)``, and
   ``x % 0`` is a ``ZeroDivisionError``. djust guarded ``divisor != 0`` and
   answered ``False`` — a guard Django does not have. Reachable two ways, and
   the second only recently: ``:"0"`` always, and ``:False`` since #2328 made
   ``int(False)`` be ``0`` as Python has it.

3. **``floatformat``'s empty argument.** ``if isinstance(arg, str): last_char =
   arg[-1]`` is the FIRST statement in Django's ``floatformat``, ahead of the
   value parse, so ``""`` raises ``IndexError`` for every value — including one
   that would otherwise have taken a give-up path. Ugly, and it is the
   behaviour. The placement matters as much as the raise:
   :class:`TestTheEmptyArgumentIsAskedFirst` asserts the ORDER, because #2328's
   own ``None``-argument guard had to be moved for exactly the opposite reason.
"""

from __future__ import annotations

import datetime
import pathlib
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

FILTERS_RS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "crates"
    / "djust_templates"
    / "src"
    / "filters.rs"
)

URL_TEXT = "see http://example.com/aaaa now"


def django_render(source: str, value: Any) -> str:
    return DjangoTemplate(source).render(DjangoContext({"p": value}))


def djust_render(source: str, value: Any) -> str:
    return _rust.render_template(source, {"p": value})


def raises_django(source: str, value: Any) -> bool:
    try:
        django_render(source, value)
    except Exception:  # noqa: BLE001
        return True
    return False


def raises_djust(source: str, value: Any) -> bool:
    try:
        djust_render(source, value)
    except BaseException:  # noqa: BLE001 — a pyo3 panic is not an `Exception`
        return True
    return False


def assert_agrees(source: str, value: Any) -> None:
    """Both engines on one cell, measured rather than remembered.

    A cell where Django RAISES agrees when djust raises too: the two exception
    types can never match across the boundary (Django's `ZeroDivisionError` vs
    a `RuntimeError` wrapping a Rust error), so the raise BIT is the comparable
    property, exactly as the differential's argument axis has it.
    """
    if raises_django(source, value):
        assert raises_djust(source, value), f"{source} on {value!r}: Django raises, djust does not"
        return
    assert not raises_djust(source, value), f"{source} on {value!r}: djust raises, Django does not"
    assert djust_render(source, value) == django_render(source, value), f"{source} on {value!r}"


class TestUrlizetruncEllipsis:
    """One `…` counted as one character, not three dots counted as three."""

    @pytest.mark.parametrize("limit", ["0", "1", "2", "3", "5", "7", "15", "22", "23", "999"])
    def test_every_limit_agrees_with_django(self, limit: str) -> None:
        """A SWEEP over the boundary, not a sample. The old spelling was wrong
        in two ways at once — the character and the budget — so a single limit
        could agree by coincidence: at limit 3, `x[:0] + "…"` and
        `x[:0] + "..."` differ only in the tail, while at limit 4 they differ
        in the kept prefix too.
        """
        assert_agrees('{{ p|urlizetrunc:"%s" }}' % limit, URL_TEXT)

    @pytest.mark.parametrize(
        "text",
        [
            URL_TEXT,
            "x@example.com",
            "www.example.com/aaaaaa",
            "http://example.com",  # exactly at several limits
            "https://example.com/a/b/c?d=e#f",
            "visit http://a.io and http://bbbbbbbbbb.io twice",
            "no url here at all",
            "",
        ],
    )
    @pytest.mark.parametrize("limit", ["1", "4", "8", "18"])
    def test_it_agrees_across_url_shapes(self, text: str, limit: str) -> None:
        assert_agrees('{{ p|urlizetrunc:"%s" }}' % limit, text)

    def test_the_reported_cell(self) -> None:
        """The issue's own cell, asserted literally."""
        out = djust_render('{{ p|urlizetrunc:"5" }}', URL_TEXT)
        assert ">http…</a>" in out, out
        assert "..." not in out
        assert out == django_render('{{ p|urlizetrunc:"5" }}', URL_TEXT)

    def test_the_ellipsis_counts_toward_the_limit_as_ONE_character(self) -> None:
        """The budget half, which a character-only fix would leave wrong.

        Django keeps `limit - 1` characters and appends one `…`, so the
        displayed text is exactly `limit` characters long. djust kept
        `limit - 3` and appended three, which is also `limit` characters — the
        two agree on LENGTH and disagree on content, which is why a length
        assertion alone would pass over the old spelling.
        """
        for limit in (4, 6, 10, 12):
            out = djust_render('{{ p|urlizetrunc:"%d" }}' % limit, URL_TEXT)
            shown = out.split("</a>")[0].split(">")[-1]
            assert len(shown) == limit, (limit, shown)
            assert shown.endswith("…"), shown
            assert shown[:-1] == "http://example.com/aaaa"[: limit - 1], (limit, shown)

    def test_a_limit_that_does_not_truncate_appends_nothing(self) -> None:
        """The other side of `len(x) <= limit`, so the fix cannot become an
        unconditional append."""
        out = djust_render('{{ p|urlizetrunc:"999" }}', URL_TEXT)
        assert "…" not in out
        assert "http://example.com/aaaa</a>" in out
        assert out == django_render('{{ p|urlizetrunc:"999" }}', URL_TEXT)

    def test_the_ascii_spelling_is_gone_from_the_source(self) -> None:
        """The pin, because a literal `…` and a literal `...` are hard to tell
        apart in a diff — which is how the three-dot spelling survived #2203's
        fix to the neighbouring filter."""
        body = FILTERS_RS.read_text(encoding="utf-8")
        body = body.split("fn truncate_url_display(", 1)[1].split("\n}", 1)[0]
        assert '"..."' not in body and "{truncated}..." not in body, body
        assert "URL_TRUNCATE" in body

    def test_truncatechars_still_spells_it_the_same_way(self) -> None:
        """The neighbour whose fix this one was missing (#2203). Pinned so the
        two cannot drift apart again in the other direction."""
        assert djust_render('{{ p|truncatechars:"5" }}', "abcdefghij") == "abcd…"
        assert djust_render('{{ p|truncatechars:"5" }}', "abcdefghij") == django_render(
            '{{ p|truncatechars:"5" }}', "abcdefghij"
        )


class TestDivisiblebyZeroDivisor:
    """`int(value) % int(arg)` has no `arg != 0` guard, and neither has this."""

    @pytest.mark.parametrize("arg", ['"0"', "0", "False", '"-0"', "0.0", '"+0"', '" 0 "', "-0"])
    def test_a_zero_divisor_raises_on_both_engines(self, arg: str) -> None:
        source = "{{ p|divisibleby:%s }}" % arg
        assert raises_django(source, 10), f"Django changed for {arg}"
        with pytest.raises(RuntimeError, match="ZeroDivisionError"):
            djust_render(source, 10)

    def test_the_false_spelling_only_became_reachable_after_2328(self) -> None:
        """`int(False)` is `0` in Python, and #2328 is what made djust agree.
        Before it, `False` did not parse as a number at all, so this spelling
        could not reach the divisor-zero branch."""
        assert raises_django("{{ p|divisibleby:False }}", 10)
        assert raises_djust("{{ p|divisibleby:False }}", 10)
        # `True` is `1`, which is the control: it must NOT raise.
        assert djust_render("{{ p|divisibleby:True }}", 10) == "True"
        assert djust_render("{{ p|divisibleby:True }}", 10) == django_render(
            "{{ p|divisibleby:True }}", 10
        )

    @pytest.mark.parametrize("value", [10, "10", 0, "notanumber", None, [1], {"k": 1}, 1.5])
    def test_the_divisor_is_judged_before_the_value(self, value: Any) -> None:
        """Python raises on the OPERATOR, so a value djust cannot read still
        reaches the division. Keeping the value's fail-soft `False` for a
        divisor of zero would answer a question Django never gets to.
        """
        assert raises_django('{{ p|divisibleby:"0" }}', value), repr(value)
        assert raises_djust('{{ p|divisibleby:"0" }}', value), repr(value)

    def test_a_nonzero_divisor_keeps_the_values_fail_soft_answer(self) -> None:
        """The value axis is untouched: `int(value)` on something unparseable
        is a `ValueError` in Django and `False` here, which is pre-existing and
        a separate question (#2328 says so in as many words)."""
        assert djust_render('{{ p|divisibleby:"2" }}', "notanumber") == "False"
        assert raises_django('{{ p|divisibleby:"2" }}', "notanumber")

    @pytest.mark.parametrize(
        ("value", "arg"), [(10, '"2"'), (10, '"3"'), (0, '"5"'), (-9, '"3"'), ("42", '"7"')]
    )
    def test_ordinary_divisions_are_unchanged(self, value: Any, arg: str) -> None:
        """Non-vacuity for the guard: one that refused every divisor would pass
        every test above."""
        assert_agrees("{{ p|divisibleby:%s }}" % arg, value)

    def test_an_unparseable_divisor_still_takes_the_2328_chokepoint(self) -> None:
        """Two different raises, and they must stay distinguishable: `"0"`
        parses and divides by zero, `"x"` never parses at all."""
        with pytest.raises(RuntimeError, match="needs an integer argument"):
            djust_render('{{ p|divisibleby:"notanumber" }}', 10)
        with pytest.raises(RuntimeError, match="ZeroDivisionError"):
            djust_render('{{ p|divisibleby:"0" }}', 10)


class TestFloatformatEmptyArgument:
    """`arg[-1]` on `""` is an IndexError, and it happens first."""

    @pytest.mark.parametrize(
        "value",
        [3.14159, Decimal("1.55"), 0, -2.5, "abc", None, [1, 2], {"a": 1}, True],
    )
    def test_an_empty_argument_raises_for_every_value(self, value: Any) -> None:
        source = '{{ p|floatformat:"" }}'
        assert raises_django(source, value), f"Django changed for {value!r}"
        with pytest.raises(RuntimeError, match="IndexError"):
            djust_render(source, value)

    def test_a_resolved_empty_string_raises_too(self) -> None:
        """`isinstance(arg, str)` is true for a resolved context value as much
        as for a quoted literal, so the guard is NOT gated on quoting.
        """
        source = "{{ p|floatformat:q }}"
        ctx = {"p": 3.14159, "q": ""}
        assert DjangoTemplate  # keep the import honest for the local render
        try:
            DjangoTemplate(source).render(DjangoContext(ctx))
        except IndexError:
            pass
        else:
            pytest.fail("Django changed: a resolved empty argument no longer raises")
        with pytest.raises(RuntimeError, match="IndexError"):
            _rust.render_template(source, ctx)

    @pytest.mark.parametrize("arg", ['"2"', '"0"', '"-1"', '"g"', '"u"', '"2g"', "None", "3"])
    def test_every_other_argument_is_unchanged(self, arg: str) -> None:
        """Non-vacuity: a guard that raised for any argument would pass the
        cases above. `"g"` and `"u"` are the suffixes whose own `arg[:-1] or -1`
        produces an empty string INTERNALLY — and Django does not raise for
        them, so the guard must be on the argument as given, not on the
        remainder after the suffix is stripped."""
        assert_agrees("{{ p|floatformat:%s }}" % arg, 3.14159)


class TestTheEmptyArgumentIsAskedFirst:
    """The ORDER, which is the half a raise-only fix would get wrong.

    #2328's `floatformat` pass had to MOVE its `None`-argument guard down,
    because Django reaches `int(arg)` only once the value has parsed and an
    arm-level guard raised for 36 cells where Django returned `""`. The empty
    argument is the opposite: `arg[-1]` is the FIRST statement in the function,
    ahead of `Decimal(input_val)`. The two guards live on opposite sides of the
    value parse and both placements are load-bearing.
    """

    @pytest.mark.parametrize("value", ["abc", {"a": 1}, [1, 2], datetime.datetime(2020, 1, 1)])
    def test_an_unusable_value_still_raises_for_an_EMPTY_argument(self, value: Any) -> None:
        """Django never reaches its `return ""`, because `arg[-1]` ran first."""
        assert raises_django('{{ p|floatformat:"" }}', value), repr(value)
        with pytest.raises(RuntimeError, match="IndexError"):
            djust_render('{{ p|floatformat:"" }}', value)

    @pytest.mark.parametrize("value", ["abc", {"a": 1}, [1, 2], datetime.datetime(2020, 1, 1)])
    def test_but_a_NONE_argument_still_lets_the_value_decide(self, value: Any) -> None:
        """#2328's rule, which this must not disturb: `int(None)` is a
        TypeError, and Django never runs it for a value that did not parse."""
        assert djust_render("{{ p|floatformat:None }}", value) == "", repr(value)
        assert not raises_django("{{ p|floatformat:None }}", value), repr(value)

    def test_and_with_a_usable_value_the_None_argument_does_raise(self) -> None:
        """Non-vacuity for the pair above: if `None` never raised, the test
        before it would pass with #2328's guard removed entirely."""
        with pytest.raises(RuntimeError, match="TypeError"):
            djust_render("{{ p|floatformat:None }}", 1.5)
        assert raises_django("{{ p|floatformat:None }}", 1.5)

    def test_the_two_guards_are_on_opposite_sides_of_the_value_parse(self) -> None:
        """The structural pin. A future reader tidying these two together would
        reintroduce whichever bug the merge picked (#1646, in the direction
        where converging is WRONG)."""
        source = (
            FILTERS_RS.with_name("floatformat.rs")
            .read_text(encoding="utf-8")
            .split("pub fn floatformat(", 1)[1]
        )
        empty_at = source.index('arg == Some("")')
        value_at = source.index("let input_val: String = match value")
        none_at = source.index("arg_is_none_literal(arg, arg_was_quoted)")
        assert empty_at < value_at < none_at, (
            "the empty-argument guard must be ABOVE the value parse and the "
            "None-argument guard BELOW it — see #2346 and #2328 respectively"
        )
