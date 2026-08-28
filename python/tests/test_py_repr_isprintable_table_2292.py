"""`py_repr_string` escapes non-ASCII non-printables (#2292).

The gap
-------
CPython's `repr()` escapes every code point for which `str.isprintable()` is
false. djust's escaper stopped at ASCII C0 + DEL, so `U+00A0`, `U+200B`,
`U+2028`, `U+2029`, `U+FEFF` and every private-use code point rendered
LITERALLY where CPython writes `\\xa0` / `\\u200b` / `\\u2028`.

Why it was thought unfixable, and why it is not
-----------------------------------------------
`str.isprintable()` is Unicode-version data, and this project's supported
interpreters disagree about it. #2292 measured 5812 disagreeing code points
between Unicode 15.0 and 16.0 and concluded no fixed Rust table could be green
on every runner.

The premise is right and understated; the conclusion does not follow.
Re-measured across the WHOLE supported matrix (3.10-3.14, five different
Unicode versions -- #2292 missed that 3.13 carries 15.1, not 15.0), the
disagreement is **11130** code points. But every one of those 11130 went from
NON-printable to printable, i.e. from unassigned (`Cn`) to assigned. Zero went
the other way, and nothing already assigned was ever reclassified.

So `not str.isprintable()` splits into a stable part -- the seven general
categories `Cc`, `Cf`, `Cs`, `Co`, `Zl`, `Zp`, `Zs`, which over 13.0 -> 16.0
gained exactly ONE already-assigned member (`U+0020 SPACE`, which Python
special-cases as printable anyway) -- and `Cn`, which is the entire moving
part. Escaping the seven categories and treating `Cn` as printable is exact
for every assigned code point on every interpreter, and is version-INDEPENDENT
so djust answers identically on all five. The residual is the unassigned
space, which is where the interpreters already disagree with each other and
which no fixed table can get right.

And the stable part is small: 139769 code points, but only **28 ranges**,
because private use is three contiguous blocks. That is a hand-written table,
not a Unicode-general-category dependency. (#2319, the sibling issue, is the
one that genuinely needs a crate: NFC/NFKD are not expressible as a range
table.)

What this file pins
-------------------
Two independent mechanisms, each with a test that goes red when only that
mechanism is removed (per the shadowing rule in CLAUDE.md):

* `TestTableMatchesTheRunningInterpreter` -- the TABLE is right. It recomputes
  the seven categories from the RUNNING interpreter's own `unicodedata` and
  compares against the table parsed out of the Rust source, over every code
  point that interpreter has assigned. This adapts to whatever Unicode version
  the runner carries instead of hardcoding one, so it is the check that would
  fail on a future CPython that reclassifies something.
* `TestRangeBoundariesThroughTheRealRenderPath` and friends -- the table is
  actually WIRED UP. A correct table the escaper never consults would sail
  through the check above.

Every code-point count quoted in `py_repr_string`'s doc comment is re-derived
here rather than restated (`TestTheDocCommentsCountsAreTrue`), because a
hand-copied count is exactly the thing that rots.

No combining-mark or decomposed literals appear in this file -- every non-ASCII
value is written as an explicit `\\uXXXX` escape. See #2319 for why: editors
and agent file-writers NFC-normalize literals on the way to disk.
"""

from __future__ import annotations

import pprint as _pprint
import random
import re
import unicodedata
from pathlib import Path

import pytest

pytest.importorskip("django")

from djust import _rust  # noqa: E402

from test_length_pprint_parity_2279_2277 import render_both  # noqa: E402

# The seven general categories CPython treats as non-printable, minus `Cn`
# (unassigned) which is the version-dependent part this port deliberately does
# not carry. `str.isprintable()` is false for exactly these plus `Cn`, except
# that U+0020 SPACE is printable despite being `Zs`.
SEVEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp", "Zs"})
MAX_CODE_POINT = 0x110000

RUST_SOURCE = Path(__file__).resolve().parents[2] / "crates" / "djust_core" / "src" / "lib.rs"


def parse_rust_table() -> list[tuple[int, int]]:
    """The `NON_PRINTABLE` ranges, read out of the Rust source.

    A source pin rather than a re-implementation: the point is to compare the
    SHIPPED table against `unicodedata`, so re-deriving it in Python would
    compare Python to Python and prove nothing.
    """
    src = RUST_SOURCE.read_text(encoding="utf-8")
    match = re.search(
        r"const NON_PRINTABLE: \[\(u32, u32\); (\d+)\] = \[(.*?)\n\];",
        src,
        re.DOTALL,
    )
    assert match is not None, f"NON_PRINTABLE table not found in {RUST_SOURCE}"
    declared_len = int(match.group(1))
    ranges = [
        (int(lo, 16), int(hi, 16))
        for lo, hi in re.findall(r"\(0x([0-9A-Fa-f]+), 0x([0-9A-Fa-f]+)\)", match.group(2))
    ]
    assert len(ranges) == declared_len, (
        f"the array's declared length {declared_len} does not match the "
        f"{len(ranges)} entries actually in it"
    )
    return ranges


RUST_RANGES = parse_rust_table()


def in_rust_table(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in RUST_RANGES)


def expected_repr(value: str) -> str:
    """CPython's `repr`, which is the spelling `{{ list }}` renders."""
    return repr(value)


def expected_pformat(value: str) -> str:
    """CPython's `pprint.pformat`, which is what the `pprint` filter IS.

    Distinct from `expected_repr` because `pformat` WRAPS at width 80, and an
    escaped string reaches that width in far fewer characters than the raw one
    does -- `\\U000f0001` is ten columns for one code point. The randomized
    sweep below hit exactly that: values whose escaped form wrapped, compared
    against an unwrapped `repr`, reported 231 false divergences that were
    entirely layout. The spelling is this file's subject; the layout is
    `test_length_pprint_parity_2279_2277`'s.
    """
    return _pprint.pformat(value)


def render_repr(value: str) -> str:
    """djust's `pprint` of a bare string, which is `py_repr_string` verbatim."""
    return _rust.render_template("{{ p|pprint|safe }}", {"p": value})


# ---------------------------------------------------------------------------
# The issue's reproducers
# ---------------------------------------------------------------------------


class TestTheIssuesReproducers:
    """The six values pinned as KNOWN-WRONG in `TestKnownResidualDivergences`.

    That pin asserted djust still disagreed with Django and fired a "now
    AGREES -- delete this row" message when closed. This class is what closed
    it.
    """

    @pytest.mark.parametrize(
        ("value", "want"),
        [
            ("\xa0", "'\\xa0'"),  # Zs  NO-BREAK SPACE
            ("\u200b", "'\\u200b'"),  # Cf  ZERO WIDTH SPACE
            ("\u2028", "'\\u2028'"),  # Zl  LINE SEPARATOR
            ("\u2029", "'\\u2029'"),  # Zp  PARAGRAPH SEPARATOR
            ("\ufeff", "'\\ufeff'"),  # Cf  ZERO WIDTH NO-BREAK SPACE
            ("\ue000", "'\\ue000'"),  # Co  private use
        ],
    )
    def test_now_escaped_like_cpython(self, value: str, want: str) -> None:
        # The table states CPython's answer, so a wrong expectation fails here
        # rather than silently agreeing with a wrong port.
        assert _pprint.pformat(value) == want, "the table's expectation is wrong"
        assert render_repr(value) == want

    @pytest.mark.parametrize(
        "value",
        ["\xa0", "\u200b", "\u2028", "\u2029", "\ufeff", "\ue000"],
    )
    def test_the_full_django_render_now_agrees(self, value: str) -> None:
        django_out, djust_out = render_both("{{ p|pprint }}", value)
        assert django_out == djust_out

    def test_the_issue_headline_case(self) -> None:
        """`{{ p|pprint }}` on U+2028, the example in the issue body."""
        assert _pprint.pformat("\u2028") == "'\\u2028'"
        assert render_repr("\u2028") == "'\\u2028'"

    def test_the_residual_was_the_escape_and_not_the_layout(self) -> None:
        """The inverse of the pin this replaces.

        `test_the_residual_is_the_escape_and_not_the_layout` proved the wrap
        points already agreed and only the spelling differed, by substituting
        the literal code point back into Django's output. Now that the
        spelling is fixed, the two must agree with no substitution at all --
        which is what makes this the closing case for that pin rather than a
        different test.
        """
        value = ("word " * 10) + "\u2028" + ("word " * 10)
        django_out, djust_out = render_both("{{ p|pprint }}", value)
        assert django_out == djust_out
        assert "\n" in djust_out, "the case does not reach the wrapping path"
        assert "\\u2028" in djust_out


# ---------------------------------------------------------------------------
# Mechanism 1 -- the table is right
# ---------------------------------------------------------------------------


class TestTableMatchesTheRunningInterpreter:
    """Recompute the seven categories from THIS interpreter and compare.

    This is the check that adapts across the CI matrix: it never names a
    Unicode version, it asks the interpreter it is running on.
    """

    def test_table_equals_the_seven_categories_minus_space(self) -> None:
        expected = {
            cp
            for cp in range(MAX_CODE_POINT)
            if unicodedata.category(chr(cp)) in SEVEN_CATEGORIES and cp != 0x20
        }
        actual = {cp for lo, hi in RUST_RANGES for cp in range(lo, hi + 1)}

        # Anything the table has but this interpreter does not put in the seven
        # categories must be UNASSIGNED here -- i.e. assigned by a later
        # Unicode version than this runner carries. That is the documented,
        # bounded residual; anything else is a real table error.
        for cp in sorted(actual - expected):
            category = unicodedata.category(chr(cp))
            assert category == "Cn", (
                f"U+{cp:04X} is in the shipped table but this interpreter "
                f"(Unicode {unicodedata.unidata_version}) classifies it {category}, "
                f"which is neither one of the seven categories nor unassigned"
            )

        # The reverse direction admits no exceptions: if this interpreter says
        # a code point is in the seven categories, the table must have it.
        # Category membership is stable once assigned, so a miss here is a
        # genuine hole rather than version drift.
        missing = sorted(expected - actual)
        assert not missing, (
            f"{len(missing)} code points are in the seven categories on this "
            f"interpreter but absent from the table, first few: "
            f"{[hex(cp) for cp in missing[:10]]}"
        )

    def test_the_table_reproduces_isprintable_for_every_assigned_code_point(
        self,
    ) -> None:
        """The load-bearing claim of the whole fix, checked exhaustively.

        For every code point THIS interpreter has assigned, membership in the
        table must equal `not str.isprintable()`. The residual is allowed to be
        the unassigned space and nothing else.
        """
        mismatches = []
        for cp in range(MAX_CODE_POINT):
            char = chr(cp)
            if unicodedata.category(char) == "Cn":
                continue  # the documented residual
            if in_rust_table(cp) != (not char.isprintable()):
                mismatches.append(cp)
        assert not mismatches, (
            f"{len(mismatches)} ASSIGNED code points where the table disagrees "
            f"with str.isprintable() on Unicode {unicodedata.unidata_version}: "
            f"{[hex(cp) for cp in mismatches[:20]]}"
        )

    def test_space_is_the_one_deliberate_exclusion(self) -> None:
        """U+0020 is `Zs` but printable -- the sole special case."""
        assert unicodedata.category(" ") == "Zs"
        assert " ".isprintable()
        assert not in_rust_table(0x20)
        assert render_repr("a b") == "'a b'"

    def test_ranges_are_sorted_and_disjoint(self) -> None:
        """The Rust lookup is a binary search, which requires both."""
        for (lo, hi), (next_lo, _) in zip(RUST_RANGES, RUST_RANGES[1:]):
            assert lo <= hi, f"range (0x{lo:X}, 0x{hi:X}) is inverted"
            assert hi < next_lo, (
                f"range ending 0x{hi:X} overlaps or abuts the next starting "
                f"0x{next_lo:X}; abutting ranges should have been merged"
            )
        last_lo, last_hi = RUST_RANGES[-1]
        assert last_lo <= last_hi


class TestTheDocCommentsCountsAreTrue:
    """Every count in `py_repr_string`'s doc comment, re-derived.

    A prose count that nothing recomputes is a count that drifts. The doc
    quotes a per-interpreter printable-code-point table; this asserts the row
    for whatever interpreter is running.
    """

    def _doc(self) -> str:
        return RUST_SOURCE.read_text(encoding="utf-8")

    def test_the_printable_count_for_this_interpreter_matches_the_doc_table(
        self,
    ) -> None:
        version = unicodedata.unidata_version
        rows = re.findall(r"^/// \| 3\.\d+ \| ([\d.]+) \| (\d+) \|$", self._doc(), re.MULTILINE)
        assert rows, "the per-interpreter table is missing from the doc comment"
        table = {unicode_version: int(count) for unicode_version, count in rows}
        assert version in table, (
            f"this interpreter carries Unicode {version}, which the doc "
            f"comment's table does not list (it has {sorted(table)}) -- add the row"
        )
        actual = sum(1 for cp in range(MAX_CODE_POINT) if chr(cp).isprintable())
        assert actual == table[version], (
            f"doc says {table[version]} printable code points on Unicode "
            f"{version}; this interpreter counts {actual}"
        )

    def test_the_range_and_code_point_counts_match_the_table(self) -> None:
        doc = self._doc()
        assert "139769 code points collapse\n/// to 28 ranges" in doc, (
            "the doc comment's own summary of the table changed shape"
        )
        assert len(RUST_RANGES) == 28
        assert sum(hi - lo + 1 for lo, hi in RUST_RANGES) == 139769

    def test_the_eleven_thousand_figure_is_the_matrix_spread(self) -> None:
        """11130 is |printable(13.0) XOR printable(16.0)|.

        Only one interpreter is running, so this checks the arithmetic the doc
        states rather than recomputing both ends: the doc's own table must be
        internally consistent with the spread it claims.
        """
        doc = self._doc()
        rows = re.findall(r"^/// \| 3\.\d+ \| ([\d.]+) \| (\d+) \|$", doc, re.MULTILINE)
        counts = [int(count) for _, count in rows]
        assert counts == sorted(counts), (
            "printability only ever grew across versions; the doc's table is "
            "not monotonic, so one of its numbers is wrong"
        )
        assert "**11130**" in doc
        assert max(counts) - min(counts) == 11130, (
            f"the doc claims a spread of 11130 but its own table spans {max(counts) - min(counts)}"
        )


# ---------------------------------------------------------------------------
# Mechanism 2 -- the table is actually consulted
# ---------------------------------------------------------------------------


class TestRangeBoundariesThroughTheRealRenderPath:
    """Every range edge, rendered through the real filter.

    A correct table that the escaper never consults passes every test in
    `TestTableMatchesTheRunningInterpreter`. This is the half that proves the
    wiring, and it walks all four boundary positions of all 28 ranges so an
    off-by-one in the binary search cannot hide in an untested edge.
    """

    @staticmethod
    def _boundary_code_points() -> list[int]:
        points = []
        for lo, hi in RUST_RANGES:
            points.extend([lo - 1, lo, hi, hi + 1])
        return [
            cp
            for cp in points
            # Surrogates cannot be represented in a Rust `char`, and cannot
            # cross the PyO3 boundary, so they are not renderable at all.
            if 0 <= cp < MAX_CODE_POINT and not (0xD800 <= cp <= 0xDFFF)
        ]

    def test_every_range_edge_matches_cpython(self) -> None:
        checked = skipped_unassigned = 0
        for cp in self._boundary_code_points():
            value = chr(cp)
            if unicodedata.category(value) == "Cn":
                # The `lo - 1` edge of a range is frequently unassigned, which
                # is the documented residual rather than a defect: CPython
                # escapes it, this port does not. Asserted positively in
                # `TestUnassignedCodePointsAreTheDocumentedResidual`.
                skipped_unassigned += 1
                continue
            want = expected_pformat(value)
            assert render_repr(value) == want, (
                f"U+{cp:04X} (category {unicodedata.category(value)}): "
                f"djust={render_repr(value)!r} cpython={want!r}"
            )
            checked += 1
        # 28 ranges x 4 edges, less surrogates and unassigned neighbours.
        assert checked >= 70, (
            f"only {checked} boundary code points were checked "
            f"({skipped_unassigned} skipped as unassigned)"
        )

    def test_the_boundary_set_covers_every_range(self) -> None:
        """The count-canary for the sweep above: it must walk ALL the ranges,
        not a prefix of them."""
        covered = {
            (lo, hi)
            for lo, hi in RUST_RANGES
            for cp in self._boundary_code_points()
            if lo <= cp <= hi
        }
        assert len(covered) == len(RUST_RANGES) == 28


class TestEscapeWidthMatchesCPython:
    """CPython picks `\\xNN`, `\\uNNNN` or `\\UNNNNNNNN` by magnitude."""

    @pytest.mark.parametrize(
        ("value", "want"),
        [
            ("\x00", "'\\x00'"),
            ("\x1b", "'\\x1b'"),
            ("\x7f", "'\\x7f'"),
            ("\x80", "'\\x80'"),
            ("\x9f", "'\\x9f'"),
            ("\xa0", "'\\xa0'"),  # last of the \xNN form
            ("\xad", "'\\xad'"),
            ("\u0600", "'\\u0600'"),  # first of the \uNNNN form
            ("\ufeff", "'\\ufeff'"),
            ("\ufffb", "'\\ufffb'"),  # last of the \uNNNN form
            ("\U000110bd", "'\\U000110bd'"),  # first of the \UNNNNNNNN form
            ("\U000e0001", "'\\U000e0001'"),
            ("\U0010fffd", "'\\U0010fffd'"),
        ],
    )
    def test_width(self, value: str, want: str) -> None:
        assert expected_repr(value) == want, "the table's expectation is wrong"
        assert render_repr(value) == want

    def test_hex_digits_are_lowercase(self) -> None:
        assert render_repr("\u200b") == "'\\u200b'"
        assert "\\u200B" not in render_repr("\u200b")

    def test_named_escapes_still_win_over_the_numeric_form(self) -> None:
        """`\\t`, `\\n`, `\\r` are `Cc` and in the table, but CPython spells
        them with their short names, so the named arms must be matched first."""
        for value, want in [("a\tb", "'a\\tb'"), ("a\nb", "'a\\nb'"), ("a\rb", "'a\\rb'")]:
            assert expected_repr(value) == want
            assert render_repr(value) == want

    def test_quote_selection_still_holds_with_escapes_present(self) -> None:
        """The quote rule reads the RAW string, so a non-printable alongside a
        quote must not perturb it."""
        for value in ["a'b\u200b", 'a"b\u200b', "a'b\"c\u200b", "\u200b\\x"]:
            assert render_repr(value) == expected_repr(value), repr(value)


class TestContainersUseTheSameEscaper:
    """`py_repr_string` is the ONE definition (#1646) -- the `{{ list }}` path
    and the `pprint` path must both have picked the fix up."""

    @pytest.mark.parametrize("value", ["\xa0", "\u200b", "\u2028", "\ue000"])
    def test_in_a_list(self, value: str) -> None:
        django_out, djust_out = render_both("{{ p|pprint }}", [value])
        assert django_out == djust_out
        # Not merely equal to each other: the UNESCAPED render must equal
        # CPython's own answer, so a shared wrong spelling could not pass.
        # (`render_both` autoescapes, which turns the repr's quotes into
        # `&#x27;` -- agreement there says nothing about the spelling.)
        raw = _rust.render_template("{{ p|pprint|safe }}", {"p": [value]})
        assert raw == _pprint.pformat([value])

    @pytest.mark.parametrize("value", ["\xa0", "\u200b", "\u2028", "\ue000"])
    def test_in_a_bare_list_render(self, value: str) -> None:
        django_out, djust_out = render_both("{{ p }}", [value])
        assert django_out == djust_out

    @pytest.mark.parametrize("value", ["\xa0", "\u200b", "\u2028", "\ue000"])
    def test_in_a_dict_value_and_key(self, value: str) -> None:
        for payload in ({"k": value}, {value: "v"}):
            django_out, djust_out = render_both("{{ p|pprint }}", payload)
            assert django_out == djust_out, repr(payload)

    def test_the_two_callers_agree_on_the_same_value(self) -> None:
        value = "\u2028\xa0\u200b"
        via_pprint = render_repr(value)
        via_list = _rust.render_template("{{ p|safe }}", {"p": [value]})
        assert via_pprint == expected_repr(value)
        assert via_list == f"[{expected_repr(value)}]"


# ---------------------------------------------------------------------------
# Randomized differential -- the check a curated table cannot be
# ---------------------------------------------------------------------------


class TestRandomizedDifferential:
    """Random strings mixing printable and non-printable code points.

    The curated tables above sample the code points someone thought of. This
    samples the ones nobody did -- which is how the drain's other parity ports
    found defects a table had missed.
    """

    @staticmethod
    def _alphabet() -> list[str]:
        pool: list[str] = []
        # Assigned non-printables, drawn from the table itself.
        for lo, hi in RUST_RANGES:
            for cp in range(lo, min(hi + 1, lo + 40)):
                if 0xD800 <= cp <= 0xDFFF:
                    continue
                if unicodedata.category(chr(cp)) == "Cn":
                    continue  # documented residual, excluded by construction
                pool.append(chr(cp))
        # Ordinary printable text, plus the characters with their own escape
        # arms, so their interaction is exercised too.
        pool.extend(list("abcXYZ019 .,-_中é\U0001f44d"))
        pool.extend(["'", '"', "\\", "\t", "\n", "\r"])
        return pool

    def test_no_divergence_from_cpython_pformat(self) -> None:
        rng = random.Random(20292)
        pool = self._alphabet()
        divergences = []
        for _ in range(3000):
            value = "".join(rng.choice(pool) for _ in range(rng.randint(0, 12)))
            got = render_repr(value)
            want = expected_pformat(value)
            if got != want:
                divergences.append((value, got, want))
        assert not divergences, (
            f"{len(divergences)} of 3000 diverge; first three: {divergences[:3]}"
        )

    def test_short_values_agree_with_bare_repr_too(self) -> None:
        """`pformat` and `repr` coincide below the wrap width, so the sweep
        above is pinned against `repr` as well wherever that is meaningful --
        otherwise a wrapping bug could mask a spelling bug."""
        rng = random.Random(452292)
        pool = self._alphabet()
        checked = 0
        for _ in range(1500):
            value = "".join(rng.choice(pool) for _ in range(rng.randint(0, 4)))
            want = expected_repr(value)
            if len(want) > 70:
                continue  # would wrap; not this test's subject
            assert render_repr(value) == want, repr(value)
            checked += 1
        assert checked > 1000, f"only {checked} values were short enough to check"

    def test_no_divergence_from_django_through_the_filter(self) -> None:
        """The same sweep through the real Django template path, which is what
        a user actually hits."""
        rng = random.Random(920292)
        pool = self._alphabet()
        divergences = []
        for _ in range(1200):
            value = "".join(rng.choice(pool) for _ in range(rng.randint(0, 10)))
            django_out, djust_out = render_both("{{ p|pprint }}", value)
            if django_out != djust_out:
                divergences.append((value, django_out, djust_out))
        assert not divergences, (
            f"{len(divergences)} of 1200 diverge; first three: {divergences[:3]}"
        )

    def test_the_sweep_actually_contains_non_printables(self) -> None:
        """Guards the guard: a pool that drifted to pure ASCII would make both
        sweeps above pass vacuously."""
        pool = self._alphabet()
        non_printable = [c for c in pool if not c.isprintable()]
        assert len(non_printable) > 50, (
            f"only {len(non_printable)} non-printable characters in the pool"
        )


class TestUnassignedCodePointsAreTheDocumentedResidual:
    """The one thing this port deliberately does NOT reproduce.

    Stated as a test rather than only in prose so that the day a future
    Unicode version assigns one of these, the change is visible.
    """

    def test_an_unassigned_code_point_is_emitted_literally(self) -> None:
        # U+0378 has been unassigned in every Unicode version to date.
        cp = 0x0378
        if unicodedata.category(chr(cp)) != "Cn":
            pytest.skip(f"U+{cp:04X} became assigned in this Unicode version")
        assert not chr(cp).isprintable(), "CPython escapes unassigned code points"
        assert render_repr(chr(cp)) == f"'{chr(cp)}'"
        assert not in_rust_table(cp)

    def test_the_residual_is_confined_to_unassigned_code_points(self) -> None:
        """Nothing ASSIGNED is left unescaped -- the complement of the above,
        and the reason the residual is acceptable."""
        leaked = [
            cp
            for cp in range(MAX_CODE_POINT)
            if unicodedata.category(chr(cp)) != "Cn"
            and not chr(cp).isprintable()
            and not in_rust_table(cp)
        ]
        assert not leaked, (
            f"{len(leaked)} assigned non-printable code points are not escaped: "
            f"{[hex(cp) for cp in leaked[:20]]}"
        )
