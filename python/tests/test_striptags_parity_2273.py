"""`striptags` ported from Django rather than scanned for `<`...`>` (#2273).

The issue reports two divergences. Reproducing them found that the first is not
a `<`-handling bug that can be patched — it is the whole shape of the filter:

* **Everything after a lone `<` was deleted.** The old implementation set
  `in_tag = true` on every `<` and cleared it on every `>`, so `"a < b"`
  rendered as `"a "`. Django runs an `html.parser.HTMLParser`, which emits a
  `<` that is not followed by a letter / `/` / `!` / `?` as *data*. There is no
  patch to a `<`...`>` scanner that reaches this, because the scanner has no
  notion of what a tag start looks like.

* **A lone `>` was deleted too**, which the issue does not name: the scanner's
  `'>' => in_tag = false` consumed the character. `"a > b"` rendered `"a  b"`.

* **A named reference lost its `;`.** `MLStripper` runs with
  `convert_charrefs=False` and re-emits `handle_entityref(name)` as
  `"&%s;" % name`, so `"&one two<b>x</b>"` is `"&one; twox"`.

Django's `strip_tags` is also a **loop**: it re-runs `_strip_once` until the
`<` count stops falling, which is what turns `"<<b>script>"` into `""` rather
than into the live `<script>` tag one pass leaves behind. Both halves are
required; the parity table below has a gate-off note naming which.

The port reuses the `html.parser.HTMLParser` tokenizer that #2262 added for the
HTML truncators, lifted behind a `Sink` trait in
`crates/djust_templates/src/htmlparser.rs` — one state machine, two handler
sets, rather than the parallel-path drift a second tokenizer would be (#1646).

Two defects in the port survived a curated table and were found only by the
randomized differential in `TestRandomizedDifferential`, which is why it is
here (v1.1.1-2 retro):

1. `feed()` + `close()` is genuinely **two** `goahead` passes, not a shorthand
   for one `goahead(1)`. The `&#`-bail arm is the only `break` that advances
   before stopping, so the following pass resumes the *loop* rather than
   re-breaking in place — and a second bail inside pass 2 has no pass 3.
2. `entityref`'s name class `[-.a-zA-Z0-9]` overlaps its own trailing
   `[^a-zA-Z0-9]` on `-` and `.`, so `re`'s **backtracking** is load-bearing:
   `&amp-` is the entity `amp` with `-` as the trail.

What is deliberately still divergent — both of Django's `raise` paths, which a
template filter has no channel for — is enumerated with its measurement in
`TestKnownRemainingDivergences`.
"""

from __future__ import annotations

import random
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


# ---------------------------------------------------------------------------
# The cells the issue reports, verbatim.
# ---------------------------------------------------------------------------


class TestReportedCells:
    """Every cell quoted in #2273, with Django's answer written out.

    The expected values are asserted against a live Django render as well, so a
    Django upgrade that moved one would fail here rather than silently
    re-point the contract.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # -- 1. everything after a lone `<` was deleted --------------------
            ("a < b", "a &lt; b"),
            ("price < 5 and > 3", "price &lt; 5 and &gt; 3"),
            ("x<y<z", "x&lt;y&lt;z"),
            ("<bx&y3.5word", "&lt;bx&amp;y3.5word"),
            # -- 2. a named reference lost its `;` -----------------------------
            # The tag is load-bearing: without one, both sides already agreed,
            # so a sweep over plain strings alone would not find this.
            ("&one two<b>x</b>", "&amp;one; twox"),
            ("<i>&a b</i>", "&amp;a; b"),
            ("&one two", "&amp;one two"),
        ],
    )
    def test_reported_cell(self, value: str, expected: str) -> None:
        source = "{{ p|striptags }}"
        django_out, djust_out = render_both(source, value)
        assert django_out == expected, (
            f"Django's answer for {value!r} changed: {django_out!r}"
        )
        assert djust_out == expected


# ---------------------------------------------------------------------------
# Divergences the issue does NOT name, found by reproducing the ones it does.
# ---------------------------------------------------------------------------


class TestUnreportedDivergences:
    """Each of these was broken on `main` and is not mentioned in #2273."""

    @pytest.mark.parametrize(
        "value",
        [
            # A lone `>` was DELETED by the scanner's `'>' => in_tag = false`.
            # GATE-OFF: only the tokenizer covers this; the wrapper loop never
            # runs (no `<`), so the value is returned by the fast path.
            "a > b",
            ">",
            "5 > 3",
            # Both delimiters, so the parse really runs and `< 1` is data.
            "5 < 10 and 10 > 5",
            "<1> <-> < >",
            # ONE pass leaves a live `<script>` behind; the loop is what
            # removes it. GATE-OFF for the wrapper loop specifically.
            "<<b>script>",
            "<<i>b>x</<i>b>",
            "<<<<<<<<<<b>b>b>b>b>b>b>b>b>b>",
            # `feed()` + `close()` is two passes: the second RESUMES the loop
            # after a bailed-out `&#`, so the tag that follows is stripped...
            "&#;<b>x</b>",
            "&#;<p><br />&#65;=&&",
            "&#x</ b>&z;",
            # ...but a second bail happens during pass 2, and there is no pass
            # 3, so what follows THAT one leaves as data.
            "&#xZZ</b>&#;<br />a <b",
            "&#&#;<!DOCTYPE html><p>& ",
            # `entityref` backtracking: `-` and `.` are in both the name class
            # and the trailing class.
            "<b/>&amp-",
            "<b/>x&one3.5",
            "<b/>&nbsp-x",
            "<i>&amp--</i>",
            # `close()` flushes an incomplete tail as data (the truncators,
            # which only `feed()`, discard it instead).
            "<b>x</b> &am",
            "<b>x</b> <c",
            "<b>x</b><!-- open",
            # CDATA: `<script>` content is not markup and survives.
            "<script>a<b</script>",
            "<style>x{}</style>keep",
            # Comments, doctypes and PIs contribute nothing.
            "<!-- c -->keep",
            "<!DOCTYPE html><h1>T</h1>",
            "<?php echo 1 ?>keep",
            # A start tag whose attribute parse fails is emitted as DATA.
            '<b x=">keep',
            # `</>` is consumed silently.
            "a</>b",
        ],
    )
    def test_divergence(self, value: str) -> None:
        assert_agrees("{{ p|striptags }}", value)


# ---------------------------------------------------------------------------
# The chain axis.
# ---------------------------------------------------------------------------

CHAINS = [
    "{{ p|striptags }}",
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

    `{{ p|escape|striptags }}` and `{{ p|safe|striptags }}` are deliberately
    absent: both diverge for reasons that have nothing to do with `striptags`
    (measured in `TestKnownRemainingDivergences`).
    """

    @pytest.mark.parametrize("source", CHAINS)
    @pytest.mark.parametrize(
        "value",
        [
            "a < b",
            "a > b",
            "5 < 10 and 10 > 5",
            "<<b>script>",
            "&one two<b>x</b>",
            "a<b>c</b>d",
            "<p>Hello <em>world</em></p>",
            "&#;<b>x</b>",
            "<b/>&amp-",
        ],
    )
    def test_chain(self, source: str, value: str) -> None:
        assert_agrees(source, value)


# ---------------------------------------------------------------------------
# The randomized differential.
# ---------------------------------------------------------------------------

# Fragments chosen to reach every branch of `HTMLParser.goahead`: the `<`
# dispatch table, the `&` / `&#` charref arms, CDATA mode, the `k < 0`
# incomplete-construct recovery, and the end-of-input tail flush.
FRAGMENTS = [
    # bare delimiters -- the issue's case and its neighbours
    "<", ">", "<>", "< >", "</>", "<<", ">>", "<<>>", "a < b", "a > b",
    "5 < 10 and 10 > 5", "x<y<z", "<b", "a <b", "<0>", "< b>", "<-", "<=",
    # well-formed markup
    "<b>", "</b>", "<i>", "</i>", "<br/>", "<br />", "<br>", "<p>", "</p>",
    "<div class='x'>", "</div>", '<a href="x">', "<img src=x>", "<span>",
    # malformed / tolerant-parse territory
    "<<b>script>", "<b ", "<b x", "<b x=", '<b x=">', "<b x=y", "<b/",
    "<b\n>", "<b\t>", "</ b>", "</b", "<b//>", '<a href="a>b">', "<b ==x>",
    # CDATA elements
    "<script>", "</script>", "<style>", "</style>", "<script>a<b</script>",
    "<style>x{}</style>", "</SCRIPT>",
    # comments, PIs, declarations
    "<!--", "-->", "<!-- c -->", "<!--->", "<!-->", "<!--a--!>",
    "<?", "<?php ?>", "<?pi>", "<!", "<!x>", "<!DOCTYPE html>", "<!doctype>",
    "]]>",
    # entities -- the `convert_charrefs=False` arms
    "&", "&&", "&amp;", "&amp", "&lt;", "&gt;", "&nbsp;", "&one", "&one;",
    "&a b", "&a", "&#", "&#65;", "&#65", "&#x41;", "&#x41", "&#xZZ", "&#;",
    "&#x", "&z;", "&123;", "& ", "&;", "&amp-", "&one3.5",
    # plain prose
    "a", "b", "x", "word", "5", "10", " ", "\n", "\t", "hello world", "3.5",
    "price", "and", "-", "=", "/", "'", '"', "\\", "é", "中",
]

# `<![name[` sections are excluded from the generated corpus: Django raises
# AssertionError on an unknown section keyword, so there is no reference
# answer to compare against (measured in `TestKnownRemainingDivergences`).


def _corpus(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    out: list[str] = []
    seen: set[str] = set()
    while len(out) < n:
        v = "".join(rng.choice(FRAGMENTS) for _ in range(rng.randint(1, 6)))
        if v in seen or "<![" in v:
            continue
        seen.add(v)
        out.append(v)
    return out


class TestRandomizedDifferential:
    """A curated table samples the axis you noticed; this samples the rest.

    Both port defects listed in this module's docstring survived the tables
    above and were found here.
    """

    def test_sweep_agrees_with_django(self) -> None:
        values = _corpus(3000, seed=22730)
        mismatches = []
        for value in values:
            for source in CHAINS:
                django_out, djust_out = render_both(source, value)
                if django_out != djust_out:
                    mismatches.append((source, value, django_out, djust_out))
        assert not mismatches, (
            f"{len(mismatches)} of {len(values) * len(CHAINS)} cells diverge; "
            f"first 5: {mismatches[:5]}"
        )

    def test_sweep_is_not_vacuous(self) -> None:
        """The corpus must actually reach the branches it claims to.

        Without this, a generator that produced only inert strings would make
        the sweep above pass for the wrong reason.
        """
        values = _corpus(3000, seed=22730)
        joined = "".join(values)
        assert sum("<" in v and ">" not in v for v in values) > 50, "no lone-`<` values"
        assert sum(">" in v and "<" not in v for v in values) > 15, "no lone-`>` values"
        assert "&#" in joined and "&amp" in joined, "no charref values"
        assert sum("<script" in v for v in values) > 20, "no CDATA values"
        assert sum("<!--" in v for v in values) > 20, "no comment values"
        # And the filter must be doing real work on them: at least a third of
        # the corpus must come back CHANGED.
        changed = sum(
            1
            for v in values
            if render_both("{{ p|striptags|safe }}", v)[1] != v
        )
        assert changed > len(values) // 3, f"only {changed} values were altered"


# ---------------------------------------------------------------------------
# What stays divergent, with its measurement.
# ---------------------------------------------------------------------------


class TestKnownRemainingDivergences:
    """Both of Django's `raise` paths, which a template filter cannot take.

    Django's `strip_tags` raises `SuspiciousOperation` on a strip-tags DoS, and
    CPython's `parse_marked_section` raises `AssertionError` on an unknown
    `<![name[` keyword. Neither is expressible from a djust filter: the Rust
    filter layer returns a `String`, `SuspiciousOperation` is a Django
    *request* concept, and raising would 500 the whole render rather than
    refuse one value. Both fail soft, and both are pinned here so the choice
    is visible rather than discovered.
    """

    @pytest.mark.parametrize(
        "value",
        [
            # `<[a-zA-Z][^>]{1000,}` carrying >= 50 `<` -- the match's own
            # leading `<` counts, so 49 inner ones are enough.
            "<a" + "<" * 49 + "y" * 1001,
            # Still shedding tags after 50 passes -- Django allows exactly
            # 50 and raises on the 51st. `keepA`/`keepB` make the UNCAPPED
            # fixpoint non-empty, so the empty string below is evidence of the
            # cap rather than of stripping (see the Rust sibling test).
            "keepA" + "<" * 51 + "b>" * 51 + "keepB",
        ],
    )
    def test_dos_guard_renders_empty_where_django_raises(self, value: str) -> None:
        with pytest.raises(SuspiciousOperation):
            DjangoTemplate("{{ p|striptags }}").render(DjangoContext({"p": value}))
        # Empty is the only refusal value that is safe in every context this
        # output reaches: returning the input, or the partially-stripped value,
        # would emit attacker-controlled markup that Django declined to emit --
        # under `{{ v|striptags|safe }}` that is an XSS surface Django has not
        # got.
        assert _rust.render_template("{{ p|striptags }}", {"p": value}) == ""
        assert _rust.render_template("{{ p|striptags|safe }}", {"p": value}) == ""

    def test_dos_guard_boundaries_are_exact_on_both_sides(self) -> None:
        """One step under each bar is parsed normally, on BOTH sides."""
        # Pre-scan: 48 inner `<` plus the match's own is 49, under the 50 bar.
        assert_agrees("{{ p|striptags }}", "<a" + "<" * 48 + "y" * 1001)
        # Loop cap: 50 passes is the last allowed depth, and Django agrees the
        # value survives intact rather than being refused.
        fifty = "keepA" + "<" * 50 + "b>" * 50 + "keepB"
        assert_agrees("{{ p|striptags }}", fifty)
        assert (
            DjangoTemplate("{{ p|striptags }}").render(DjangoContext({"p": fifty}))
            == "keepAkeepB"
        )

    @pytest.mark.parametrize(
        "value",
        ["<![foo[x]]>", "x&&&one;<![</p>", "<!--a--!><![</p>"],
    )
    def test_unknown_marked_section_fails_soft(self, value: str) -> None:
        with pytest.raises(AssertionError):
            DjangoTemplate("{{ p|striptags }}").render(DjangoContext({"p": value}))
        # No exception, and no crash: refusing to parse the section is the
        # fail-soft equivalent of Django's assert.
        _rust.render_template("{{ p|striptags }}", {"p": value})

    @pytest.mark.parametrize(
        ("source", "value"),
        [
            # `|escape|striptags`: djust strips the UNESCAPED value, so the
            # tags Django's `escape` had already neutralised are removed.
            ("{{ p|escape|striptags }}", "a<b>c</b>d"),
            # `|safe|striptags`: djust does not carry the safe flag through
            # `striptags`, so the output is escaped where Django's is not.
            ("{{ p|safe|striptags }}", "a < b"),
            # `|striptags|length`: djust's `length` counts BYTES.
            ("{{ p|striptags|length }}", "中<b"),
        ],
    )
    def test_chain_divergence_is_not_striptags(self, source: str, value: str) -> None:
        """Three chain divergences whose cause is the OTHER filter.

        Each is pinned with a proof that `striptags` itself is exact on the
        value, so a future reader does not re-diagnose them as a `striptags`
        bug. Tracked separately.
        """
        django_out, djust_out = render_both(source, value)
        assert django_out != djust_out, (
            f"{source} on {value!r} now AGREES -- delete this row and the "
            f"follow-up issue it documents"
        )
        # `striptags` alone is byte-exact on the same value.
        assert_agrees("{{ p|striptags|safe }}", value)
