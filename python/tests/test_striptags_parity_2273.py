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
than into the live `<script>` tag one pass leaves behind.

The port reuses the `html.parser.HTMLParser` tokenizer that #2262 added for the
HTML truncators, lifted behind a `Sink` trait in
`crates/djust_templates/src/htmlparser.rs` — one state machine, two handler
sets, rather than the parallel-path drift a second tokenizer would be (#1646).

The reference moves; the port does not
--------------------------------------
`html/parser.py` was rewritten for HTML5-spec alignment in **CPython 3.12.10**
and changed again in **3.14**, so the interpreters this project's CI matrix
runs do not agree with each other:

    3.12.9   vs 3.12.13 : 1108 / 4000 corpus values differ
    3.12.13  vs 3.13.7  :    0
    3.12.13  vs 3.14.6  :  224   (end-of-input `&` / `&#` handling)

The first version of this module computed its reference at run time, so it
asserted a different contract on every runner: green on the repo's 3.12.9
`.venv`, red in CI on 3.12/3.13/3.14. **No fixed port can be green against a
reference that disagrees with itself**, so the differential is pinned instead —
`python/tests/fixtures/striptags_reference_2273.json`, captured across every
matrix interpreter by `scripts/gen-striptags-reference.py` and split into:

* **stable** — every captured CPython agrees. djust must match, on any runner.
* **unstable** — they disagree, so there is no single right answer; djust must
  still behave like *one* supported CPython, and the disagreement is recorded
  in the repo rather than discovered in CI.

`TestPinnedReferenceIsHonest` keeps the pin from becoming a dumping ground: a
value may only sit in `unstable` if the recorded CPythons genuinely disagree,
and the `stable` half is re-derived from the running interpreter's real Django
on every run.

Port target
-----------
djust implements the **3.12.10+ / 3.13** tokenizer. The 3.14 delta is confined
to `&`/`&#` at end of input and is tracked separately; on the corpus djust
matches 3.13 on 1149 of the 1316 unstable values and 3.14 on 934, the remainder
being shared-tokenizer changes (comment close, `locatetagend`, the widened
CDATA element set) that also affect the truncators and are deliberately not in
this PR's scope.

One chain divergence that remains is NOT `striptags`: `|escape|striptags`
(#2281). It is pinned in `TestKnownRemainingDivergences` together with a proof
that `striptags` itself is byte-exact on the same value, so it does not get
re-diagnosed as this filter's bug. The other two were fixed while this file
was in flight -- `|safe|striptags` (#2280) by #2285, and `|striptags|length`
(#2279) by the `length` code-point fix, whose parity file is
`test_length_pprint_parity_2279_2277.py`.

Two defects in the port survived a curated table and were found only by the
randomized differential:

1. `feed()` + `close()` is genuinely **two** `goahead` passes, not a shorthand
   for one `goahead(1)`. The `&#`-bail arm is the only `break` that advances
   before stopping, so the following pass resumes the *loop* — and a second
   bail inside pass 2 has no pass 3.
2. `entityref`'s name class `[-.a-zA-Z0-9]` overlaps its own trailing
   `[^a-zA-Z0-9]` on `-` and `.`, so `re`'s **backtracking** is load-bearing:
   `&amp-` is the entity `amp` with `-` as the trail.
"""

from __future__ import annotations

import json
import pathlib
import random
import sys
from typing import Any

import pytest

pytest.importorskip("django")

from django.core.exceptions import SuspiciousOperation  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils.html import strip_tags as django_strip_tags  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "striptags_reference_2273.json"


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


def djust_answer(value: str) -> str:
    """djust's `striptags` result in the fixture's `OK:` / `RAISE:` encoding."""
    try:
        return "OK:" + _rust.render_template("{{ p|striptags|safe }}", {"p": value})
    except Exception as exc:  # noqa: BLE001
        return "RAISE:" + type(exc).__name__


def django_answer(value: str) -> str:
    """The running interpreter's REAL Django, same encoding."""
    try:
        return "OK:" + django_strip_tags(value)
    except SuspiciousOperation:
        return "RAISE:SuspiciousOperation"
    except Exception as exc:  # noqa: BLE001
        return "RAISE:" + type(exc).__name__


# ---------------------------------------------------------------------------
# Values compared LIVE against the running interpreter's Django.
#
# Every one of these must be **version-stable**: the same answer on the repo's
# `.venv` and on all three CI interpreters. A value whose answer moved between
# CPython releases cannot be asserted this way — it belongs in the pinned
# fixture, where each version's answer is recorded separately.
#
# `TestPinnedReferenceIsHonest::test_every_live_compared_value_is_stable`
# enforces that mechanically, because getting it wrong is silent: the test
# passes locally and fails only on the runner whose CPython disagrees. That is
# exactly how this module's first version shipped red.
# ---------------------------------------------------------------------------

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

LIVE_COMPARED_VALUES = [v for v, _ in REPORTED_CELLS] + UNREPORTED_VALUES + CHAIN_VALUES


# ---------------------------------------------------------------------------
# The corpus. Imported by scripts/gen-striptags-reference.py so the fixture and
# the test can never drift apart.
# ---------------------------------------------------------------------------

# Fragments chosen to reach every branch of `HTMLParser.goahead`: the `<`
# dispatch table, the `&` / `&#` charref arms, CDATA mode, the incomplete
# construct recovery, and the end-of-input tail flush.
FRAGMENTS = [
    # bare delimiters -- the issue's case and its neighbours
    "<",
    ">",
    "<>",
    "< >",
    "</>",
    "<<",
    ">>",
    "<<>>",
    "a < b",
    "a > b",
    "5 < 10 and 10 > 5",
    "x<y<z",
    "<b",
    "a <b",
    "<0>",
    "< b>",
    "<-",
    "<=",
    # well-formed markup
    "<b>",
    "</b>",
    "<i>",
    "</i>",
    "<br/>",
    "<br />",
    "<br>",
    "<p>",
    "</p>",
    "<div class='x'>",
    "</div>",
    '<a href="x">',
    "<img src=x>",
    "<span>",
    # malformed / tolerant-parse territory
    "<<b>script>",
    "<b ",
    "<b x",
    "<b x=",
    '<b x=">',
    "<b x=y",
    "<b/",
    "<b\n>",
    "<b\t>",
    "</ b>",
    "</b",
    "<b//>",
    '<a href="a>b">',
    "<b ==x>",
    # CDATA elements
    "<script>",
    "</script>",
    "<style>",
    "</style>",
    "<script>a<b</script>",
    "<style>x{}</style>",
    "</SCRIPT>",
    # comments, PIs, declarations
    "<!--",
    "-->",
    "<!-- c -->",
    "<!--->",
    "<!-->",
    "<!--a--!>",
    "<?",
    "<?php ?>",
    "<?pi>",
    "<!",
    "<!x>",
    "<!DOCTYPE html>",
    "<!doctype>",
    "]]>",
    "<![",
    "<![CDATA[x]]>",
    "<![endif]>",
    # entities -- the `convert_charrefs=False` arms
    "&",
    "&&",
    "&amp;",
    "&amp",
    "&lt;",
    "&gt;",
    "&nbsp;",
    "&one",
    "&one;",
    "&a b",
    "&a",
    "&#",
    "&#65;",
    "&#65",
    "&#x41;",
    "&#x41",
    "&#xZZ",
    "&#;",
    "&#x",
    "&z;",
    "&123;",
    "& ",
    "&;",
    "&amp-",
    "&one3.5",
    # plain prose
    "a",
    "b",
    "x",
    "word",
    "5",
    "10",
    " ",
    "\n",
    "\t",
    "hello world",
    "3.5",
    "price",
    "and",
    "-",
    "=",
    "/",
    "'",
    '"',
    "\\",
    "é",
    "中",
]

# Inputs whose point is the shape of the WHOLE string.
WHOLE = [
    "",
    "<",
    ">",
    "&",
    "a < b",
    "a > b",
    "5 < 10 and 10 > 5",
    "<<b>script>",
    "a <b",
    "x<y<z",
    "<bx&y3.5word",
    "price < 5 and > 3",
    "&one two<b>x</b>",
    "<i>&a b</i>",
    "&one two",
    "a<b>c</b>d",
    "<" * 10 + "b>" * 10,
    "<<<<<<<<<<b>>>>>>>>>>",
    # depth-guard territory (Django raises SuspiciousOperation on all three).
    "<a" + "<" * 49 + "y" * 1001,
    "<a" + "<" * 48 + "y" * 1001,
    "keepA" + "<" * 51 + "b>" * 51 + "keepB",
    "keepA" + "<" * 50 + "b>" * 50 + "keepB",
    # unterminated at end of input
    "text <b",
    "text &am",
    "text &#6",
    "text <!--",
    "text <?",
    "text <![CDATA[",
    "text <script>",
    "text </",
    "text </d",
]


def build_corpus(n: int = 4000, seed: int = 22730) -> list[str]:
    """Deterministic adversarial corpus. Also imported by the generator.

    Seeded with every live-compared literal so the fixture records their
    per-version answers, which is what makes the stability guard checkable.
    """
    rng = random.Random(seed)
    out = list(dict.fromkeys(WHOLE + LIVE_COMPARED_VALUES))
    seen = set(out)
    while len(out) < n:
        v = "".join(rng.choice(FRAGMENTS) for _ in range(rng.randint(1, 6)))
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


_FIXTURE_CACHE: dict[str, Any] | None = None


def load_fixture() -> dict[str, Any]:
    """Lazy so that `scripts/gen-striptags-reference.py` can import
    `build_corpus` from this module in order to CREATE the fixture."""
    global _FIXTURE_CACHE
    if _FIXTURE_CACHE is None:
        assert FIXTURE_PATH.exists(), (
            f"{FIXTURE_PATH} is missing — regenerate with scripts/gen-striptags-reference.py"
        )
        _FIXTURE_CACHE = json.loads(FIXTURE_PATH.read_text())
    return _FIXTURE_CACHE


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
    here — it lives in the pinned fixture instead.
    """

    @pytest.mark.parametrize("value", UNREPORTED_VALUES)
    def test_divergence(self, value: str) -> None:
        assert_agrees("{{ p|striptags }}", value)


# ---------------------------------------------------------------------------
# The chain axis.
# ---------------------------------------------------------------------------

CHAINS = [
    "{{ p|striptags }}",
    # #2280 (a filter's `is_safe=True` was not honoured) was fixed by #2285
    # while this PR was open, so `|safe|striptags` is a tested chain rather
    # than a pinned divergence. `TestKnownRemainingDivergences` fired on its
    # own instruction the moment that landed, which is what the "now AGREES --
    # delete this row" assertion is for.
    "{{ p|safe|striptags }}",
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

    `{{ p|escape|striptags }}` is deliberately absent: it diverges for a
    reason that has nothing to do with `striptags` (#2281), pinned in
    `TestKnownRemainingDivergences`. `{{ p|safe|striptags }}` was in the same
    position until #2285 fixed #2280, and is now tested here.
    """

    @pytest.mark.parametrize("source", CHAINS)
    @pytest.mark.parametrize("value", CHAIN_VALUES)
    def test_chain(self, source: str, value: str) -> None:
        assert_agrees(source, value)


# ---------------------------------------------------------------------------
# The pinned differential.
# ---------------------------------------------------------------------------


class TestPinnedDifferential:
    """djust against the captured reference — the same assertion on every
    runner, because the expected bytes come from the repo and not from the
    interpreter running the test."""

    def test_stable_values_match_the_pinned_reference(self) -> None:
        stable = load_fixture()["stable"]
        mismatches = []
        for value, expected in stable.items():
            got = djust_answer(value)
            if got == expected:
                continue
            # A reference that RAISES has no djust equivalent: the Rust filter
            # layer returns a String, and `SuspiciousOperation` is a Django
            # request concept. Those are pinned in
            # `TestKnownRemainingDivergences` instead.
            if expected.startswith("RAISE:"):
                continue
            mismatches.append((value, expected, got))
        assert not mismatches, (
            f"{len(mismatches)} of {len(stable)} version-stable values diverge; "
            f"first 5: {mismatches[:5]}"
        )

    def test_version_dependent_values_track_a_supported_cpython(self) -> None:
        """Where the CPythons disagree there is no single right answer — but
        djust must still behave like ONE of them, not like nothing.

        This is what stops "the reference moves" from becoming a licence for
        arbitrary behaviour on adversarial input.
        """
        unstable = load_fixture()["unstable"]
        orphans = []
        for value, per_version in unstable.items():
            recorded = set(per_version.values())
            got = djust_answer(value)
            if got in recorded:
                continue
            if all(a.startswith("RAISE:") for a in recorded):
                # Every CPython raises; djust renders instead, by design.
                assert got.startswith("OK:"), (value, got)
                continue
            orphans.append((value, sorted(recorded)[:2], got))
        assert not orphans, (
            f"{len(orphans)} of {len(unstable)} version-dependent values match "
            f"NO supported CPython; first 5: {orphans[:5]}"
        )

    def test_the_corpus_is_not_vacuous(self) -> None:
        """The corpus must reach the branches it claims to.

        Without this, a generator that produced only inert strings would make
        the assertions above pass for the wrong reason.
        """
        values = build_corpus()
        joined = "".join(values)
        assert sum("<" in v and ">" not in v for v in values) > 50, "no lone-`<`"
        assert sum(">" in v and "<" not in v for v in values) > 15, "no lone-`>`"
        assert "&#" in joined and "&amp" in joined, "no charrefs"
        assert sum("<script" in v for v in values) > 20, "no CDATA"
        assert sum("<!--" in v for v in values) > 20, "no comments"
        changed = sum(1 for v in values if djust_answer(v) != "OK:" + v)
        assert changed > len(values) // 3, f"only {changed} values were altered"


class TestPinnedReferenceIsHonest:
    """The pin must not be able to launder a real divergence.

    A fixture that the test suite never re-derives is just a record of whatever
    the code did on the day it was written. These three checks make it a
    record of what *Django* does.
    """

    def test_the_corpus_matches_the_fixture(self) -> None:
        corpus = set(build_corpus())
        recorded = set(load_fixture()["stable"]) | set(load_fixture()["unstable"])
        assert corpus == recorded, (
            f"corpus and fixture disagree "
            f"({len(corpus - recorded)} unrecorded, "
            f"{len(recorded - corpus)} stale) — regenerate with "
            f"scripts/gen-striptags-reference.py"
        )

    def test_pinned_stable_reference_matches_this_interpreter(self) -> None:
        """Re-derive the stable half from the runner's REAL Django.

        Safe to assert on any runner precisely because these are the values
        every captured CPython agreed on. If a future CPython moves one, this
        goes red and names it — which is the correct signal, and the one the
        run-time-reference version of this module could not give.
        """
        stable = load_fixture()["stable"]
        moved = [
            (v, expected, django_answer(v))
            for v, expected in stable.items()
            if django_answer(v) != expected
        ]
        assert not moved, (
            f"{len(moved)} pinned-stable values no longer match this "
            f"interpreter (python {'.'.join(map(str, sys.version_info[:3]))}, "
            f"captured on {load_fixture()['versions']}). Either CPython's html.parser "
            f"or Django's strip_tags moved; regenerate the fixture. "
            f"First 5: {moved[:5]}"
        )

    def test_every_unstable_value_is_genuinely_unstable(self) -> None:
        """A value may only sit in `unstable` if the CPythons really disagree.

        Without this the exclusion list is a place to hide failures: any
        inconvenient value could be moved out of `stable` and forgotten.
        """
        singletons = [
            v for v, per in load_fixture()["unstable"].items() if len(set(per.values())) < 2
        ]
        assert not singletons, (
            f"{len(singletons)} 'unstable' values have only ONE recorded "
            f"answer — they belong in `stable`: {singletons[:5]}"
        )

    def test_every_live_compared_value_is_stable(self) -> None:
        """No value compared against the RUNNING Django may be one the
        CPythons disagree about.

        This is the guard for the failure that made this PR red in CI: a
        literal whose answer moved in 3.12.10 passes on the repo's 3.12.9
        `.venv` and fails on every CI interpreter. Three such values were in
        this module's `UNREPORTED_VALUES` list, put there by hand.
        """
        unstable = load_fixture()["unstable"]
        leaked = [v for v in LIVE_COMPARED_VALUES if v in unstable]
        assert not leaked, (
            f"{len(leaked)} live-compared values are version-dependent and "
            f"will fail on some runner — move them to the pinned fixture: "
            f"{leaked}"
        )
        missing = [
            v
            for v in LIVE_COMPARED_VALUES
            if v not in unstable and v not in load_fixture()["stable"]
        ]
        assert not missing, (
            f"{len(missing)} live-compared values are not in the fixture at "
            f"all, so their stability is unverified: {missing}"
        )

    def test_the_fixture_spans_more_than_one_cpython(self) -> None:
        versions = load_fixture()["versions"]
        assert len(versions) >= 2, versions
        # The split is only meaningful if the captured versions actually
        # straddle the 3.12.10 html.parser rewrite.
        assert len(load_fixture()["unstable"]) > 100, (
            f"only {len(load_fixture()['unstable'])} unstable values — the capture "
            f"probably used interpreters that all share one html.parser"
        )


# ---------------------------------------------------------------------------
# What stays divergent, with its measurement.
# ---------------------------------------------------------------------------


class TestKnownRemainingDivergences:
    """Django's `raise` path, and three chain cells whose cause is elsewhere."""

    @pytest.mark.parametrize(
        "value",
        [
            # `<[a-zA-Z][^>]{1000,}` carrying >= 50 `<` -- the match's own
            # leading `<` counts, so 49 inner ones are enough.
            "<a" + "<" * 49 + "y" * 1001,
            # Still shedding tags after 50 passes -- Django allows exactly 50
            # and raises on the 51st. `keepA`/`keepB` make the UNCAPPED
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
            DjangoTemplate("{{ p|striptags }}").render(DjangoContext({"p": fifty})) == "keepAkeepB"
        )

    # The parametrized `test_chain_divergence_is_not_striptags` that lived here
    # is GONE, and both of its rows went for the same reason: each carried a
    # "now AGREES -- delete this row" assertion and each fired.
    #
    # * `{{ p|striptags|length }}` — #2279 closed it; `length` counts code
    #   points now. Its replacement is `test_length_pprint_parity_2279_2277.py`,
    #   which pins the chain cell as an AGREEMENT.
    # * `{{ p|escape|striptags }}` — #2281 closed it; `escape` is eager now, so
    #   `striptags` receives the escaped text and finds no tags to strip. Its
    #   replacement is the method below.
    #
    # The two landed within an hour of each other and emptied the list between
    # them. An empty `parametrize` collects ZERO cases and reports green, which
    # is the decorative-test shape (#1859) — so the class is the closing
    # assertions rather than a husk waiting for a third row.

    def test_the_escape_chain_now_agrees(self) -> None:
        """#2281, closed. Was a row above; kept as the opposite assertion.

        djust's `escape` deferred to render time, so `striptags` received the
        RAW value and stripped tags Django's `escape` had already turned into
        inert text. `escape` is eager now, so nothing is left to strip — and
        `striptags` itself was byte-exact on this value the whole time, which
        is what the row asserted and this keeps asserting.
        """
        assert_agrees("{{ p|escape|striptags }}", "a<b>c</b>d")
        assert_agrees("{{ p|striptags|safe }}", "a<b>c</b>d")
        django_out, _ = render_both("{{ p|escape|striptags }}", "a<b>c</b>d")
        assert django_out == "a&lt;b&gt;c&lt;/b&gt;d", django_out
