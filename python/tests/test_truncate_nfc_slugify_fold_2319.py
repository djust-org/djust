"""`truncatechars` normalizes NFC and skips combining marks; `slugify` folds
NFKD to ASCII (#2319).

The three gaps
--------------
`django.utils.text.Truncator.chars` opens with
`unicodedata.normalize("NFC", text)` and `_text_chars` then skips characters
whose canonical combining class is non-zero. `calculate_truncate_chars_length`
applies the same skip to the truncation TEXT, so a combining mark inside
`truncate` costs nothing either. `slugify` opens with
`unicodedata.normalize("NFKD", value).encode("ascii", "ignore")`.

The Rust port implemented none of the three, which is why `{{ p|slugify }}`
left `café` alone and `{{ p|truncatechars:5 }}` cut a decomposed `ábcdefg` one
character early.

Why this needed a dependency and #2292 did not
-----------------------------------------------
Both issues are "does djust get to depend on Unicode tables", and they have
OPPOSITE answers, for a measurable reason.

`str.isprintable()` (#2292) is version-dependent across djust's matrix -- five
Unicode versions, 11130 disagreeing code points -- but every disagreement is an
UNASSIGNED code point becoming assigned, so the stable part is a 28-range hand
table and no crate is needed.

Canonical combining class and canonical decomposition are different: they are
covered by the Unicode Character Encoding Stability Policies and are immutable
once a code point is assigned. Measured across CPython 3.10-3.14 (Unicode 13.0,
14.0, 15.0, 15.1, 16.0):

    code points with non-zero combining class : 872, 912, 922, 922, 934
    combining class CHANGED for an existing point :   0
    canonical decomposition CHANGED for an existing point : 0

Zero, at every step. So a table is SAFE here -- but it is not hand-rollable:
NFC needs canonical decomposition, canonical ordering and the composition
exclusion set, and `slugify` additionally needs NFKD. That is
`unicode-normalization`, and adopting it is what this change decides.

`TestTheStabilityClaimIsTrueOnThisInterpreter` re-derives the stability half of
that argument from the running interpreter, so the justification is checked
rather than asserted.

What Django does NOT do, which the issue got wrong
---------------------------------------------------
The issue says `truncatechars` AND `truncatechars_html` "count combining marks
where Django's `Truncator._text_chars` skips them". Only half is right.
`TruncateCharsHTMLParser.process` counts with a plain `len(data)` and does NOT
skip combining marks -- verified against live Django in
`TestHtmlPathNormalizesButDoesNotSkip`. For the HTML variant, NFC alone
accounts for the whole divergence, and adding a combining skip there would
introduce a NEW one. Likewise `Truncator.words` reads `self._wrapped`
directly: the `words` filters must NOT normalize, pinned as a negative control
in `TestWordsFiltersMustNotNormalize`.

Reading this file
-----------------
**Every decomposed value is built programmatically from an explicit `\\uXXXX`
escape**, never typed as a literal. A literal decomposed sequence is
NFC-normalized on the way to disk by editors and agent file-writers, which
turns it into the PRECOMPOSED form -- exactly the transformation under test, so
the test would pass against the unfixed code while never constructing the bug.
`assert_decomposed` is called on every such value so that a normalization
accident is a red test rather than a silent one.
"""

from __future__ import annotations

import random
import unicodedata
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.utils.text import Truncator, slugify as django_slugify  # noqa: E402

from test_length_pprint_parity_2279_2277 import assert_agrees, render_both  # noqa: E402

# Combining marks, spelled as escapes. Never as literals -- see the module
# docstring.
ACUTE = "\u0301"  # COMBINING ACUTE ACCENT, ccc=230
DIAERESIS = "\u0308"  # COMBINING DIAERESIS, ccc=230
CEDILLA = "\u0327"  # COMBINING CEDILLA, ccc=202
RING_ABOVE = "\u030a"  # COMBINING RING ABOVE, ccc=230

E_ACUTE = "é"  # the PRECOMPOSED form of "e" + ACUTE
A_ACUTE = "á"  # the PRECOMPOSED form of "a" + ACUTE


# Code points with a non-zero canonical combining class, per Unicode version.
# These are the numbers the module docstring quotes as the measurement behind
# the dependency decision, and `TestTheStabilityClaimIsTrueOnThisInterpreter`
# recomputes the row for whichever interpreter is running rather than trusting
# them. Keyed by Unicode version, not by CPython version, because that is what
# actually determines the tables.
CCC_TOTALS = {
    "13.0.0": 872,  # CPython 3.10
    "14.0.0": 912,  # CPython 3.11
    "15.0.0": 922,  # CPython 3.12
    "15.1.0": 922,  # CPython 3.13
    "16.0.0": 934,  # CPython 3.14
}

MAX_CODE_POINT = 0x110000


def assert_decomposed(value: str) -> str:
    """Guard: `value` must actually be decomposed.

    A one-line check that turns the silent measurement failure this whole file
    is about into a red test. If a tool normalized the value on the way to
    disk, NFC is a no-op on it and every assertion downstream is vacuous.
    """
    assert unicodedata.normalize("NFC", value) != value, (
        f"{[hex(ord(c)) for c in value]} is NOT decomposed -- it was normalized "
        f"on the way to disk, and every assertion about it is vacuous"
    )
    return value


# ---------------------------------------------------------------------------
# The dependency decision's premise
# ---------------------------------------------------------------------------


class TestTheStabilityClaimIsTrueOnThisInterpreter:
    """The combining class / decomposition tables are stable, unlike
    `isprintable`. Checked, not asserted."""

    def test_the_combining_class_total_for_this_interpreter(self) -> None:
        """The docstring's "872, 912, 922, 922, 934", recomputed."""
        version = unicodedata.unidata_version
        assert version in CCC_TOTALS, (
            f"this interpreter carries Unicode {version}, which CCC_TOTALS does "
            f"not list (it has {sorted(CCC_TOTALS)}) -- add the row, and re-check "
            f"that the stability argument still holds for it"
        )
        actual = sum(1 for cp in range(MAX_CODE_POINT) if unicodedata.combining(chr(cp)))
        assert actual == CCC_TOTALS[version], (
            f"Unicode {version} should have {CCC_TOTALS[version]} code points with "
            f"a non-zero combining class; this interpreter counts {actual}"
        )

    def test_the_combining_class_table_only_ever_grows(self) -> None:
        """Internal consistency of the measurement.

        The full claim -- that no ALREADY-ASSIGNED code point ever changed its
        combining class -- needs two interpreters at once and so cannot be
        checked from inside one test process; it is recorded in the PR, where it
        was measured across all ten ordered pairs of 3.10-3.14. What IS checkable
        here is that the totals never shrink, which a reclassification-to-zero
        would violate.
        """
        totals = [CCC_TOTALS[v] for v in sorted(CCC_TOTALS)]
        assert totals == sorted(totals), (
            f"the combining-class totals are not monotonic: {totals} -- a code "
            f"point lost its combining class, which the stability policy forbids"
        )

    def test_combining_class_is_defined_for_the_marks_this_file_uses(self) -> None:
        for mark, expected_ccc in [
            (ACUTE, 230),
            (DIAERESIS, 230),
            (CEDILLA, 202),
            (RING_ABOVE, 230),
        ]:
            assert unicodedata.combining(mark) == expected_ccc, (
                f"U+{ord(mark):04X} has combining class "
                f"{unicodedata.combining(mark)} on Unicode "
                f"{unicodedata.unidata_version}, not {expected_ccc}"
            )

    def test_precomposed_and_decomposed_spellings_are_nfc_equivalent(self) -> None:
        """The property the whole port relies on."""
        assert unicodedata.normalize("NFC", assert_decomposed("e" + ACUTE)) == E_ACUTE
        assert unicodedata.normalize("NFC", assert_decomposed("a" + ACUTE)) == A_ACUTE

    def test_a_combining_mark_is_not_a_printable_question(self) -> None:
        """Why #2292 and #2319 get opposite answers: combining marks are
        assigned, printable, ordinary characters. Their instability question is
        about the ccc TABLE, not about assignment."""
        assert ACUTE.isprintable()
        assert unicodedata.category(ACUTE) == "Mn"


class TestNoCombiningMarkLiteralsInTheSources:
    """The escape-don't-literal rule, enforced rather than merely documented.

    A combining mark typed as a LITERAL is invisible in a diff, indistinguishable
    on screen from its precomposed form, and silently NFC-normalized on the way
    to disk by editors and agent file-writers. When that happens to a test value
    whose whole point is that it is decomposed, the test starts asserting the
    POST-fix value and passes against the UNFIXED code -- coverage that was never
    constructed.

    #2319 documents this at length and asks whatever closes it to keep the
    discipline up, so here it is as a check. Scanning the files rather than
    trusting the convention is the only form that survives the next edit.

    Precomposed non-ASCII (``é``, ``ü``, ``中``) is fine and stays readable as
    itself; only characters with a non-zero canonical combining class are
    banned, because those are the ones a normalizer moves.
    """

    SOURCES = [
        "python/tests/test_truncate_nfc_slugify_fold_2319.py",
        "python/tests/test_py_repr_isprintable_table_2292.py",
        "crates/djust_templates/src/truncate.rs",
        "crates/djust_core/src/lib.rs",
    ]

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

    @pytest.mark.parametrize("relative", SOURCES)
    def test_no_combining_mark_is_written_as_a_literal(self, relative: str) -> None:
        path = self._repo_root() / relative
        assert path.exists(), f"{relative} moved -- update SOURCES"
        text = path.read_text(encoding="utf-8")
        offenders = [
            (lineno, hex(ord(char)), unicodedata.name(char, "?"))
            for lineno, line in enumerate(text.split("\n"), 1)
            for char in line
            if ord(char) > 0x7F and unicodedata.combining(char)
        ]
        assert not offenders, (
            f"{relative} contains {len(offenders)} combining mark(s) written as "
            f"LITERALS rather than \\uXXXX escapes: {offenders[:5]}. "
            f"They are invisible in review and one save hook away from being "
            f"NFC-normalized into their precomposed form -- which is exactly the "
            f"transformation under test here."
        )

    def test_the_guard_would_catch_a_literal(self) -> None:
        """Gate-off for the guard itself: it must reject the thing it bans."""
        smuggled = "e\u0301"  # what a normalizer would turn into "\u00e9"
        offenders = [c for c in smuggled if ord(c) > 0x7F and unicodedata.combining(c)]
        assert offenders, "the predicate the guard uses does not detect a combining mark"


# ---------------------------------------------------------------------------
# The issue's reproducers
# ---------------------------------------------------------------------------


class TestTheIssuesReproducers:
    """The four cases in the issue body, each verified against live Django."""

    def test_truncatechars_counts_a_decomposed_pair_as_one(self) -> None:
        value = assert_decomposed("a" + ACUTE + "bcdefg")
        assert len(value) == 8, "8 code points, NFC-composing to 7"
        django_out, djust_out = render_both("{{ p|truncatechars:5 }}", value)
        assert django_out == A_ACUTE + "bcd…"
        assert djust_out == django_out

    def test_a_string_of_five_decomposed_pairs_is_not_truncated(self) -> None:
        value = assert_decomposed(("e" + ACUTE) * 5)
        assert len(value) == 10, "10 code points, NFC-composing to 5"
        django_out, djust_out = render_both("{{ p|truncatechars:5 }}", value)
        assert django_out == E_ACUTE * 5
        assert djust_out == django_out
        assert "…" not in djust_out, "nothing should have been truncated"

    def test_truncatechars_html(self) -> None:
        value = assert_decomposed("<b>" + "a" + ACUTE + "bcdefg</b>")
        django_out, djust_out = render_both("{{ p|truncatechars_html:5 }}", value)
        assert djust_out == django_out

    def test_slugify_folds_to_ascii(self) -> None:
        django_out, djust_out = render_both("{{ p|slugify }}", "caf" + E_ACUTE)
        assert django_out == "cafe"
        assert djust_out == "cafe"


# ---------------------------------------------------------------------------
# Mechanism 1 -- NFC, and that it is UNCONDITIONAL
# ---------------------------------------------------------------------------


class TestNfcIsUnconditional:
    """`Truncator.chars` normalizes whether or not it truncates.

    So the combining skip alone is not enough for byte-parity: implementing it
    without NFC gets the right character COUNT and the wrong normalization
    FORM. This class is the half that would still fail in that case.
    """

    @pytest.mark.parametrize(
        "value",
        ["e" + ACUTE, "e" + ACUTE + "x", "a" + ACUTE + "bc", "c" + CEDILLA + "a"],
    )
    def test_untruncated_input_comes_back_composed(self, value: str) -> None:
        assert_decomposed(value)
        django_out, djust_out = render_both("{{ p|truncatechars:50 }}", value)
        assert django_out == unicodedata.normalize("NFC", value)
        assert django_out != value, "the case does not exercise normalization"
        assert djust_out == django_out

    def test_the_html_variant_normalizes_too(self) -> None:
        value = assert_decomposed("<i>" + "e" + ACUTE + "</i>")
        django_out, djust_out = render_both("{{ p|truncatechars_html:50 }}", value)
        assert E_ACUTE in django_out, "Django did not compose"
        assert djust_out == django_out

    def test_multiple_marks_reorder_canonically(self) -> None:
        """NFC is not just composition -- it canonically ORDERS the marks by
        combining class first. CEDILLA (202) sorts before ACUTE (230)."""
        value = assert_decomposed("c" + ACUTE + CEDILLA)
        expected = unicodedata.normalize("NFC", value)
        assert expected != value
        django_out, djust_out = render_both("{{ p|truncatechars:50 }}", value)
        assert django_out == expected
        assert djust_out == django_out

    def test_a_composition_exclusion_is_honoured(self) -> None:
        """Not every decomposable sequence recomposes: the composition
        exclusion set is why NFC needs real tables rather than a mapping.

        U+0344 decomposes but its result does not recompose to it.
        """
        value = "\u0344"
        composed = unicodedata.normalize("NFC", value)
        assert composed != value, "U+0344 is expected to decompose under NFC"
        django_out, djust_out = render_both("{{ p|truncatechars:50 }}", value)
        assert django_out == composed
        assert djust_out == django_out


# ---------------------------------------------------------------------------
# Mechanism 2 -- the combining skip when counting text
# ---------------------------------------------------------------------------


class TestCombiningMarksAreFreeWhenCounting:
    """The skip in `_text_chars`, isolated from NFC.

    Every value here uses a mark that does NOT compose with its base, so NFC is
    a no-op and only the skip is under test. Without that separation the two
    mechanisms shadow each other and gating either off leaves the suite green.
    """

    @staticmethod
    def _non_composing(base: str, mark: str) -> str:
        value = base + mark
        assert unicodedata.normalize("NFC", value) == value, (
            f"{base!r}+U+{ord(mark):04X} composes, so this value cannot isolate "
            f"the combining skip from NFC"
        )
        return value

    def test_a_non_composing_mark_costs_nothing(self) -> None:
        # "q" has no precomposed form with an acute accent, so NFC leaves this
        # exactly as written and only the combining skip can explain the count.
        value = self._non_composing("q", ACUTE) + "bcdefg"
        django_out, djust_out = render_both("{{ p|truncatechars:5 }}", value)
        assert django_out == "q" + ACUTE + "bcd…"
        assert djust_out == django_out

    def test_many_marks_on_one_base_still_cost_one(self) -> None:
        value = "q" + ACUTE + DIAERESIS + RING_ABOVE + "bcdefg"
        assert unicodedata.normalize("NFC", value) == value, "NFC must be a no-op here"
        django_out, djust_out = render_both("{{ p|truncatechars:3 }}", value)
        assert djust_out == django_out

    def test_a_leading_combining_mark(self) -> None:
        """A mark with no base at all -- the index-0 edge of the skip loop."""
        value = ACUTE + "abcdefgh"
        assert unicodedata.normalize("NFC", value) == value
        django_out, djust_out = render_both("{{ p|truncatechars:4 }}", value)
        assert djust_out == django_out

    def test_only_combining_marks(self) -> None:
        value = ACUTE * 6
        assert unicodedata.normalize("NFC", value) == value
        django_out, djust_out = render_both("{{ p|truncatechars:2 }}", value)
        assert djust_out == django_out


class TestCombiningMarksInTheTruncationTextAreFree:
    """`calculate_truncate_chars_length` applies the same skip to `truncate`.

    A separate mechanism from the text skip, and one no test of the text half
    can reach -- so it gets its own cases.
    """

    def test_a_mark_in_the_truncation_text_costs_nothing(self) -> None:
        """The contract, stated against `Truncator` directly.

        The `truncatechars` FILTER always passes `truncate=None` (Django's
        filter signature has no slot for it), so this mechanism is not
        reachable from a template. It is reachable from
        `calculate_truncate_chars_length`, which the HTML parser also calls,
        and the Rust unit tests in `truncate.rs` exercise it at that level.
        Asserted here so the reference behaviour is recorded next to the rest.
        """
        truncation = "e" + ACUTE + ".."
        # 5 minus three NON-combining characters ("e", ".", ".") is 2, so two
        # characters of the source survive.
        assert Truncator("abcdefghij").chars(5, truncate=truncation) == ("ab" + truncation)
        # Without the skip it would be 5 - 4 = 1, i.e. "a" + truncation.
        assert Truncator("abcdefghij").chars(5, truncate="ex..") == "a" + "ex.."

    def test_the_default_ellipsis_costs_one(self) -> None:
        """The default truncation text is a single character, so `truncate_len`
        is `length - 1` -- the arithmetic every other case here depends on."""
        assert Truncator("abcdefghij").chars(5) == "abcd…"
        assert_agrees("{{ p|truncatechars:5 }}", "abcdefghij")

    def test_truncation_text_of_only_marks_costs_nothing(self) -> None:
        truncation = ACUTE * 3
        assert Truncator("abcdefghij").chars(5, truncate=truncation) == ("abcde" + truncation)


# ---------------------------------------------------------------------------
# The premise correction -- what the HTML and words paths must NOT do
# ---------------------------------------------------------------------------


class TestHtmlPathNormalizesButDoesNotSkip:
    """The issue's premise, corrected.

    `TruncateCharsHTMLParser.process` counts `len(data)` with no combining
    check, so the HTML variant needs NFC and NOTHING ELSE. Adding a skip there
    would create a fresh divergence, so these cases pin the difference rather
    than merely covering the filter.
    """

    def test_the_html_and_plain_paths_disagree_and_that_is_correct(self) -> None:
        value = assert_decomposed(("e" + ACUTE) * 5)
        plain = Truncator(value).chars(5)
        html = Truncator("<b>" + value + "</b>").chars(5, html=True)
        # Plain keeps all five (five characters after NFC); HTML truncates,
        # because its budget is `length - len(ellipsis)` applied to raw chars.
        assert plain == E_ACUTE * 5
        assert "…" in html, (
            "live Django no longer truncates here -- the premise this pin "
            "corrects has changed and the port must be re-checked"
        )
        assert plain != html.replace("<b>", "").replace("</b>", "")

    def test_djust_reproduces_the_html_path_including_that_difference(self) -> None:
        value = assert_decomposed(("e" + ACUTE) * 5)
        django_out, djust_out = render_both("{{ p|truncatechars_html:5 }}", "<b>" + value + "</b>")
        assert djust_out == django_out

    def test_a_non_composing_mark_is_COUNTED_by_the_html_path(self) -> None:
        """The sharpest form: a mark NFC cannot absorb still costs a character
        in the HTML variant, where it would be free in the plain one."""
        value = "<b>q" + ACUTE + "bcdefg</b>"
        assert unicodedata.normalize("NFC", value) == value
        django_out, djust_out = render_both("{{ p|truncatechars_html:5 }}", value)
        assert djust_out == django_out


class TestWordsFiltersMustNotNormalize:
    """`Truncator.words` reads `self._wrapped`, not the normalized text.

    A negative control: if the NFC were applied at too high a level -- to the
    filter dispatch, or to `Truncator` as a whole -- these would go red.
    """

    @pytest.mark.parametrize("source", ["{{ p|truncatewords:2 }}", "{{ p|truncatewords_html:2 }}"])
    def test_decomposed_input_survives_unnormalized(self, source: str) -> None:
        value = assert_decomposed("e" + ACUTE + " b c d")
        django_out, djust_out = render_both(source, value)
        assert ACUTE in django_out, (
            "live Django now normalizes in words(), which this pin says it "
            "does not -- re-check the port"
        )
        assert djust_out == django_out


# ---------------------------------------------------------------------------
# Mechanism 3 -- slugify's NFKD fold
# ---------------------------------------------------------------------------


class TestSlugifyAsciiFold:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("caf" + E_ACUTE, "cafe"),
            ("caf" + "e" + ACUTE, "cafe"),  # decomposed spelling, same slug
            ("über", "uber"),
            ("Strße", "stre"),
            ("中文", ""),  # no ASCII fold exists; everything drops
            ("A\u030a" + "ngström", "angstrom"),
        ],
    )
    def test_matches_django(self, value: str, expected: str) -> None:
        assert django_slugify(value) == expected, "the table's expectation is wrong"
        _, djust_out = render_both("{{ p|slugify }}", value)
        assert djust_out == expected

    def test_the_fold_happens_before_lowercasing(self) -> None:
        """Django's order is NFKD -> encode ascii ignore -> lower().

        Doing it the other way round changes the answer for characters whose
        lowercase form is not ASCII even though their NFKD fold is.
        """
        for value in ["İ", "É", "ẞ"]:
            _, djust_out = render_both("{{ p|slugify }}", value)
            assert djust_out == django_slugify(value), repr(value)

    def test_ascii_input_is_untouched(self) -> None:
        for value in ["hello world", "a.b.c", "_a_", "  spaced  ", "A-B--C"]:
            assert_agrees("{{ p|slugify }}", value)

    def test_a_compatibility_decomposition_not_just_a_canonical_one(self) -> None:
        """NFKD, not NFD: the ligature and the superscript only fold under the
        COMPATIBILITY mapping, so a canonical-only implementation fails here."""
        for value, expected in [("ﬁn", "fin"), ("x²", "x2")]:
            assert django_slugify(value) == expected
            _, djust_out = render_both("{{ p|slugify }}", value)
            assert djust_out == expected


class TestSlugifyNonAsciiWhitespaceGoesThroughTheFoldFirst:
    """Moving the NFKD fold AHEAD of the separator logic changes the answer for
    non-ASCII whitespace -- in Django's direction, and not uniformly.

    Before #2319 the port lowercased and then asked ``py_is_space``, so ANY
    Unicode whitespace was a separator: ``a\u2028b`` slugged to ``a-b``. Django
    folds and drops non-ASCII first, so ``U+2028`` never reaches the separator
    pass at all and the answer is ``ab``.

    The split is not "non-ASCII whitespace is dropped" either, which is why the
    cases are enumerated rather than reasoned about: ``U+00A0`` and ``U+3000``
    NFKD-decompose *to an ordinary space* and so stay separators, while
    ``U+2028``, ``U+200B`` and ``U+1680`` have no ASCII decomposition and
    vanish. Two characters that are both ``str.isspace()`` end up on opposite
    sides, decided by the decomposition table rather than by any whitespace
    predicate.

    Adjacent to the whitespace-boundary corpus coupling from #2293, and pinned
    here because ``truncate::py_is_space`` is the predicate this fix moved the
    fold in front of.
    """

    @pytest.mark.parametrize(
        ("value", "expected", "why"),
        [
            ("a\u00a0b", "a-b", "NBSP decomposes to SPACE, so it stays a separator"),
            ("a\u3000b", "a-b", "IDEOGRAPHIC SPACE likewise"),
            ("a\u2007b", "a-b", "FIGURE SPACE likewise"),
            ("a\u2028b", "ab", "LINE SEPARATOR has no ASCII fold; dropped"),
            ("a\u2029b", "ab", "PARAGRAPH SEPARATOR likewise"),
            ("a\u200bb", "ab", "ZERO WIDTH SPACE likewise"),
            ("a\u1680b", "ab", "OGHAM SPACE MARK likewise"),
            ("a\u001fb", "a-b", "an ASCII control survives the fold"),
            ("a b", "a-b", "the ordinary case, unchanged"),
        ],
    )
    def test_matches_django(self, value: str, expected: str, why: str) -> None:
        assert django_slugify(value) == expected, f"the table is wrong: {why}"
        _, djust_out = render_both("{{ p|slugify }}", value)
        assert djust_out == expected, why

    def test_the_two_groups_are_genuinely_both_whitespace(self) -> None:
        """Guards the point: this is not "separators vs non-separators", it is
        two ``isspace()`` characters that the decomposition table sends
        different ways."""
        kept, dropped = "\u00a0", "\u2028"
        assert kept.isspace() and dropped.isspace()
        assert unicodedata.normalize("NFKD", kept) == " "
        assert unicodedata.normalize("NFKD", dropped) == dropped


# ---------------------------------------------------------------------------
# Randomized differential
# ---------------------------------------------------------------------------


class TestRandomizedDifferential:
    """Decomposed inputs generated PROGRAMMATICALLY, so no tool sits between
    the generator and the assertion."""

    MARKS = [ACUTE, DIAERESIS, CEDILLA, RING_ABOVE, "\u0323", "\u0331"]
    BASES = list("abcdeqxyzAEIOU") + ["é", "ü", "中", " ", ".", "-"]

    def _value(self, rng: random.Random) -> str:
        out = []
        for _ in range(rng.randint(0, 10)):
            out.append(rng.choice(self.BASES))
            for _ in range(rng.randint(0, 2)):
                out.append(rng.choice(self.MARKS))
        return "".join(out)

    @pytest.mark.parametrize(
        "source",
        [
            "{{ p|truncatechars:1 }}",
            "{{ p|truncatechars:3 }}",
            "{{ p|truncatechars:5 }}",
            "{{ p|truncatechars:12 }}",
            "{{ p|slugify }}",
            "{{ p|truncatewords:2 }}",
        ],
    )
    def test_no_divergence(self, source: str) -> None:
        rng = random.Random(hash(source) & 0xFFFF)
        divergences = []
        for _ in range(600):
            value = self._value(rng)
            django_out, djust_out = render_both(source, value)
            if django_out != djust_out:
                divergences.append((value, django_out, djust_out))
        assert not divergences, (
            f"{len(divergences)} of 600 diverge for {source}; first three: "
            f"{[(([hex(ord(c)) for c in v]), a, b) for v, a, b in divergences[:3]]}"
        )

    @pytest.mark.parametrize(
        "source", ["{{ p|truncatechars_html:5 }}", "{{ p|truncatechars_html:12 }}"]
    )
    def test_no_divergence_html(self, source: str) -> None:
        rng = random.Random(0xBEEF)
        divergences = []
        for _ in range(600):
            value = "<b>" + self._value(rng).replace("<", "") + "</b>"
            django_out, djust_out = render_both(source, value)
            if django_out != djust_out:
                divergences.append((value, django_out, djust_out))
        assert not divergences, (
            f"{len(divergences)} of 600 diverge for {source}; first three: {divergences[:3]}"
        )

    def test_the_corpus_actually_contains_decomposed_values(self) -> None:
        """Guards the guard: a generator that stopped emitting marks would make
        every sweep above pass vacuously."""
        rng = random.Random(1)
        values = [self._value(rng) for _ in range(600)]
        decomposed = [v for v in values if unicodedata.normalize("NFC", v) != v]
        assert len(decomposed) > 200, (
            f"only {len(decomposed)} of 600 generated values are decomposed"
        )
        with_marks = [v for v in values if any(unicodedata.combining(c) for c in v)]
        assert len(with_marks) > 300, (
            f"only {len(with_marks)} of 600 generated values carry a combining mark"
        )
