"""``wordwrap`` is ``textwrap.TextWrapper``, not a greedy re-joiner (#2293).

What was there
--------------
``crates/djust_templates/src/filters.rs::word_wrap`` was
``text.split_whitespace()`` re-joined on single spaces with a running width. That
is a different algorithm from Django's, which delegates to
``textwrap.TextWrapper``, and it diverged four ways at once: it flattened every
existing line break into a space, collapsed runs of spaces and dropped leading
indentation, measured widths in BYTES, and returned the text unchanged at
``width=0`` where Django raises ``ValueError``.

Why the byte defect could not be fixed alone
--------------------------------------------
#2279 swept ``word.len()`` → ``word.chars().count()`` on its own and measured it
fixing 21 differential cells and REGRESSING 6 — every regression a string
containing ``U+2028``. Django's ``splitlines()`` breaks a line there; the
re-joiner turned it into a space, and the byte overcount had been putting a
break at that position *by accident*. Two bugs cancelling, so the pair had to
move together. :meth:`TestTheCancellingPair` pins both halves and the cell the
issue reported.

How this is checked
-------------------
Not by a curated table. ``textwrap`` is in the standard library and Django is
imported here, so the reference is a call away, and
:meth:`TestRandomizedDifferential.test_randomized_sweep_against_django` renders
the same value through both engines over a randomized corpus × eight widths.
The corpus alphabet is built from the THREE whitespace sets the port has to keep
apart — ``str.splitlines()``, ``textwrap._whitespace`` and ``str.isspace()`` —
because every known defect in this filter lived in a gap between two of them and
an alphabet drawn from only one of the three cannot construct those cells.

The argument half, closed later by #2328
----------------------------------------
This file originally recorded ``int(arg)`` as a deliberate divergence: Django
raises for a non-numeric argument and djust kept a historical 75. #2328 closed
that — and not for ``wordwrap`` alone, because the same shape was at eleven
other dispatch arms, so every built-in that reads its argument as a number now
parses it at one chokepoint that raises exactly where Django's bare ``int()``
does. The same PR made an unresolvable bare-identifier argument raise
``VariableDoesNotExist``-style rather than arriving at the filter as its own
NAME. A PARSED width of <= 0 is a different question and raises for a different
reason — Django's own ``_wrap_chunks`` guard. :meth:`TestWidthArgument` pins
each of those separately.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils.text import wrap as django_wrap  # noqa: E402

from djust import _rust  # noqa: E402

_COMPILED: dict[str, DjangoTemplate] = {}


def _render_both(source: str, value: Any) -> tuple[str, str]:
    """``(django, djust)`` for one cell, through both real template engines."""
    template = _COMPILED.get(source)
    if template is None:
        template = _COMPILED[source] = DjangoTemplate(source)
    return (
        template.render(DjangoContext({"p": value})),
        _rust.render_template(source, {"p": value}),
    )


def _assert_agrees(source: str, value: Any) -> None:
    django_out, djust_out = _render_both(source, value)
    assert djust_out == django_out, (
        f"{source} on {value!r}: django={django_out!r} djust={djust_out!r}"
    )


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------
#
# Spelled with escapes rather than literals: an editor can NFC-normalize a
# literal on the way to disk, and several of these characters are invisible.
#
# The three sets, and which of them each entry belongs to, is the whole design
# of this alphabet — see the module docstring.
_ALPHABET: list[str] = [
    # words
    "a",
    "b",
    "Q",
    "0",
    "-",
    "--",
    ".",
    ",",
    "!",
    "'",
    '"',
    "&",
    "?",
    "ab",
    "abc",
    "abcd",
    "abcdefgh",
    "abcdefghijklmnop",
    "goof-ball",
    "e-mail",
    "a-b-c",
    "-x",
    "x-",
    # textwrap._whitespace, and the only two of the six that survive splitlines
    " ",
    "  ",
    "   ",
    "\t",
    "\t\t",
    # str.splitlines() boundaries
    "\n",
    "\r",
    "\r\n",
    "\x0b",
    "\x0c",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
    # str.isspace() only: splits nothing, yet strips to empty
    "\x1f",
    "\xa0",
    "\u2003",
    "\u3000",
    # the byte-vs-character axis
    "\xe9",
    "字",
    "日本語",
    "→",
    "\U0001f389",
    "\U0001f1fa\U0001f1f8",
    "e\u0301",
    "\u200b",
    # a hostile fragment, so the sweep also answers "does this stay escaped"
    "<img",
    "onerror=",
    "<b>",
]

#: Widths either side of every interesting boundary: 1 (every chunk is long),
#: the length of the shorter alphabet entries, and past the longest.
_WIDTHS = [1, 2, 3, 5, 8, 10, 20, 75]


def _values(seed: int = 22930, n: int = 420) -> list[str]:
    """Hand-written seams first, then a randomized tail."""
    seams = [
        "",
        " ",
        "\n",
        "\n\n",
        "a\n",
        "a\nb",
        "   ",
        "\t",
        "a\tb",
        "\ta",
        "a b",
        "f\u2028字日\U0001f1fa\U0001f1f80\U0001f389",
        "  leading indent kept",
        "a  b",
        "a \xa0 b",
        "\xa0",
        "\x1f",
        "a\x1fb",
        "Look, goof-ball -- use the -b option!",
        "supercalifragilistic expialidocious",
        "x" * 100,
        "word " * 12,
    ]
    rng = random.Random(seed)
    return seams + [
        "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 10))) for _ in range(n)
    ]


class TestTheCancellingPair:
    """The two defects #2279 measured as cancelling, and the cell it reported."""

    def test_the_reported_cell(self) -> None:
        # The issue's own example. The byte fix alone turned this from agreeing
        # into disagreeing; the pair together keeps it agreeing for the right
        # reason — `U+2028` is a line break, not a space.
        value = "f\u2028字日\U0001f1fa\U0001f1f80\U0001f389"
        assert django_wrap(value, 10) == "f\n字日\U0001f1fa\U0001f1f80\U0001f389"
        _assert_agrees("{{ p|wordwrap:10 }}", value)

    def test_half_one_a_u2028_is_a_line_break_not_a_space(self) -> None:
        # The re-joiner emitted a SPACE here, which is what accidentally
        # cancelled the byte overcount.
        assert _rust.render_template("{{ p|wordwrap:10 }}", {"p": "a\u2028b"}) == "a\nb"
        _assert_agrees("{{ p|wordwrap:10 }}", "a\u2028b")

    def test_half_two_the_width_counts_characters_not_bytes(self) -> None:
        # Four 3-byte characters at width 4: one break if the count is in
        # characters, three if it is in bytes.
        assert _rust.render_template("{{ p|wordwrap:4 }}", {"p": "字 日 本 語"}) == "字 日\n本 語"
        _assert_agrees("{{ p|wordwrap:4 }}", "字 日 本 語")

    @pytest.mark.parametrize(
        ("value", "width"),
        [
            # Every one of these agreed with Django ONLY because two defects
            # cancelled, or disagreed because of one of them.
            ("a\u2028b", 1),
            ("a\u2028b", 3),
            ("字\u2028字", 2),
            ("f\u2028字", 10),
            ("a\u2029b", 4),
            ("\xe9 \xe9 \xe9", 3),
        ],
    )
    def test_neither_half_is_load_bearing_for_the_other(self, value: str, width: int) -> None:
        _assert_agrees("{{ p|wordwrap:%d }}" % width, value)


class TestWhatTheRejoinerDestroyed:
    """`wrap` PRESERVES everything it does not have to break."""

    @pytest.mark.parametrize(
        ("value", "width", "expected"),
        [
            # Existing line breaks: each line is wrapped independently.
            ("a\nb", 10, "a\nb"),
            ("one two\nthree four", 20, "one two\nthree four"),
            # Runs of spaces are interior whitespace, not separators to collapse.
            ("a  b", 10, "a  b"),
            ("a     b", 10, "a     b"),
            # Leading indentation survives on the FIRST line of each input line.
            ("    indented", 40, "    indented"),
            ("  aaa bbb", 5, "  aaa\nbbb"),
            # A whitespace-only line is restored rather than emptied.
            ("   ", 5, "   "),
            ("a\n   \nb", 5, "a\n   \nb"),
            # A trailing newline is re-appended.
            ("a\n", 5, "a\n"),
            # Tabs expand to the next multiple of eight before anything else.
            ("a\tb", 40, "a       b"),
            # A long word is never broken (`break_long_words=False`).
            ("aaaaaaaa b", 3, "aaaaaaaa\nb"),
            # ... and never broken on a hyphen either (`break_on_hyphens=False`).
            ("goof-ball", 5, "goof-ball"),
        ],
    )
    def test_case(self, value: str, width: int, expected: str) -> None:
        assert django_wrap(value, width) == expected, "the expectation is Django's"
        _assert_agrees("{{ p|wordwrap:%d }}" % width, value)


class TestTheThreeWhitespaceSets:
    """One named case per pair of sets that could be confused for each other.

    Without these, three of the port's mechanisms went red under gate-off only
    in the randomized sweep — true, but it names no behaviour. Each case here
    is a cell one specific confusion gets wrong.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # `str.splitlines()` breaks on ten things; `split("\n")` on one.
            ("a\rb", "a\nb"),
            ("a\x0bb", "a\nb"),
            ("a\x0cb", "a\nb"),
            ("a\x1cb", "a\nb"),
            ("a\x1db", "a\nb"),
            ("a\x1eb", "a\nb"),
            ("a\x85b", "a\nb"),
            ("a\u2028b", "a\nb"),
            ("a\u2029b", "a\nb"),
            # `\r\n` is ONE boundary, not two — treating it as two puts a blank
            # line between the halves.
            ("a\r\nb", "a\nb"),
            # `\x1f` is `isspace` but NOT a splitlines boundary, so it stays.
            ("a\x1fb", "a\x1fb"),
        ],
    )
    def test_what_counts_as_a_line(self, value: str, expected: str) -> None:
        assert django_wrap(value, 10) == expected, "the expectation is Django's"
        _assert_agrees("{{ p|wordwrap:10 }}", value)

    def test_the_splitter_breaks_on_textwraps_set_and_not_on_isspace(self) -> None:
        # `\u{a0}` is `isspace` but is NOT a chunk boundary, so `aa\xa0bb` is ONE
        # five-character chunk and survives a width of 3 intact. A splitter that
        # used `isspace` would make three chunks and emit "aa\nbb".
        assert django_wrap("aa\xa0bb", 3) == "aa\xa0bb"
        _assert_agrees("{{ p|wordwrap:3 }}", "aa\xa0bb")

    @pytest.mark.parametrize("blank", ["\xa0", "\x1f", "\u3000"])
    def test_drop_whitespace_uses_isspace_and_not_textwraps_set(self, blank: str) -> None:
        # ... and yet the same character, standing alone as a chunk, IS dropped
        # at a line break, because `drop_whitespace` tests `chunk.strip() == ''`.
        # The ordinary space before it survives: only ONE chunk is dropped.
        value = f"a {blank} b"
        assert django_wrap(value, 3) == "a \nb"
        _assert_agrees("{{ p|wordwrap:3 }}", value)


class TestWidthArgument:
    """`int(arg)` <= 0 raises; an unparseable arg deliberately does not."""

    @pytest.mark.parametrize("width", ["0", "-1", "-75"])
    def test_a_non_positive_width_raises_djangos_message(self, width: str) -> None:
        source = '{{ p|wordwrap:"%s" }}' % width
        with pytest.raises(ValueError) as django_exc:
            DjangoTemplate(source).render(DjangoContext({"p": "a b"}))
        with pytest.raises(RuntimeError) as djust_exc:
            _rust.render_template(source, {"p": "a b"})
        # djust's template errors surface as `RuntimeError("Template error: ...")`
        # rather than the original Python exception type — a property of the
        # engine boundary, not of this filter. The MESSAGE is Django's verbatim.
        assert str(django_exc.value) in str(djust_exc.value)
        assert str(django_exc.value) == f"invalid width {width} (must be > 0)"

    def test_the_empty_string_is_the_one_input_that_does_not_raise(self) -> None:
        # `wrap` raises inside the per-line loop, and `"".splitlines()` is empty,
        # so the loop body never runs. Django agrees.
        assert django_wrap("", 0) == ""
        assert _rust.render_template('{{ p|wordwrap:"0" }}', {"p": ""}) == ""
        # One line is enough to reach the guard.
        with pytest.raises(RuntimeError):
            _rust.render_template('{{ p|wordwrap:"0" }}', {"p": "\n"})

    def test_an_unparseable_width_raises_in_both_engines(self) -> None:
        # This used to pin the OPPOSITE — djust wrapped at its historical 75
        # where Django raised — as a deliberate divergence. #2328 closed it for
        # every argument-taking filter at once, so the two now agree that
        # `int("nope")` has no answer.
        with pytest.raises(ValueError):
            DjangoTemplate('{{ p|wordwrap:"nope" }}').render(DjangoContext({"p": "a"}))
        with pytest.raises(RuntimeError, match="needs an integer argument"):
            _rust.render_template('{{ p|wordwrap:"nope" }}', {"p": "word " * 30})

    def test_a_width_with_surrounding_space_parses_as_django_parses_it(self) -> None:
        _assert_agrees('{{ p|wordwrap:" 5 " }}', "aaa bbb ccc")


class TestRandomizedDifferential:
    """The load-bearing check: the same cell through both engines, in bulk."""

    def test_randomized_sweep_against_django(self) -> None:
        values = _values()
        cells = 0
        bad: list[str] = []
        for width in _WIDTHS:
            source = "{{ p|wordwrap:%d }}" % width
            for value in values:
                cells += 1
                django_out, djust_out = _render_both(source, value)
                if django_out != djust_out:
                    bad.append(
                        f"width={width} value={value!r} django={django_out!r} djust={djust_out!r}"
                    )
        # A mechanical count, not a prose one: the product is what it is.
        assert cells == len(_WIDTHS) * len(values)
        assert cells > 3000, f"the sweep shrank to {cells} cells"
        assert not bad, "%d/%d cells disagree:\n  %s" % (
            len(bad),
            cells,
            "\n  ".join(bad[:20]),
        )

    def test_the_sweep_would_notice_a_wrong_answer(self) -> None:
        """The sweep is not vacuous: a cell it covers can disagree.

        Renders the corpus through a DELIBERATELY wrong width and requires the
        comparison to report it — so a `render_both` that silently returned two
        copies of the same string could not pass.
        """
        disagreements = 0
        for value in _values()[:60]:
            django_out = DjangoTemplate("{{ p|wordwrap:5 }}").render(DjangoContext({"p": value}))
            djust_out = _rust.render_template("{{ p|wordwrap:4 }}", {"p": value})
            if django_out != djust_out:
                disagreements += 1
        assert disagreements > 0, (
            "comparing width 5 against width 4 found no disagreement — "
            "the harness is not comparing two independent engines"
        )


class TestThisFileCannotBeFlattened:
    """No invisible character may appear as a LITERAL in this file.

    Not a style rule. Writing this suite, an edit silently replaced two `U+2028`
    literals with ordinary spaces, and the two cases they were the whole point
    of went GREEN for the wrong reason — a `U+2028` case that no longer contains
    a `U+2028` asserts nothing. The characters this filter is about are
    invisible, so nothing about the diff would have shown it.
    """

    #: Every character the corpus cares about that a reader cannot see, plus the
    #: combining mark, which normalization is what silently changes.
    _INVISIBLE = "\u2028\u2029\xa0\u2003\u3000\u200b\x0b\x0c\x1c\x1d\x1e\x1f\x85\r\u0301"

    def test_no_invisible_character_appears_as_a_literal(self) -> None:
        import pathlib

        source = pathlib.Path(__file__).read_bytes().decode("utf-8")
        # `\n` and `\t` are excluded: a source file is made of them.
        found = sorted(
            f"U+{ord(c):04X}" for c in self._INVISIBLE if c in source and c not in "\n\t"
        )
        assert not found, (
            f"{found} appear as literals in this file. Spell them as escapes "
            "(\\u2028, \\xa0, ...) — an editor or an automated edit can replace "
            "an invisible literal with a space, and the case that depended on it "
            "then passes for the wrong reason."
        )

    def test_the_alphabet_and_the_seams_still_carry_the_characters(self) -> None:
        """... and the escapes are still THERE, which the rule above cannot say."""
        corpus = "".join(_ALPHABET) + "".join(_values(n=0))
        for char in "\u2028\u2029\xa0\x1f\u3000\x85":
            assert char in corpus, f"U+{ord(char):04X} left the corpus"


class TestEscapingIsUnchanged:
    """The port PRESERVES more of the input than the re-joiner did (#2293)."""

    @pytest.mark.parametrize(
        "value",
        [
            "<img\nsrc=x onerror=alert(1)>",
            "</script>\u2028<script>alert(1)</script>",
            "a < b",
            '" onmouseover="x',
            "<b>\t<i>",
        ],
    )
    def test_a_hostile_payload_is_escaped_exactly_as_django_escapes_it(self, value: str) -> None:
        # The re-joiner collapsed `<img\nsrc=x>` to `<img src=x>`; the port keeps
        # the newline. Both are escaped, and the bar is Django's own output —
        # not "nothing is live" — so this is a parity check, not a weaker one.
        for width in (3, 10, 75):
            _assert_agrees("{{ p|wordwrap:%d }}" % width, value)
            _assert_agrees("{{ p|wordwrap:%d|escape }}" % width, value)
            _assert_agrees("{{ p|escape|wordwrap:%d }}" % width, value)
