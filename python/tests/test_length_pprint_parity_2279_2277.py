"""`length` counts code points (#2279) and `pprint` wraps at width 80 (#2277).

Two defects in the same family: a Python *semantic* djust did not reproduce,
rather than a filter algorithm it got subtly wrong.

`length` counted BYTES
----------------------
`str::len()` in Rust is a byte count; `len()` in Python is a code-point count.
`é` is 2 bytes and `中` is 3, so every non-ASCII string measured long --
`{{ "中<b"|length }}` gave 5 where Django gives 3.

**Code points, not graphemes**, which is the trap the fix could have walked
into by "improving" on Python: `len("👍🏽")` is 2 (the thumb plus its skin-tone
modifier) and `len("👨‍👩‍👧")` is 5 (three emoji plus two joiners). A
grapheme-cluster count would answer 1 to both and be a *different* wrong
answer. Rust's `char` is a Unicode scalar value, so `chars().count()` is
Python's answer exactly.

The bug had been masked in the #2273 `striptags` sweep: the old `striptags`
deleted everything after a lone `<`, so `"中<b"` became `"中"` -- 3 bytes, which
coincidentally matched Django's char count of the *unstripped* value. Fixing
`striptags` unmasked it in 216 cells. Two bugs cancelling, the #2272 pattern.

`pprint` never wrapped
----------------------
Django's `pprint` filter is `pprint.pformat(value)`, whose default `width=80`
**wraps**: past that width it breaks the structure across lines with hanging
indentation. The filter built one line and never wrapped, so `[1.5] * 40` was
39 newlines in Django and 0 in djust. It is a real line-breaking algorithm, not
a width check, and the port lives in `crates/djust_templates/src/pprint.rs` --
`_format` / `_format_items` / `_format_dict_items` / `_pprint_str`, with the
scalar spelling delegated to `djust_core::py_repr_string`, the one definition
the `{{ list }}` path also uses (#1646).

What the randomized differential caught that a table would not
--------------------------------------------------------------
**Dict key ordering, a defect the port itself introduced.** `_safe_repr` sorts
`sorted(object.items(), key=_safe_tuple)` -- by the KEY. The first version
sorted the RENDERED pairs, which the filter it replaced had also done and got
away with, because that filter spelled every key `'...'` unconditionally. Once
keys went through a real `repr`, the opening quote became `"` for a key holding
a `'` (`0x22` vs `0x27`) and escapes shifted the rest, so one key's content
reordered it against every other. 6.4% of a 4000-value corpus; zero cells in
the curated table, and zero in the broad differential whose keys were all
`k000`-shaped.

Former residual, closed by #2292: non-ASCII non-printables
----------------------------------------------------------
`py_repr_string` used to escape `\\`, the active quote, `\t`, `\n`, `\r`, the
rest of C0 and DEL -- and stop, so `U+00A0`, `U+200B`, `U+2028` and `U+FEFF`
rendered literally where CPython writes `\\xa0` / `\\u200b` / `\\u2028` /
`\\ufeff`. The reason given was that `str.isprintable()` is Unicode-version
data which **disagrees across this project's CI matrix**, so no fixed table in
Rust could be green on every runner -- the `striptags` situation (#2273), where
the reference moves and the port cannot.

The disagreement is real and is in fact LARGER than first measured (five
Unicode versions across 3.10-3.14, 11130 code points, not 5812 across a
3.12/3.14 pair -- and 3.13 carries 15.1, which the original count missed). But
it has a shape: every disagreeing code point is one that went from UNASSIGNED
to assigned. The seven general categories that make up the rest of
"non-printable" (`Cc`, `Cf`, `Cs`, `Co`, `Zl`, `Zp`, `Zs`) never move for a
code point already assigned. #2292 ports those seven as a 28-range table and
treats unassigned `Cn` as printable, which is exact for every assigned code
point on every interpreter and identical across all five.

Coverage lives in `test_py_repr_isprintable_table_2292.py`, which recomputes
the table from the running interpreter's `unicodedata` rather than restating a
version. The rows here are the inverted pins.

Measured, on the broad filter differential this file's harness re-runs
(13,751 cells over every measuring filter):

    before  2421 disagree
    after   1998 disagree
    fixed    423        regressed 0

and every one of the 269 remaining `{{ p|pprint }}` cells classifies into the
residual above or into tuple-flattening by the serializer (a `normalize_django_value`
artifact, not this filter's) -- zero unexplained.
"""

from __future__ import annotations

import pprint as _pprint
import random
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402


_COMPILED: dict[str, DjangoTemplate] = {}


def render_both(source: str, value: Any) -> tuple[str, str]:
    """`(django, djust)` for one cell, rendering the SAME value through both.

    The `Template` is compiled once per source: the differentials below run
    thousands of cells against a handful of sources, and re-parsing the template
    each time is most of the wall clock.
    """
    template = _COMPILED.get(source)
    if template is None:
        template = _COMPILED[source] = DjangoTemplate(source)
    django_out = template.render(DjangoContext({"p": value}))
    djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
    return django_out, djust_out


def assert_agrees(source: str, value: Any) -> None:
    django_out, djust_out = render_both(source, value)
    assert djust_out == django_out, (
        f"{source} on {value!r}: django={django_out!r} djust={djust_out!r}"
    )


# ---------------------------------------------------------------------------
# #2279 -- length
# ---------------------------------------------------------------------------

# Every value carries its Python `len`, so the table asserts the CONTRACT
# (code points) and not merely "djust agrees with djust".
LENGTH_VALUES: list[tuple[str, int]] = [
    # The issue's two reproducers.
    ("中<b", 3),
    ("éb&nbsp;&amp; >&#", 17),
    # The empty string.
    ("", 0),
    # ASCII, where bytes and code points agree -- the arm that was never wrong.
    ("abc", 3),
    # Latin-1 accents: 2 bytes each.
    ("é", 1),
    ("ééé", 3),
    # CJK: 3 bytes each.
    ("中", 1),
    ("中文字", 3),
    # A BMP emoji: 4 bytes, ONE code point.
    ("👍", 1),
    # Skin-tone modifier: TWO code points, and Python says 2. Not 1.
    ("👍🏽", 2),
    # ZWJ sequence: three emoji plus two joiners is FIVE code points.
    ("👨‍👩‍👧", 5),
    # Flag (regional indicator pair): two code points.
    ("\U0001f1fa\U0001f1f8", 2),
    # Combining mark: `e` + U+0301 is TWO code points, not one grapheme.
    ("é", 2),
    ("áb̈", 4),
    # Non-ASCII whitespace and invisibles still count as one each.
    (" ​ ﻿", 4),
    # Mixed.
    ("a中👍é", 4),
]


class TestLengthCountsCodePoints:
    @pytest.mark.parametrize(("value", "expected"), LENGTH_VALUES)
    def test_matches_python_len(self, value: str, expected: int) -> None:
        assert len(value) == expected, "the table's own expectation is wrong"
        assert _rust.render_template("{{ p|length }}", {"p": value}) == str(expected)

    @pytest.mark.parametrize(("value", "expected"), LENGTH_VALUES)
    def test_matches_django(self, value: str, expected: int) -> None:
        assert_agrees("{{ p|length }}", value)

    def test_grapheme_clusters_are_not_what_python_counts(self) -> None:
        """The fix must NOT "improve" on Python by counting graphemes.

        Each value below is ONE user-perceived character and more than one code
        point. Python answers with the code points; so must djust.
        """
        for value, code_points in [
            ("👍🏽", 2),
            ("👨‍👩‍👧", 5),
            ("é", 2),
            ("\U0001f1fa\U0001f1f8", 2),
        ]:
            assert len(value) == code_points
            got = _rust.render_template("{{ p|length }}", {"p": value})
            assert got == str(code_points), (
                f"{value!r}: djust said {got}, Python says {code_points} -- a "
                f"grapheme count would say 1 and be a different wrong answer"
            )

    @pytest.mark.parametrize(
        "value",
        [
            [],
            [1, 2, 3],
            ["é", "中"],
            (),
            (1,),
            ("é", "中", "👍"),
            None,
            True,
            42,
            3.5,
        ],
    )
    def test_non_string_values_are_unchanged(self, value: Any) -> None:
        """The list/tuple/scalar arms were already right; keep them that way."""
        assert_agrees("{{ p|length }}", value)

    def test_the_striptags_chain_now_agrees(self) -> None:
        """The exact cell #2273 had to pin as a known divergence.

        `{{ p|striptags|length }}` on `"中<b"` was the chain that made the two
        bugs visible: the old `striptags` deleted the tail, so the BYTE count of
        the `"中"` that survived (3) matched Django's CHAR count of the whole
        value. Django's `strip_tags("中<b")` is `"中<b"` -- the lone `<b` at end
        of input is emitted as data -- so both filters now answer 3, and they
        answer it for the right reason on both sides.
        """
        assert_agrees("{{ p|striptags|length }}", "中<b")
        assert _rust.render_template("{{ p|striptags|safe }}", {"p": "中<b"}) == "中<b"
        assert _rust.render_template("{{ p|striptags|length }}", {"p": "中<b"}) == "3"

    def test_randomized_differential_against_django(self) -> None:
        """Randomized, not curated: 2000 strings over every character class."""
        classes = [
            "abcdefg XYZ0123.,-_",
            "éèñüßàçöÅ",
            "中文字漢字日本語한국",
            "👍😀🎉👍🏽👨‍👩‍👧\U0001f3f3️‍\U0001f308\U0001f1fa\U0001f1f8",
            "éäñ́",
            "  ​﻿ﬁⅠᴀ",
        ]
        pool = "".join(classes)
        rng = random.Random(2279)
        disagreements: list[tuple[str, str, str]] = []
        for _ in range(2000):
            kind = rng.randrange(len(classes) + 2)
            if kind == len(classes):
                value = ""
            elif kind == len(classes) + 1:
                value = "".join(rng.choice(pool) for _ in range(rng.randrange(0, 40)))
            else:
                src = classes[kind]
                value = "".join(rng.choice(src) for _ in range(rng.randrange(0, 25)))
            django_out, djust_out = render_both("{{ p|length }}", value)
            if django_out != djust_out:
                disagreements.append((value, django_out, djust_out))
        assert not disagreements, (
            f"{len(disagreements)} of 2000 disagree; first: {disagreements[0]!r}"
        )


# ---------------------------------------------------------------------------
# #2277 -- pprint
# ---------------------------------------------------------------------------


class TestPprintWraps:
    def test_the_issues_two_reproducers(self) -> None:
        """The exact table in #2277: 39 and 19 newlines, not 0."""
        for value, want_newlines in [
            ([1.5] * 40, 39),
            ({f"k{i:02d}": float(i) for i in range(20)}, 19),
        ]:
            django_out, djust_out = render_both("{{ p|pprint }}", value)
            assert django_out.count("\n") == want_newlines
            assert djust_out.count("\n") == want_newlines
            assert djust_out == django_out

        # The issue's note that this pre-dates #2270 and is unrelated to floats.
        assert_agrees("{{ p|pprint }}", ["a"] * 40)

    @pytest.mark.parametrize(
        "value",
        [
            # Short structures still do NOT wrap -- the flat repr is emitted
            # whenever it fits, which is why nesting alone never wraps.
            [],
            {},
            [1],
            [[1, 2], [3, 4]],
            {"a": 1, "b": 2},
            [1, [2, [3, [4, [5, [6]]]]]],
            # Non-containers pass straight through.
            "not a container",
            12345,
            None,
            True,
            3.5,
            # Wide single elements: one element alone can exceed the width.
            ["w" * 90],
            [["x" * 100]],
            {"a": "x" * 100, "b": 1},
            # A dict whose VALUES wrap: they must line up under the value
            # column, past `len(repr(key)) + 2`.
            {"a": list(range(40))},
            {"z": 1, "a": 2, "m": 3},
            {"a_long_key_name_here": list(range(30)), "b": list(range(30))},
            # Deep nesting past the width on more than one level.
            [{"x": 1, "y": 2}] * 10,
            [list(range(30))],
            list(range(200)),
            [[["deep" * 10] * 6] * 3],
            # A long STRING, which dispatches to `_pprint_str`: split at
            # whitespace, wrapped in parentheses at the top level.
            "word " * 40,
            ["word " * 40],
            {"k": "word " * 40},
            # A string with existing line breaks -- `splitlines` boundaries.
            "line one\n" + "word " * 30,
            ["a\n" + "x" * 100],
            # A string with NO whitespace to break at: it overruns.
            "x" * 200,
            ["x" * 200],
            # Mixed widths and types in one container.
            [1, "short", "y" * 90, None, 2.5, {"k": "v"}],
            # `allowance` threaded to the LAST element and nowhere else: this
            # inner string's repr is 77 characters, which fits `width - indent`
            # minus the 1 a non-last element gets and does NOT fit minus the 2
            # the last element gets, so the two rules give different output.
            # Found by mutating the model, not by guessing (see the gate-off
            # table in the PR).
            [["wo " * 25]],
            [["short", "wo " * 25]],
            # `splitlines` boundaries beyond `\n`. `str::lines()` splits on `\n`
            # alone; Python breaks on eight more. These three are ASCII
            # controls, so their escaped spelling is portable and the case is a
            # parity assertion. (U+2028 and U+2029 are boundaries too, but they
            # are also non-printable, so they land in
            # `TestKnownResidualDivergences` instead.)
            ("word " * 10) + "\x0b" + ("word " * 10),
            ("word " * 10) + "\r" + ("word " * 10),
            [("word " * 10) + "\x0c" + ("word " * 10)],
        ],
    )
    def test_layout_matches_pformat(self, value: Any) -> None:
        assert_agrees("{{ p|pprint }}", value)

    def test_dict_keys_sort_by_key_not_by_rendered_repr(self) -> None:
        """The defect the port itself introduced (see the module docstring).

        A key holding a `'` is spelled with `"` quotes, so sorting the rendered
        pairs puts it under `0x22` instead of under its own first character. A
        key whose leading character is escaped moves too.
        """
        for value in [
            {"k000": 1, "文字1中c中3ée'": 2},
            {"k001": "xx", "中\t🏳'": True},
            {"\tY": 6394, "'quoted": 1, "zz": 2},
            {"\x01a": 1, "1b": 2},
            {"a'b": 1, 'a"b': 2, "ab": 3},
        ]:
            assert_agrees("{{ p|pprint }}", value)

    def test_randomized_differential_against_django(self) -> None:
        """4000 values, deliberately varying length, depth, width and type mix.

        #2277 exists because a sibling differential reported full `pprint`
        parity while every container in its sample was SHORT -- single-variant
        coverage of a multi-variant surface. So the generator draws element
        counts that straddle the width in both directions and element widths
        that individually exceed it.

        The corpus excludes the two documented residuals -- non-ASCII
        non-printable code points and tuples -- which
        `TestKnownResidualDivergences` pins instead.
        """
        pool = "abcdefgXYZ0123 .,-_'\"\\\t\n\r\x01\x7féü中文字👍😀\U0001f3f3"
        rng = random.Random(2277)

        def scalar() -> Any:
            k = rng.randrange(8)
            if k == 0:
                return rng.randrange(-10000, 10000)
            if k == 1:
                return round(rng.uniform(-1e4, 1e4), rng.randrange(0, 7))
            if k == 2:
                return "".join(rng.choice(pool) for _ in range(rng.randrange(0, 40)))
            if k == 3:
                return rng.choice([True, False, None])
            if k == 4:
                return rng.randrange(10**18, 10**25)
            if k == 5:
                # One element wide enough to blow the width on its own.
                return "x" * rng.randrange(0, 200)
            if k == 6:
                # Whitespace to break at, so `_pprint_str` has work to do.
                return "word " * rng.randrange(1, 40)
            return "".join(rng.choice(pool) for _ in range(rng.randrange(0, 130)))

        def build(depth: int = 0) -> Any:
            if depth > 3 or rng.random() < 0.35:
                return scalar()
            # Lengths that straddle 80 characters of flat repr in both
            # directions -- the axis #2277's sibling differential never left.
            # Tapered by depth only to keep the corpus from growing as n**4;
            # every level still reaches both sides of the width.
            widths = [0, 1, 2, 3, 5, 8, 12, 20, 40] if depth < 2 else [0, 1, 2, 3, 5, 8]
            n = rng.choice(widths)
            k = rng.randrange(3)
            if k == 0:
                return [build(depth + 1) for _ in range(n)]
            if k == 1:
                return [scalar()] * n
            return {
                (
                    "k%03d" % i
                    if rng.random() < 0.6
                    else "".join(rng.choice(pool) for _ in range(rng.randrange(1, 14)))
                ): build(depth + 1)
                for i in range(n)
            }

        disagreements = []
        for _ in range(4000):
            value = build()
            django_out, djust_out = render_both("{{ p|pprint }}", value)
            if django_out != djust_out:
                disagreements.append((value, django_out, djust_out))
        assert not disagreements, (
            f"{len(disagreements)} of 4000 disagree; first value "
            f"{disagreements[0][0]!r}\n  django={disagreements[0][1]!r}\n"
            f"  djust ={disagreements[0][2]!r}"
        )


# ---------------------------------------------------------------------------
# py_repr_string's OTHER caller -- the container-rendering path
# ---------------------------------------------------------------------------

# Every row carries Python's own `repr`, so the table asserts the CONTRACT and
# not merely that djust agrees with itself. Each escape is an ASCII control or
# DEL, which every supported interpreter spells identically -- see
# `TestKnownResidualDivergences` for the half that is not portable.
CONTROL_VALUES = [
    ("a\tb", "'a\\tb'"),  # TAB
    ("c\nd", "'c\\nd'"),  # LF
    ("e\rf", "'e\\rf'"),  # CR
    ("g\x1bh", "'g\\x1bh'"),  # ESC -- no `\e` in Python, it is `\x1b`
    ("i\x7fj", "'i\\x7fj'"),  # DEL
    ("k\x00l", "'k\\x00l'"),  # NUL
    ("m\x0bn", "'m\\x0bn'"),  # VT -- no `\v` either
    ("m'n", '"m\'n"'),  # a `'` and no `"` flips the quoting to `"`
    ("o'p\"q", "'o\\'p\"q'"),  # BOTH quote kinds, so the flip does not apply
    ("r\\s", "'r\\\\s'"),  # the backslash itself
]


class TestContainerReprUsesTheSameEscaper:
    """`py_repr_string`'s SECOND caller: `{{ list }}` / `{{ dict }}`, not `pprint`.

    The helper was extracted from `Value::py_repr` so that `pprint` and the
    container-rendering path stop carrying two different escapers (#1646) -- the
    `pprint` one escaped nothing at all. Extracting it also gave `Value::py_repr`
    the ASCII control escapes it had been missing, which is a behaviour change on
    a path neither #2279 nor #2277 names::

        {{ p }} with p = ['a<TAB>b']
            before   [ 'a<TAB>b' ]   -- a literal tab
            after    [ 'a\\tb' ]     -- Django's answer

    That is a fix, and it is disclosed in the CHANGELOG. It is tested HERE
    because every other test in this file reaches `py_repr_string` through
    `pprint`: gate off the escaping inside the helper and, without this class,
    nothing tells you which of the two callers you broke. The
    refactor-with-helper gap, CLAUDE.md #1195.
    """

    @pytest.mark.parametrize(("value", "want_repr"), CONTROL_VALUES)
    def test_the_table_states_pythons_answer(self, value: str, want_repr: str) -> None:
        """Guards every assertion below from pinning a wrong expectation."""
        assert repr(value) == want_repr

    @pytest.mark.parametrize(("value", "want_repr"), CONTROL_VALUES)
    def test_in_a_list(self, value: str, want_repr: str) -> None:
        assert_agrees("{{ p }}", [value])
        assert _rust.render_template("{{ p|safe }}", {"p": [value]}) == f"[{want_repr}]"

    @pytest.mark.parametrize(("value", "want_repr"), CONTROL_VALUES)
    def test_in_a_dict_value(self, value: str, want_repr: str) -> None:
        assert_agrees("{{ p }}", {"k": value})
        assert (
            _rust.render_template("{{ p|safe }}", {"p": {"k": value}}) == "{'k': " + want_repr + "}"
        )

    @pytest.mark.parametrize(("value", "want_repr"), CONTROL_VALUES)
    def test_in_a_dict_key(self, value: str, want_repr: str) -> None:
        """A KEY goes through the same helper. `{{ dict }}` had a hand-rolled
        escaper in this position once and it missed the backslash, so a key
        `a\\` emitted a closing quote that read as escaped (#2203 review)."""
        assert_agrees("{{ p }}", {value: 1})
        assert _rust.render_template("{{ p|safe }}", {"p": {value: 1}}) == "{" + want_repr + ": 1}"

    @pytest.mark.parametrize(("value", "want_repr"), CONTROL_VALUES)
    def test_in_a_tuple_and_when_nested(self, value: str, want_repr: str) -> None:
        """The `Value::Tuple` arm, reached WITHOUT `normalize_django_value`.

        The serializer flattens a tuple to a list, so a tuple that goes through
        `render_both` never exercises that arm; passing the raw Python tuple to
        `render_template` does. Django's `str(tuple)` is the reference either
        way, and it is `repr` of each element.
        """
        assert _rust.render_template("{{ p|safe }}", {"p": (value,)}) == "(" + want_repr + ",)"
        assert_agrees("{{ p }}", [[value]])

    def test_the_two_callers_agree_on_the_same_value(self) -> None:
        """The convergence itself: one spelling, two paths (#1646).

        A second escaper is what made these disagree before this PR, and any
        mutation inside the helper has to move BOTH sides for this to stay
        green -- which is what makes it a check on the convergence rather than
        on either caller.
        """
        for value, want_repr in CONTROL_VALUES:
            container = _rust.render_template("{{ p|safe }}", {"p": [value]})
            pprinted = _rust.render_template("{{ p|pprint|safe }}", {"p": [value]})
            assert container == f"[{want_repr}]", value
            assert pprinted == container, value


# ---------------------------------------------------------------------------
# The residuals this PR does NOT close, recorded rather than rediscovered
# ---------------------------------------------------------------------------


class TestKnownResidualDivergences:
    """**Historically a pin class; every row in it is now CLOSED.**

    Rows here used to assert a KNOWN-WRONG answer -- that djust STILL differed
    from Django -- so that each would fail the day someone closed its gap. Two
    gaps have since closed, and both rows survive renamed and inverted:

    * `{{ dict|length }}` (#2294) -> `test_length_of_a_dict_now_agrees_fixed_by_2294`,
      plus the model-instance half that explains why the pin existed;
    * non-ASCII non-printable escaping (#2292) ->
      `test_non_ascii_non_printables_are_now_escaped_fixed_by_2292` and
      `test_the_layout_agreed_even_while_the_escape_did_not`, with the
      exhaustive coverage in `test_py_repr_isprintable_table_2292.py`.

    Converting a closed pin rather than deleting it is deliberate: the
    reasoning for the original divergence is the reasoning the fix had to
    answer, and it is the part a future reader needs. Nothing in this class
    asserts a wrong answer any more -- a NEW residual would be added as a pin
    in the old style, with its issue number on it.

    The failure mode this framing guards against is real and recent: three
    artifacts pinned a buggy arrangement as if correct and let a shipped XSS
    survive in `linenumbers`, one of them saying so in its own test name.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "\xa0",  # Zs -- NO-BREAK SPACE
            "\u200b",  # Cf -- ZERO WIDTH SPACE
            "\u2028",  # Zl -- LINE SEPARATOR, also a `splitlines` boundary
            "\u2029",  # Zp -- PARAGRAPH SEPARATOR, likewise
            "\ufeff",  # Cf -- ZERO WIDTH NO-BREAK SPACE
            "\ue000",  # Co -- a private-use code point
        ],
    )
    def test_non_ascii_non_printables_are_now_escaped_fixed_by_2292(self, value: str) -> None:
        """**FIXED, and inverted from a pin to a regression test.**

        This row used to assert djust still DISAGREED with Django, and carried
        a "now AGREES -- delete this row" message for whoever closed the gap.
        #2292 closed it: `py_repr_string` now carries a 28-range table of the
        seven stable general categories (`Cc`, `Cf`, `Cs`, `Co`, `Zl`, `Zp`,
        `Zs`) and escapes them, treating unassigned `Cn` as printable.

        The reasoning that kept it open was that `str.isprintable()` is
        Unicode-version data and no fixed table is green on every runner. That
        premise is true -- and understated: djust's matrix spans FIVE Unicode
        versions and 11130 disagreeing code points, not the 5812 in a
        3.12-vs-3.14 pair. What it misses is that every one of those 11130 is
        an unassigned code point BECOMING assigned; the seven categories
        themselves never move for anything already assigned. See
        `test_py_repr_isprintable_table_2292.py`, which recomputes that claim
        from the running interpreter rather than restating it.
        """
        django_out, djust_out = render_both("{{ p|pprint }}", value)
        assert django_out == djust_out
        # Escaped, not merely passed through -- the literal spelling this row
        # used to pin must now be gone.
        rendered = _rust.render_template("{{ p|pprint|safe }}", {"p": value})
        assert rendered != f"'{value}'"
        assert rendered == _pprint.pformat(value)

    def test_the_layout_agreed_even_while_the_escape_did_not(self) -> None:
        """A `U+2028` string long enough to reach `_pprint_str`.

        While #2292 was open this asserted the two outputs differed ONLY by the
        spelling, by substituting the literal code point back into Django's
        output. That was the evidence the gap was a spelling gap and not a
        layout one -- so with the spelling fixed, the two must now agree
        outright, with no substitution. Keeping the case (rather than deleting
        it with the pin) keeps the wrap path covered: it depends on `\\u2028`
        being a `splitlines` boundary, which djust honours.
        """
        value = ("word " * 10) + "\u2028" + ("word " * 10)
        django_out, djust_out = render_both("{{ p|pprint }}", value)
        assert django_out == djust_out
        assert "\\u2028" in djust_out, "the escape did not survive the wrap path"
        assert "\n" in djust_out, "the case does not reach the wrapping path"

    def test_ascii_controls_in_the_same_string_are_escaped(self) -> None:
        """The ASCII half, which was correct before #2292 and must stay so:
        the named escapes still win over the numeric form."""
        for value, want in [
            ("a\tb", "'a\\tb'"),
            ("a\nb", "'a\\nb'"),
            ("a\rb", "'a\\rb'"),
            ("a\x00b", "'a\\x00b'"),
            ("a\x1bb", "'a\\x1bb'"),
            ("a\x7fb", "'a\\x7fb'"),
            ("a\\b", "'a\\\\b'"),
            ("a'b", '"a\'b"'),
            ("a'b\"c", "'a\\'b\"c'"),
        ]:
            assert _pprint.pformat(value) == want, "the table's expectation is wrong"
            assert _rust.render_template("{{ p|pprint|safe }}", {"p": value}) == want, value

    @pytest.mark.parametrize("value", [{"a": 1, "b": 2}, {"é": "中"}])
    def test_length_of_a_dict_now_agrees_fixed_by_2294(self, value: dict) -> None:
        """**No longer a known-wrong pin.** Closed by #2294; kept as the
        regression test, converted rather than deleted.

        This row used to read ``..._is_WRONGLY_still_zero_bug_2294`` and assert
        ``djust_out == "0"``, because `Value::Object` had no arm in the
        `length` match and fell to `_ => 0`. #2279 left it alone deliberately:
        `Value::Object` carries BOTH a Python dict and a serialized model, and
        `len(model)` raises `TypeError`, which Django's `length` catches and
        answers 0 to -- so 0 was the right answer for one of the two things the
        variant represents and wrong for the other.

        #2294 told them apart with `Value::object_str()`, the `"__str__"`
        predicate `{{ obj }}` already uses. The model half is asserted directly
        below, and the full case -- including why `"__model__"` is NOT the
        marker, and the residual a dict carrying its own `"__str__"` key leaves
        -- lives in
        ``test_measuring_filter_parity_2294.py::TestLengthOfAnObject``.
        """
        django_out, djust_out = render_both("{{ p|length }}", value)
        assert django_out == str(len(value))
        assert djust_out == django_out

    def test_a_model_still_answers_zero_which_is_why_it_was_pinned(self) -> None:
        """The half that keeps `0` -- and the reason the row above was a pin.

        `len(model)` raises `TypeError`; Django's `length` catches it. A fix
        that made every `Value::Object` answer `o.len()` would have traded one
        wrong answer for another, spelling a model's FIELD COUNT.
        """
        from django.contrib.auth.models import User

        model = User(username="bob")
        with pytest.raises(TypeError):
            len(model)
        django_out, djust_out = render_both("{{ p|length }}", model)
        assert django_out == "0"
        assert djust_out == "0"
