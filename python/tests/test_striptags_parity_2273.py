"""`striptags` renders exactly what Django's `strip_tags` returns (#2273, 2.0).

`striptags` was a Rust port of `django.utils.html.strip_tags` over a
transcription of CPython's `html.parser`, pinned to one parser behaviour. In
2.0 it runs `django.template.defaultfilters.striptags` itself, so its output
follows the running interpreter's `html.parser`, as Django's does, and the
pinned-reference fixture, the per-CPython figures and the depth-guard
divergence are gone with the port.

What stays is the live differential: the cells #2273 reported, the ones it did
not, and the chains that consume the filter's output, each compared against a
real Django render. Every value here is version-stable, so the answer is the
same on every interpreter in the matrix.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("django")

from django.core.exceptions import SuspiciousOperation  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402


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


REPORTED_CELLS = [
    # -- 1. everything after a lone `<` was deleted ---------------------------
    ("a < b", "a &lt; b"),
    ("price < 5 and > 3", "price &lt; 5 and &gt; 3"),
    ("x<y<z", "x&lt;y&lt;z"),
    ("<bx&y3.5word", "&lt;bx&amp;y3.5word"),
    # -- 2. a named reference lost its `;` ------------------------------------
    # The tag is load-bearing: without one, both sides already agreed, so a
    # sweep over plain strings alone would not find this.
    ("&one two<b>x</b>", "&amp;one; twox"),
    ("<i>&a b</i>", "&amp;a; b"),
    ("&one two", "&amp;one two"),
]

UNREPORTED_VALUES = [
    # A lone `>` was DELETED by the scanner's `'>' => in_tag = false`.
    "a > b",
    ">",
    "5 > 3",
    # Both delimiters, so the parse really runs and `< 1` is data.
    "5 < 10 and 10 > 5",
    "<1> <-> < >",
    # ONE pass leaves a live `<script>` behind; the loop removes it.
    "<<b>script>",
    "<<i>b>x</<i>b>",
    "<<<<<<<<<<b>b>b>b>b>b>b>b>b>b>",
    # `feed()` + `close()` is two passes: the second RESUMES the loop after a
    # bailed-out `&#`, so the tag that follows is stripped.
    "&#;<b>x</b>",
    "&#;<p><br />&#65;=&&",
    "&#x</ b>&z;",
    # `entityref` backtracking: `-` and `.` are in both the name class and the
    # trailing class.
    "<b/>&amp-",
    "<b/>x&one3.5",
    "<b/>&nbsp-x",
    "<i>&amp--</i>",
    "<b/>&9a<i>x</i>",
    # A bare `<` at the very end of the input is data.
    "<b>x</b><",
    "<b>x</b></",
    # CDATA: `<script>` content is not markup and survives.
    "<script>a<b</script>",
    "<style>x{}</style>keep",
    # Comments, doctypes and PIs contribute nothing.
    "<!-- c -->keep",
    "<!DOCTYPE html><h1>T</h1>",
    "<?php echo 1 ?>keep",
    # `</>` is consumed silently.
    "a</>b",
]

CHAIN_VALUES = [
    "a < b",
    "a > b",
    "5 < 10 and 10 > 5",
    "<<b>script>",
    "&one two<b>x</b>",
    "a<b>c</b>d",
    "<p>Hello <em>world</em></p>",
    "&#;<b>x</b>",
    "<b/>&amp-",
]


# ---------------------------------------------------------------------------
# The cells the issue reports, verbatim.
# ---------------------------------------------------------------------------


class TestReportedCells:
    """Every cell quoted in #2273, with Django's answer written out.

    These agree on every CPython in the matrix, so they are asserted against a
    live Django render as well — a Django upgrade that moved one fails here
    rather than silently re-pointing the contract.
    """

    @pytest.mark.parametrize(("value", "expected"), REPORTED_CELLS)
    def test_reported_cell(self, value: str, expected: str) -> None:
        source = "{{ p|striptags }}"
        django_out, djust_out = render_both(source, value)
        assert django_out == expected, f"Django's answer for {value!r} changed: {django_out!r}"
        assert djust_out == expected


# ---------------------------------------------------------------------------
# Divergences the issue does NOT name, found by reproducing the ones it does.
# ---------------------------------------------------------------------------


class TestUnreportedDivergences:
    """Broken on `main`, not mentioned in #2273, and stable across the matrix.

    Anything whose answer moved between CPython versions is deliberately NOT
    here: a live compare of it would only pass on some interpreters.
    """

    @pytest.mark.parametrize("value", UNREPORTED_VALUES)
    def test_divergence(self, value: str) -> None:
        assert_agrees("{{ p|striptags }}", value)


# ---------------------------------------------------------------------------
# The chain axis.
# ---------------------------------------------------------------------------

CHAINS = [
    "{{ p|striptags }}",
    # The input-safety axis: a SafeData value reaches Django's filter marked
    # safe, and an escaped one reaches it already escaped (#2280, #2281).
    "{{ p|safe|striptags }}",
    "{{ p|escape|striptags }}",
    "{{ p|force_escape|striptags }}",
    "{{ p|striptags|upper }}",
    "{{ p|striptags|lower }}",
    "{{ p|striptags|escape }}",
    "{{ p|striptags|safe }}",
    "{{ p|striptags|truncatechars:5 }}",
]


class TestChains:
    """`striptags` composed with the filters that consume its output.

    #2250 caught a 1,195-cell regression in its own candidate fix only because
    it tested chains, and #2272 found 243 cells where two bugs were cancelling.

    `striptags` returns a plain string, so the output-escape axis
    (`|striptags|safe`, `|striptags|escape`) is where an HTML-producing filter
    run through Django could double-escape or leak, and is pinned here.
    """

    @pytest.mark.parametrize("source", CHAINS)
    @pytest.mark.parametrize("value", CHAIN_VALUES)
    def test_chain(self, source: str, value: str) -> None:
        assert_agrees(source, value)


# ---------------------------------------------------------------------------
# What the port pinned differently, now Django's answer on every interpreter.
# ---------------------------------------------------------------------------

# The values the templates guide used to list as differing from Django on
# CPython 3.13.10+ / 3.14.1+ (unterminated references) and 3.13.15+ / 3.14.7+
# (abruptly closed empty comments). The filter is Django's now, so both sides
# give whatever the running `html.parser` gives.
FORMERLY_PINNED_VALUES = [
    "<i>&a",
    "<b>&#65",
    "&#x<br>",
    "&#xZZ<i>",
    "<!--->-->",
    "<!--->a<!--->",
    "<!---><?pi>&#-->",
    "<!--><br/><0>--><br/>",
    "price<!--->< b><i><!--a--!>",
    "<script><!--->&#65<<>><!-- c --></>",
    "<!--->< b><b x=\t<!DOCTYPE html><!--->",
    "<script>a<b</script><<b>script><!--->&a b<!-- c --><!--->",
]


class TestFormerlyPinnedValues:
    @pytest.mark.parametrize("value", FORMERLY_PINNED_VALUES)
    def test_agrees_with_this_interpreters_django(self, value: str) -> None:
        assert_agrees("{{ p|striptags }}", value)


class TestDepthGuard:
    """Django raises `SuspiciousOperation` past its stripping-depth guard. The
    port rendered `""` there; the routed filter raises Django's exception."""

    @pytest.mark.parametrize(
        "value",
        [
            # `<[a-zA-Z][^>]{1000,}` carrying >= 50 `<` -- the match's own
            # leading `<` counts, so 49 inner ones are enough.
            "<a" + "<" * 49 + "y" * 1001,
            # Still shedding tags after 50 passes: Django allows exactly 50.
            "keepA" + "<" * 51 + "b>" * 51 + "keepB",
        ],
    )
    def test_raises_what_django_raises(self, value: str) -> None:
        with pytest.raises(SuspiciousOperation):
            DjangoTemplate("{{ p|striptags }}").render(DjangoContext({"p": value}))
        with pytest.raises(SuspiciousOperation):
            _rust.render_template("{{ p|striptags }}", {"p": value})

    def test_one_step_under_each_bar_renders(self) -> None:
        assert_agrees("{{ p|striptags }}", "<a" + "<" * 48 + "y" * 1001)
        fifty = "keepA" + "<" * 50 + "b>" * 50 + "keepB"
        assert_agrees("{{ p|striptags }}", fifty)
        assert _rust.render_template("{{ p|striptags }}", {"p": fifty}) == "keepAkeepB"


def test_the_escape_chain_agrees() -> None:
    """#2281: `escape` is eager, so `striptags` finds no tags to strip."""
    assert_agrees("{{ p|escape|striptags }}", "a<b>c</b>d")
    assert_agrees("{{ p|striptags|safe }}", "a<b>c</b>d")
    django_out, _ = render_both("{{ p|escape|striptags }}", "a<b>c</b>d")
    assert django_out == "a&lt;b&gt;c&lt;/b&gt;d", django_out
