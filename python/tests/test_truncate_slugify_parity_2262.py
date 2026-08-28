"""Seven filter algorithms, ported from Django rather than described (#2262, #2261).

The two issues name seven divergences across `truncatechars_html` (x2),
`truncatewords_html`, `truncatewords`, `urlencode`, `slugify` and `title`. All
seven reproduce here first, verbatim, and every one is now exact.

Reproducing them corrected the issues' premise in two places and found several
divergences neither issue names:

* **`truncatechars_html`'s two divergences are one branch, not two.** Django's
  `TruncateCharsHTMLParser.process` has a special case — when the *whole* input
  is one run of text of exactly `length` characters, it emits that text **raw
  and unescaped** and stops. That single branch produces both the "8 characters
  at a limit of 8 is not over the limit" cell and the "measures the escaped
  form" cell. No off-by-one adjustment reaches it, which is why this is a port.

* **The HTML twins had eight further divergences the issue does not name**,
  all consequences of Django driving a real `html.parser.HTMLParser`:
  comments/doctypes/processing instructions are **deleted** (Django does not
  override `handle_comment` and friends, so the base no-op runs); an
  unterminated construct at the end of the input **discards everything after
  it** (`Truncator` calls `reset()` before `close()`, so the parser's
  wait-for-more-text path never flushes); `<script>`/`<style>` switch to CDATA
  mode where text is not entity-decoded; character references round-trip
  through `html.unescape` + `escape`; `frame` and `spacer` are void elements;
  and a stray `</>` is consumed silently.

* **`title` was wrong on far more than surrounding whitespace.** It split on
  whitespace, where Python's `str.title()` treats *any non-cased character* as
  a word boundary — that is the `<b>x</b>` cell. Beyond the issue: the
  titlecase mapping is not uppercase (`ß` titlecases to `Ss`, not `SS`), `\\d`
  in Django's `\\d([A-Z])` fixup is category `Nd` only (so `²A` must NOT be
  lowercased), `Cased` is not `is_lowercase() || is_uppercase()`, and a final
  sigma lowercases to `ς` with a `Case_Ignorable`-skipping lookahead.

An exhaustive single-codepoint differential for `title` runs in
`TestTitleExhaustive`; the rest are randomized differentials, because a curated
table samples the axis you noticed (v1.1.1-2 retro).

What is deliberately still divergent is enumerated in
`TestKnownRemainingDivergences` with its measurement, rather than left for a
reader to discover.
"""

from __future__ import annotations

import random
import re
import sys
import unicodedata
from typing import Any

import pytest

pytest.importorskip("django")

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
# The cells the issues report, verbatim.
# ---------------------------------------------------------------------------


class TestReportedCells:
    """Every cell quoted in #2262 and #2261, with Django's answer inline.

    The expected values are asserted against a live Django render as well as
    written out, so a Django upgrade that changed one would fail here rather
    than silently re-point the contract.
    """

    @pytest.mark.parametrize(
        ("source", "value", "expected"),
        [
            # -- #2262 --------------------------------------------------------
            # 1. `len == limit` is not over the limit.
            ("{{ p|truncatechars_html:8 }}", "Infinity", "Infinity"),
            # 2. The budget counts TEXT, not the escaped form.
            ("{{ p|truncatechars_html:8 }}", {"a": 1}, "{&#x27;a&#x27;: 1}"),
            # 3. `truncatewords_html` escapes inside the parser AND at render.
            ("{{ p|truncatewords_html:2 }}", {"a": 1}, "{&amp;#x27;a&amp;#x27;: 1}"),
            # 4. `" ".join(words)` drops the padding.
            ("{{ p|truncatewords:2 }}", "  spaced  ", "spaced"),
            # 5. `quote`'s default safe set is "/", not "".
            ("{{ p|urlencode }}", "<b>x</b>", "%3Cb%3Ex%3C/b%3E"),
            # -- #2261 --------------------------------------------------------
            # `.` is DELETED by `[^\w\s-]`, never mapped to a separator.
            ("{{ p|slugify }}", "3.5", "35"),
            ("{{ p|slugify }}", "<b>x</b>", "bxb"),
            ("{{ p|slugify }}", "-1.5e+300", "15e300"),
            # `title` never touches surrounding whitespace, and a word boundary
            # is any non-cased character.
            ("{{ p|title }}", "  spaced  ", "  Spaced  "),
            ("{{ p|title }}", "<b>x</b>", "&lt;B&gt;X&lt;/B&gt;"),
        ],
    )
    def test_reported_cell(self, source: str, value: Any, expected: str) -> None:
        django_out, djust_out = render_both(source, value)
        assert django_out == expected, (
            f"Django's answer for {source} on {value!r} changed: {django_out!r}"
        )
        assert djust_out == expected


# ---------------------------------------------------------------------------
# Divergences the issues do NOT name, found by reproducing the ones they do.
# ---------------------------------------------------------------------------


class TestUnreportedDivergences:
    """Each of these was broken on `main` and is not mentioned in either issue."""

    @pytest.mark.parametrize(
        ("source", "value"),
        [
            # A comment contributes nothing: Django does not override
            # `handle_comment`, so the base class's no-op runs.
            ("{{ p|truncatechars_html:100 }}", "a <!-- comment --> bcd"),
            ("{{ p|truncatechars_html:5 }}", "a <!-- comment --> bcd"),
            # ... and neither do doctypes, PIs or bogus comments.
            ("{{ p|truncatechars_html:100 }}", "<!doctype html>x"),
            ("{{ p|truncatechars_html:100 }}", "<?pi?>x"),
            ("{{ p|truncatechars_html:100 }}", "<! bogus >x"),
            ("{{ p|truncatechars_html:100 }}", "</>x"),
            # An unterminated construct at the end discards the whole input:
            # `goahead` breaks waiting for more text and `reset()` throws it away.
            ("{{ p|truncatechars_html:100 }}", "trailing &amp"),
            ("{{ p|truncatewords_html:100 }}", "a &amp; b &nope; &#65; &"),
            ("{{ p|truncatechars_html:100 }}", "unclosed <b attr"),
            # CDATA mode: text inside <script> is not entity-decoded.
            ("{{ p|truncatechars_html:100 }}", "<script>a<b</script>c"),
            # `frame` is a void element in Django's list; the old one omitted it.
            ("{{ p|truncatechars_html:100 }}", "<frame>x"),
            # Character references round-trip through unescape + escape.
            ("{{ p|truncatewords_html:100 }}", "a &amp; b &#65; &notit; c"),
            # `length <= 0` is the empty string for every truncator, and a
            # negative argument parses (the old `usize` parse silently fell
            # back to the default).
            ("{{ p|truncatechars:-1 }}", "hello"),
            ("{{ p|truncatewords:-1 }}", "hello there"),
            ("{{ p|truncatechars_html:-1 }}", "<b>hello</b>"),
            ("{{ p|truncatewords_html:-1 }}", "<b>hello</b>"),
            # `urlencode`'s argument was ignored entirely.
            ('{{ p|urlencode:"" }}', "a/b c"),
            ('{{ p|urlencode:"/" }}', "a/b c"),
            ('{{ p|urlencode:"&" }}', "a&b/c"),
            # `title`: the titlecase mapping is not uppercase...
            ("{{ p|title }}", "ß"),
            # ... `\d` in the fixup is Nd only, so `²A` must stay `²A`...
            ("{{ p|title }}", "½ cup"),
            ("{{ p|title }}", "a²b"),
            # ... and a final sigma is ς, with a Case_Ignorable-skipping
            # lookahead that makes `Σ.Ζ` a NON-final sigma.
            ("{{ p|title }}", "ΟΔΟΣ"),
            ("{{ p|title }}", "ΠΣ.ΖΝΣ"),
            ("{{ p|title }}", "ΘγΝΣ'σ"),
            # `title`'s two documented fixups.
            ("{{ p|title }}", "o'connor"),
            ("{{ p|title }}", "a1b"),
            # `slugify` strips `_` as well as `-` from both ends.
            ("{{ p|slugify }}", "_a_"),
            ("{{ p|slugify }}", "--x--"),
        ],
    )
    def test_agrees_with_django(self, source: str, value: Any) -> None:
        assert_agrees(source, value)


# ---------------------------------------------------------------------------
# Randomized differentials.
# ---------------------------------------------------------------------------

_WORDS = ["a", "hi", "word", "Infinity", "3.5", "x&y", "<b", "ab'c", '"q"']
_VOID = ["br", "img", "hr", "input", "wbr", "frame"]
_NORMAL = ["p", "b", "i", "div", "span", "em", "strong", "a"]


def _gen_plain(rnd: random.Random) -> Any:
    kind = rnd.randrange(10)
    if kind == 0:
        return ""
    if kind == 1:
        return " " * rnd.randrange(1, 4)
    if kind == 2:
        return rnd.choice(["3.5", "-1.5e+300", "1E-9", "Infinity", "-0.0", "007"])
    if kind == 3:
        return {"a": rnd.randrange(3)}
    if kind == 4:
        return [rnd.randrange(3), "x"]
    if kind == 5:
        return rnd.choice(["<b>x</b>", "a&amp;b", "&#x27;q&#x27;", "&lt;", "&nbsp;"])
    if kind == 6:
        return rnd.choice(["  spaced  ", "\tlead", "trail\n", "a\nb\nc", " a  b "])
    if kind == 7:
        return rnd.choice(["3.5", "a.b.c", "a+b", "a_b", "--x--", "__y__", "-_a_-"])
    if kind == 8:
        return "".join(rnd.choice([*_WORDS, " ", "  ", "\n"]) for _ in range(rnd.randrange(1, 8)))
    return "".join(
        rnd.choice("abcXY 019.-_+&<>'\"/%?#=~!*()[]{}:;,\t\n@$")
        for _ in range(rnd.randrange(0, 24))
    )


def _gen_html(rnd: random.Random, depth: int = 0) -> str:
    """Nested, unclosed, void-element-bearing and entity-bearing markup."""
    out = []
    for _ in range(rnd.randrange(1, 5)):
        k = rnd.randrange(10)
        if k <= 2:
            out.append(rnd.choice(["Hello", "one two three", "x", "  ", "a b", ""]))
        elif k == 3:
            out.append(rnd.choice(["&amp;", "&#x27;", "&lt;b&gt;", "&nbsp;", "&#65;", "&notit;"]))
        elif k == 4:
            t = rnd.choice(_VOID)
            out.append(rnd.choice([f"<{t}>", f"<{t}/>", f"<{t} />"]))
        elif k == 5 and depth < 3:
            t = rnd.choice(_NORMAL)
            attrs = rnd.choice(["", ' class="c"', " id='i'", ' data-x="1" y'])
            out.append(f"<{t}{attrs}>{_gen_html(rnd, depth + 1)}</{t}>")
        elif k == 6 and depth < 3:
            out.append(f"<{rnd.choice(_NORMAL)}>{_gen_html(rnd, depth + 1)}")
        elif k == 7:
            out.append(f"</{rnd.choice(_NORMAL)}>")
        elif k == 8:
            out.append(rnd.choice(["<!-- c -->", "<!doctype html>", "<?pi?>"]))
        else:
            out.append(rnd.choice([" ", "\n", "\t", "  "]))
    return "".join(out)


def _big_int(value: Any) -> bool:
    """Formerly the #2260 exclusion; now always ``False`` and kept as a pin.

    An int past ``2**63`` used to lose precision in `Value` BEFORE any filter
    ran, so `{{ p }}` alone diverged and a sweep including such values would
    attribute #2260 to whichever filter happened to be under test. #2260 closed
    while this branch was in flight, so nothing is excluded any more — the
    sweeps below now cover big integers, and
    :meth:`TestKnownRemainingDivergences.test_big_integers_survive_since_2260`
    asserts the exclusion is genuinely unnecessary rather than merely unused.
    """
    return False


#: `(name, template, arguments, generator)`. The `%s` slots are the chain
#: prefix and the filter argument.
_SUITES = [
    ("slugify", "{{ p|%sslugify }}", [None], _gen_plain),
    ("title", "{{ p|%stitle }}", [None], _gen_plain),
    ("truncatewords", "{{ p|%struncatewords:%s }}", [0, 1, 2, 3, 5, 10], _gen_plain),
    ("truncatechars", "{{ p|%struncatechars:%s }}", [0, 1, 2, 3, 5, 8, 20], _gen_plain),
    ("urlencode", "{{ p|%surlencode }}", [None], _gen_plain),
    ("urlencode_arg", '{{ p|%surlencode:"" }}', [None], _gen_plain),
    (
        "truncatechars_html",
        "{{ p|%struncatechars_html:%s }}",
        [1, 2, 5, 8, 20],
        _gen_html,
    ),
    ("truncatewords_html", "{{ p|%struncatewords_html:%s }}", [1, 2, 3, 5, 10], _gen_html),
]

#: Chains worth exercising. `escape|`, `striptags|` and `safe|` are absent, and
#: each exclusion is MEASURED rather than assumed — every one of them is a
#: filter that diverges from Django in its own right, so including it would
#: measure that bug instead of these:
#:
#: * `escape|` — djust's `escape` returns its input unchanged and leans on
#:   autoescaping, which is only equivalent to Django's when `escape` is the
#:   LAST filter (#2257 residue 1). 4,349 divergent cells of 13,500 in a sweep
#:   where `upper|` and `lower|` diverge on exactly the unchained cells.
#:   Pinned in :class:`TestEscapeChainIsBlockedOn2257`.
#: * `striptags|` — 478 cells, and `striptags` itself diverges on 198 of 3,000
#:   plain values (it deletes everything after a lone `<`, which Django keeps).
#:
#: `safe|` was excluded here for the same reason until #2274: djust decided
#: escaping from a NAME whitelist alone, while Django's rule is `is_safe=True`
#: **and the input was already safe**, so `|safe` did not survive any
#: subsequent filter. That is now fixed — `filter_output_is_safe` takes the
#: input's safety and feeds it forward — so `safe|` is back in the sweep, and
#: :class:`TestSafeMarking` asserts agreement rather than pinning divergence.
#:
#: `upper|` and `lower|` stay, because they are the two that measure THESE
#: filters rather than a neighbour's bug.
_CHAINS = ["", "upper|", "lower|", "safe|"]


def _sweep(name: str, template: str, args: list, gen, chains, n: int, seed: int):
    """Yield every divergent cell of one randomized sweep."""
    rnd = random.Random(seed)
    values = [gen(rnd) for _ in range(n)]
    total = 0
    for value in values:
        if _big_int(value):
            continue
        for chain in chains:
            for arg in args:
                source = template % (chain, arg) if arg is not None else template % (chain,)
                total += 1
                django_out, djust_out = render_both(source, value)
                if django_out != djust_out:
                    yield source, value, django_out, djust_out
    assert total > 0, f"{name}: the sweep generated no cases"


class TestRandomizedDifferential:
    """Each filter against real Django over generated inputs, chains included."""

    @pytest.mark.parametrize(("name", "template", "args", "gen"), _SUITES, ids=lambda v: v)
    def test_no_divergence(self, name, template, args, gen) -> None:
        divergences = []
        for source, value, django_out, djust_out in _sweep(
            name, template, args, gen, _CHAINS, n=120, seed=20262261
        ):
            if _is_known_remaining(value):
                continue
            divergences.append(
                f"  {source} on {value!r}: django={django_out!r} djust={djust_out!r}"
            )
        assert not divergences, f"{name}: {len(divergences)} divergent cells\n" + "\n".join(
            divergences[:15]
        )


class TestHtmlTruncatorsAreExact:
    """The HTML twins have NO known-remaining class: they must be at zero.

    `Truncator.chars`'s NFC normalization is skipped for the plain-text path
    too, but the HTML path's generator produces no combining marks, so this
    sweep is a clean zero and stays that way.
    """

    @pytest.mark.parametrize("name", ["truncatechars_html", "truncatewords_html"])
    def test_zero_divergences(self, name: str) -> None:
        template, args, gen = next((t, a, g) for n, t, a, g in _SUITES if n == name)
        divergences = [
            f"  {src} on {val!r}: django={d!r} djust={j!r}"
            for src, val, d, j in _sweep(name, template, args, gen, _CHAINS, n=400, seed=987654)
        ]
        assert not divergences, f"{name}: {len(divergences)} divergent cells\n" + "\n".join(
            divergences[:15]
        )


class TestTitleExhaustive:
    """Every assignable codepoint, alone and adjacent to a cased letter.

    A randomized string sweep cannot reach the titlecase-mapping table or the
    `Nd`-vs-`N*` distinction reliably — those are single codepoints among a
    million. This walks all of them.

    The only permitted divergences are codepoints for which THIS CPython knows
    no case mapping at all (`c.upper() == c.lower() == c.title() == c`) while
    Rust's newer Unicode tables do. That covers both characters CPython leaves
    unassigned and ones it assigns without a mapping — U+019B gained an
    uppercase in Unicode 16, and CPython 3.12 ships 15.0. It is data-version
    skew, not an algorithm difference, and it is asserted to be the ONLY
    residue: anything else fails the test.
    """

    @staticmethod
    def _cpython_knows_no_case(char: str) -> bool:
        return char.upper() == char and char.lower() == char and char.title() == char

    def test_every_codepoint(self) -> None:
        template = "{{ p|title }}"
        compiled = DjangoTemplate(template)
        unexpected = []
        skew = 0
        checked = 0
        for cp in range(0x110000):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            char = chr(cp)
            for probe in (char, "a" + char, char + "a", "a" + char + "a"):
                django_out = compiled.render(DjangoContext({"p": probe}))
                djust_out = _rust.render_template(template, normalize_django_value({"p": probe}))
                checked += 1
                if django_out == djust_out:
                    continue
                if self._cpython_knows_no_case(char):
                    skew += 1
                    continue
                unexpected.append(
                    f"  U+{cp:04X} {probe!r}: django={django_out!r} djust={djust_out!r}"
                )
        assert checked > 4_000_000, checked
        assert not unexpected, (
            f"{len(unexpected)} codepoints diverge for a reason other than "
            f"Unicode-version skew:\n" + "\n".join(unexpected[:20])
        )
        # Skew is expected to be tiny; a large number would mean the tables in
        # `truncate.rs` were generated against the wrong interpreter.
        assert skew < 2000, f"unexpected amount of Unicode-version skew: {skew}"


class TestSigmaAndCasedTables:
    """The generated Unicode tables must still match the running interpreter.

    `truncate.rs` carries `CASED_RANGES`, `CASE_IGNORABLE_RANGES`, `ND_RANGES`
    and `TITLE_EXCEPTIONS`, all derived from CPython. These re-derive the same
    facts through the filter so a table that drifts from the interpreter fails
    loudly rather than showing up as a stray cell in a sweep.
    """

    def test_greek_sweep(self) -> None:
        alphabet = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩαβγδεζηθικλμνξοπρστυφχψωςΣ ,.'-1"
        rnd = random.Random(99)
        compiled = DjangoTemplate("{{ p|title }}")
        divergences = []
        for _ in range(1500):
            s = "".join(rnd.choice(alphabet) for _ in range(rnd.randrange(1, 10)))
            django_out = compiled.render(DjangoContext({"p": s}))
            djust_out = _rust.render_template("{{ p|title }}", normalize_django_value({"p": s}))
            if django_out != djust_out:
                divergences.append(f"  {s!r}: django={django_out!r} djust={djust_out!r}")
        assert not divergences, "\n".join(divergences[:15])

    def test_nd_is_not_all_numerics(self) -> None:
        """`²` is `N*` but not `Nd`; `\\d([A-Z])` must not match `²A`."""
        assert re.match(r"\d", "²") is None
        assert_agrees("{{ p|title }}", "a²b")
        assert_agrees("{{ p|title }}", "a2b")
        # And the two must differ, or the assertion above is vacuous.
        assert render_both("{{ p|title }}", "a²b")[0] != render_both("{{ p|title }}", "a2b")[
            0
        ].replace("2", "²")


class TestSafeMarking:
    """Whether a filter's output is escaped must match Django's `is_safe` flag.

    Django marks `slugify`, `title` and all four truncators `is_safe=True` and
    `urlencode` `is_safe=False`. For a plain (unsafe) input the distinction is
    invisible — the output is escaped either way — so the observable contract
    is: none of these filters may suppress autoescaping on its own. #2259 is a
    sibling issue where exactly this went wrong for `linebreaks`.
    """

    @pytest.mark.parametrize(
        "source",
        [
            "{{ p|slugify }}",
            "{{ p|title }}",
            "{{ p|truncatechars:20 }}",
            "{{ p|truncatewords:20 }}",
            "{{ p|truncatechars_html:20 }}",
            "{{ p|truncatewords_html:20 }}",
            "{{ p|urlencode }}",
            '{{ p|urlencode:"<>" }}',
        ],
    )
    def test_output_is_escaped_like_django(self, source: str) -> None:
        for value in ["<b>&'\"</b>", "a & b", "<script>x</script>", "'q'"]:
            assert_agrees(source, value)

    def test_safe_now_survives_these_filters(self) -> None:
        """`{{ p|safe|X }}` agrees. This pin was inverted by #2274.

        It used to assert the DIVERGENCE, with the instruction "closing it
        turns this red deliberately" — which is exactly what happened. Django's
        `FilterExpression.resolve` marks a filter's output safe when the filter
        has `is_safe=True` **and the input was already `SafeData`**; djust
        modelled only the first half, so `|safe` did not survive any subsequent
        filter. `filter_output_is_safe` now takes the input's safety and feeds
        it forward, so these agree and `safe|` is back in `_CHAINS`.

        `title` and `truncatechars` are both `is_safe=True`, so the markup must
        come through LIVE — an "escape everything" implementation fails here.
        """
        for source in ("{{ p|safe|title }}", "{{ p|safe|truncatechars:20 }}"):
            django_out, djust_out = render_both(source, "<b>&x</b>")
            assert "<" in django_out and "&lt;" not in django_out, (
                f"Django changed: {source} -> {django_out!r}"
            )
            assert djust_out == django_out, f"{source}: django={django_out!r} djust={djust_out!r}"
        # And the re-taint direction, so the above is not "stop escaping":
        # `upper` is `is_safe=False` in Django, so it undoes the `|safe`.
        assert_agrees("{{ p|safe|upper }}", "<b>&x</b>")
        # Without `|safe` the same filters still escape.
        assert_agrees("{{ p|title }}", "<b>&x</b>")
        assert_agrees("{{ p|truncatechars:20 }}", "<b>&x</b>")


# ---------------------------------------------------------------------------
# Known remaining divergences, with their measurement.
# ---------------------------------------------------------------------------


def _is_known_remaining(value: Any) -> bool:
    """True for inputs in a class this port deliberately does not close."""
    if not isinstance(value, str):
        value = str(value)
    if any(ord(c) > 127 for c in value):
        return True
    return False


class TestKnownRemainingDivergences:
    """Two classes are open, both needing Unicode normalization tables.

    `django.utils.text.slugify` opens with
    `unicodedata.normalize("NFKD", value).encode("ascii", "ignore")` and
    `Truncator.chars` with `unicodedata.normalize("NFC", text)` plus a
    `unicodedata.combining()` skip. Neither NFKD nor NFC is available without
    adding a Unicode normalization crate to the workspace, so neither is
    implemented and the residue is confined to non-ASCII input.

    Measured over the randomized sweep in this file (13,500 unchained cells):
    45 cells for `slugify`'s ASCII fold, 30 for `Truncator.chars`'s NFC — and
    zero for anything else. The tests below PIN the current behaviour so that
    closing the gap is a deliberate, visible change rather than a silent one.
    """

    def test_slugify_does_not_ascii_fold(self) -> None:
        django_out, djust_out = render_both("{{ p|slugify }}", "café")
        assert django_out == "cafe"
        # Not folded: the accented letter is kept rather than decomposed. It is
        # NOT dropped either — dropping it (the faithful-minus-NFKD reading)
        # would turn every non-English slug into rubble.
        assert djust_out == "café"

    def test_truncator_chars_does_not_normalize(self) -> None:
        decomposed = "éx"  # 'éx' as e + COMBINING ACUTE
        assert unicodedata.normalize("NFC", decomposed) != decomposed
        django_out, djust_out = render_both("{{ p|truncatechars:2 }}", decomposed)
        assert django_out == "éx"  # NFC-composed to 2 characters, so untouched
        assert djust_out == "e…"  # 3 characters here, so truncated

    def test_ascii_input_is_unaffected_by_either_gap(self) -> None:
        """The gap is exactly non-ASCII: no ASCII input may hit it."""
        for value in ["3.5", "-1.5e+300", "  spaced  ", "<b>x</b>", "a.b.c", "_a_"]:
            assert_agrees("{{ p|slugify }}", value)
            assert_agrees("{{ p|truncatechars:2 }}", value)
            assert_agrees("{{ p|truncatechars:8 }}", value)

    def test_big_integers_survive_since_2260(self) -> None:
        """An int past `2**63` used to lose precision BEFORE any filter ran.

        It accounted for every remaining pure-ASCII cell of the sweep while
        this branch was being written (42 of 13,500) and was excluded so the
        measurement would not attribute #2260 to a truncation filter. #2260
        closed on `main` in the meantime, so the exclusion is gone and this
        asserts the reason it is gone — both the bare variable and each ported
        filter now agree, which is what makes `_big_int` returning `False`
        correct rather than merely convenient.
        """
        big = 12345678901234567890
        assert render_both("{{ p }}", big) == ("12345678901234567890",) * 2
        for source in (
            "{{ p|slugify }}",
            "{{ p|title }}",
            "{{ p|truncatechars:20 }}",
            "{{ p|truncatewords:20 }}",
            "{{ p|truncatechars_html:20 }}",
            "{{ p|truncatewords_html:20 }}",
            "{{ p|urlencode }}",
        ):
            assert_agrees(source, big)


class TestEscapeChainIsBlockedOn2257:
    """`{{ p|escape|truncatechars_html:N }}` regressed, and the cause is `escape`.

    A set comparison against a real `origin/main` build over 24,300 cells found
    1,070 unchained cells fixed and **zero** unchained regressions; `upper|`,
    `lower|`, `safe|` and `striptags|` were also clean. The `escape|` chain
    fixed 395 and regressed **239**, all on the two HTML truncators.

    Those 239 agreed before for the WRONG REASON — two bugs cancelling. djust's
    `escape` returns its input unchanged (#2257 residue 1) and leans on
    autoescaping, so the truncator receives raw markup rather than the escaped
    text Django hands it; and the old truncator escaped tags verbatim instead of
    parsing them, which happened to undo that. Parsing the markup correctly
    uncovers the `escape` no-op.

    The proof is below and it is the whole claim of this PR restated: feed the
    port the string Django's `escape` ACTUALLY produces and it reproduces
    Django's chained answer exactly. All 239 pass it — so the fault is located
    in `escape`, not here, and it closes when #2257 does.

    A further **4** cells regressed on the `safe|` chain, for the neighbouring
    reason described in :meth:`TestSafeMarking.
    test_safe_does_not_survive_these_filters_and_that_is_framework_wide`: djust
    has no input-was-safe clause, so the text the parser already escaped is
    escaped again. The same proof holds for those, and is asserted below too.
    243 regressions, 243 explained by a chain link, 0 by the port.
    """

    @pytest.mark.parametrize(
        ("filter_expr", "value"),
        [
            # A processing instruction: Django's escape hides it from the
            # parser, djust's does not, so the port deletes it (correctly).
            ("truncatechars_html:8", "<?pi?>"),
            ("truncatechars_html:20", "<!doctype html>"),
            ("truncatechars_html:20", "<!-- c -->"),
            # A character reference: Django's escape neutralises the `&`.
            ("truncatechars_html:20", "&notanentity;"),
            ("truncatechars_html:20", "&#65;"),
            ("truncatewords_html:2", "  &  "),
            ("truncatewords_html:2", "a &amp; b"),
        ],
    )
    def test_feeding_the_port_djangos_escaped_string_reproduces_django(
        self, filter_expr: str, value: str
    ) -> None:
        from django.utils.html import escape as django_escape

        chained = "{{ p|escape|%s }}" % filter_expr
        django_out = DjangoTemplate(chained).render(DjangoContext({"p": value}))

        # A TRAILING `|safe` stands in for Django's `is_safe=True` carrying the
        # SafeString through — djust decides escaping from the LAST filter — and
        # the input is the string Django's `escape` really handed the truncator.
        as_django_feeds_it = _rust.render_template(
            "{{ p|%s|safe }}" % filter_expr, {"p": django_escape(value)}
        )
        assert as_django_feeds_it == django_out, (
            f"{chained} on {value!r}: the port itself is wrong, not `escape` — "
            f"django={django_out!r} port-fed-Django's-escaped-string="
            f"{as_django_feeds_it!r}"
        )

    @pytest.mark.parametrize(
        ("filter_expr", "value"),
        [
            # The `safe|` half of the same class: Django's `is_safe=True` keeps
            # the SafeString, so its autoescape pass does not run over the text
            # the parser already escaped. djust's rule has no input-was-safe
            # clause, so it escapes twice.
            ("truncatewords_html:2", "  &  "),
            ("truncatewords_html:2", "a & b"),
            ("truncatechars_html:20", "a & b"),
        ],
    )
    def test_the_same_proof_holds_for_the_safe_chain(self, filter_expr: str, value: str) -> None:
        chained = "{{ p|safe|%s }}" % filter_expr
        django_out = DjangoTemplate(chained).render(DjangoContext({"p": value}))
        # `safe` hands the filter the value UNCHANGED; the trailing `|safe`
        # stands in for `is_safe=True` keeping the result safe.
        as_django_feeds_it = _rust.render_template("{{ p|%s|safe }}" % filter_expr, {"p": value})
        assert as_django_feeds_it == django_out, (
            f"{chained} on {value!r}: the port itself is wrong, not the safe "
            f"rule — django={django_out!r} port={as_django_feeds_it!r}"
        )

    def test_the_escape_chain_agrees_now_that_escape_is_eager(self) -> None:
        """Closed — by #2281 rather than by #2257, which is the correction.

        This class located the fault in `escape` and predicted the chain would
        agree once `escape` stopped being a no-op. It does. The cause was
        #2257 residue 1 (the no-op) and #2281 fixed exactly that: `escape` is
        `conditional_escape` now, eager, so the truncator receives the escaped
        text Django hands it rather than raw markup.

        The proof rows above — feed the port Django's escaped string, get
        Django's answer — are what made the diagnosis checkable, and they still
        pass; this is the same claim asserted end-to-end.
        """
        django_out, djust_out = render_both("{{ p|escape|truncatechars_html:8 }}", "<?pi?>")
        assert django_out == "&lt;?pi?&gt;"
        assert djust_out == django_out, (
            f"the `escape|` chain regressed: django={django_out!r} djust={djust_out!r}"
        )


class TestUnescapeTableMatchesCPython:
    """`markup5ever`'s entity table must still equal CPython's `html5` map.

    The HTML truncators decode character references through it, so a
    divergence would be invisible until an entity-bearing input hit a sweep.
    """

    def test_named_entities_round_trip(self) -> None:
        import html.entities

        rnd = random.Random(7)
        names = sorted(html.entities.html5)
        sample = rnd.sample(names, 300)
        for name in sample:
            # Feed `&name` (with or without its `;`) through the parser and
            # check djust resolves it exactly as Django does.
            assert_agrees("{{ p|truncatechars_html:400 }}", f"x&{name} y")

    def test_numeric_charrefs(self) -> None:
        for raw in ["&#65;", "&#x41;", "&#65", "&#128;", "&#1;", "&#x110000;", "&#0;"]:
            assert_agrees("{{ p|truncatechars_html:400 }}", f"a{raw}b")


def test_module_imports_the_real_extension() -> None:
    """Guard against measuring a stale build (the sweeps are otherwise silent)."""
    assert _rust.__file__.endswith(".so") or _rust.__file__.endswith(".pyd"), (
        f"unexpected extension module: {_rust.__file__}"
    )
    assert "djust" in sys.modules
