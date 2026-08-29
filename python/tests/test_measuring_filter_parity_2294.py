"""Three of the four measuring-filter divergences #2294 found by grepping the sink.

#2294 was filed by a sweep over the length-measuring filters while #2279 (byte
counts) was in flight. None of its four items is that defect; each is a separate
Python semantic djust did not reproduce. Three are fixed here.

1. ``stringformat:"Ns"`` ignored the width
--------------------------------------------
Django's filter is ``("%" + arg) % value``, and CPython honours
``[flags][width][.precision]`` for the ``s`` conversion exactly as it does for
``%d``. The arm read only the conversion character, so ``{{ p|stringformat:"10s" }}``
rendered ``'ab'`` where Django renders ``'        ab'`` — and ``.3s`` did not
truncate, and ``-10s`` did not left-align.

The grammar is ported from CPython's ``unicode_format_arg_parse`` and then
CHECKED against it: :meth:`TestStringformatS.test_exhaustive_spec_sweep` builds
every prefix over ``-+ #0`` / digits / ``.`` / ``hlL`` up to length 4, crosses
them with a value corpus, and requires djust to equal ``("%" + prefix + "s") % v``
on every cell. Five behaviours that sweep settles and a curated table would not
have thought to include:

* the ``0`` flag is IGNORED for ``s`` — ``"%010s" % "ab"`` pads with *spaces*,
  where the same flag on ``%d`` pads with zeros;
* ``+``, ``' '`` and ``#`` are accepted and do nothing, may repeat, and may
  follow one another (``%--10s`` is left-aligned);
* a bare ``.`` is precision ZERO — ``"%.s"`` is ``''`` and ``"%10.s"`` is ten
  spaces, not "no precision";
* ``0`` leads as a flag, so ``%0s`` is width 0 while ``%010s`` is width 10;
* width and precision have DIFFERENT limits, five orders of magnitude apart —
  width is a ``Py_ssize_t`` (max ``2**63-1``) and precision an ``int`` (max
  ``2**31-1``); past either, CPython raises ``ValueError`` and Django's
  ``except (ValueError, TypeError)`` answers ``''``. Both bounds were bisected
  against the interpreter rather than assumed, because assuming one limit for
  both is exactly the plausible-and-wrong answer.

2. ``center`` used Rust's tie-break
-----------------------------------
``format!("{s:^width$}")`` puts the smaller half of an odd margin on the LEFT,
unconditionally. CPython's ``str.center`` is
``left = marg // 2 + (marg & width & 1)`` — it biases left only when the width
is *also* odd. So ``'ab'.center(5)`` is ``'  ab '`` and djust said ``' ab  '``.

The two agree whenever the margin is even, which is why the issue's own note
that ``'a'.center(4)`` and ``'abc'.center(6)`` agree is the important part: a
curated table drawn from those samples finds nothing.
:meth:`TestCenter.test_exhaustive_length_by_width_sweep` walks every
``(len, width)`` pair in a grid instead.

``ljust`` / ``rjust`` were already right: Rust's format width is a char count,
so the byte-vs-char question of #2279 does not arise for any of the three.

3. ``{{ dict|length }}`` answered 0
-----------------------------------
``Value::Object`` had no arm in the ``length`` match and fell to ``_ => 0``.

The reason #2279 left it alone is the whole of the decision: ``Value::Object``
is **two** different Python things wearing one shape — a genuine ``dict``, whose
``length`` is ``len(dict)``, and any non-dict object the Python serializer
flattened into a map, whose ``length`` is 0 because ``len(model)`` raises
``TypeError`` and Django's filter catches it. So ``_ => 0`` was right for one of
them and wrong for the other, and a fix that just returns ``o.len()`` trades one
wrong answer for a different one.

The marker that tells them apart is a ``"__str__"`` entry holding a string —
:meth:`Value::object_str`, the same predicate ``{{ obj }}`` already uses to
decide whether a map renders as its ``__str__`` or as a dict repr (#968). Using
it here is what keeps ``{{ p }}`` and ``{{ p|length }}`` from disagreeing about
what ``p`` IS.

``"__model__"`` is the marker that LOOKS more specific and is not usable: **four
of the six** model-serialization sites omit it — ``serialization.py``'s
depth-limited-FK and max-depth shorthands and both ``template/rendering.py``
fallbacks all stamp ``__str__`` alone; only ``_serialize_model_safely`` and
``jit.py``'s identity-only subset carry it. So a model at the depth limit would
have started answering ``len()`` of its field count. Filed on its own as #2322,
because a marker that four of six producers omit is a trap for anything that
reaches for it, not just for ``length``. Pinned in
:meth:`TestLengthOfAnObject.test_model_at_the_depth_limit_still_answers_zero`.

The residual this leaves is a genuine ``dict`` that happens to carry a
``"__str__"`` key: it answers 0 where Django answers its length. That dict
ALREADY renders as its ``__str__`` rather than as a dict, so the alternative is
not "more correct", it is *two* predicates that disagree with each other.
Pinned as a known divergence in
:meth:`TestLengthOfAnObject.test_a_dict_carrying___str___is_WRONGLY_read_as_an_object`.

4. ``truncatechars`` counts combining marks — NOT fixed here
------------------------------------------------------------
Still open, re-measured on this branch and unchanged. ``Truncator.chars`` opens
with ``unicodedata.normalize("NFC", text)`` and skips characters whose canonical
combining class is non-zero; neither is available without adding a Unicode
normalization crate to the workspace. It is already pinned, with its
measurement and alongside ``slugify``'s NFKD fold (the same blocker), in
``test_truncate_slugify_parity_2262.py::TestKnownRemainingDivergences`` — which
is where it stays, because the two want closing together by one dependency
decision rather than separately.

Measured
--------
``scripts/filter-parity-differential.py``, two builds, against a rebuilt
``origin/main`` baseline at ``1fa46c33`` (29,662 cells over every filter in
Django's live registry, chained to depth 3, plus the custom-filter and
marked-tuple axes)::

    newly AGREEING            30
    REGRESSIONS                1
    live-payload leaks   38 -> 38   (0 closed, 0 INTRODUCED)

The single regression is ``{{ dict|add:"1"|length }}``, and it is ``add``'s
documented third-branch divergence being un-cancelled rather than anything this
branch got wrong — FOUR twins (``l-plain``, ``l-mixed``, ``l-marked``,
``t-marked``) already diverged identically on the baseline. Pinned and
explained in
:meth:`TestLengthOfAnObject.test_the_add_chain_UNMASKS_adds_own_documented_divergence`.
"""

from __future__ import annotations

import itertools
import random
import re
import unicodedata
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

_COMPILED: dict[str, DjangoTemplate] = {}


def render_both(source: str, value: Any) -> tuple[str, str]:
    """``(django, djust)`` for one cell, rendering the SAME value through both."""
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


# A corpus that varies the axes the three filters branch on: emptiness, ASCII vs
# multi-byte vs astral, and length either side of every width under test. The
# astral and combining entries are spelled with escapes on purpose — a literal
# in the source can be NFC-normalized by an editor on the way to disk, which
# silently turns a decomposed test value into a precomposed one.
_VALUES: list[str] = [
    "",
    "a",
    "ab",
    "abc",
    "abcd",
    "abcdef",
    "x" * 20,
    "中",  # CJK, 3 bytes / 1 code point
    "中文字",
    "é",  # precomposed e-acute, 2 bytes / 1 code point
    "e\u0301",  # DECOMPOSED: 2 code points
    "\U0001f44d",  # astral, 4 bytes / 1 code point
    "\U0001f44d\U0001f3fd",  # 2 code points
    "a中\U0001f44dé",
    " sp ced ",
    "<b>&amp;</b>",
]


# ---------------------------------------------------------------------------
# 1. stringformat
# ---------------------------------------------------------------------------


class TestStringformatS:
    """The reported cells, then an exhaustive sweep of the whole spec grammar."""

    @pytest.mark.parametrize(
        ("source", "value", "expected"),
        [
            # The three cells #2294 quotes, with Django's answer written out.
            ('{{ p|stringformat:"10s" }}', "ab", "        ab"),
            ('{{ p|stringformat:"5s" }}', "", "     "),
            ('{{ p|stringformat:"4s" }}', "中", "   中"),
            # The three the issue says are "presumably affected the same way".
            ('{{ p|stringformat:"-10s" }}', "ab", "ab        "),
            ('{{ p|stringformat:".3s" }}', "abcdef", "abc"),
            ('{{ p|stringformat:"10.3s" }}', "abcdef", "       abc"),
        ],
    )
    def test_reported_cells(self, source: str, value: str, expected: str) -> None:
        django_out, djust_out = render_both(source, value)
        assert django_out == expected, "the table's own expectation is wrong"
        assert djust_out == expected

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            # The `0` flag does NOT zero-pad `%s`, unlike `%d`.
            ("010s", "    abcdef"),
            # Accepted-and-ignored flags.
            ("+10s", "    abcdef"),
            (" 10s", "    abcdef"),
            ("#10s", "    abcdef"),
            # Flags repeat, and a repeated/followed `-` still left-aligns.
            ("--10s", "abcdef    "),
            ("- 10s", "abcdef    "),
            # A bare `.` is precision ZERO, not "absent".
            (".s", ""),
            ("10.s", "          "),
            (".0s", ""),
            # `0` leads as a flag, so this is width 0 and not width 10.
            ("0s", "abcdef"),
            ("00s", "abcdef"),
            # A width shorter than the value does nothing.
            ("1s", "abcdef"),
            # Trailing length modifiers are accepted and ignored.
            ("hs", "abcdef"),
            ("10ls", "    abcdef"),
            ("10Ls", "    abcdef"),
            # `ValueError: width too big` / `precision too big` -> Django's "".
            #
            # The two limits are five orders of magnitude apart and were
            # BISECTED against the interpreter, not assumed: width is a
            # `Py_ssize_t` (max 2**63-1) and precision an `int` (max 2**31-1).
            # Every row here is one past its limit, so CPython raises during
            # the PARSE and neither side allocates. A row just UNDER the width
            # limit is deliberately absent — it is a two-gigabyte string.
            ("9223372036854775808s", ""),
            ("99999999999999999999s", ""),
            (".2147483648s", ""),
            (".99999999999999999999s", ""),
            # ...and the value one below each limit is still ACCEPTED, which is
            # what makes the rows above a boundary rather than a blanket
            # refusal of long digit runs. Checked without allocating: a
            # precision merely truncates.
            (".2147483647s", "abcdef"),
            # Leading zeros do not count toward the limit.
            ("00000000000000000000010s", "    abcdef"),
        ],
    )
    def test_grammar_corners(self, spec: str, expected: str) -> None:
        """Each row is a behaviour a reasonable reading of "%s" gets wrong."""
        source = "{{ p|stringformat:%r }}" % spec
        django_out, djust_out = render_both(source, "abcdef")
        assert django_out == expected, "the table's own expectation is wrong"
        assert djust_out == expected

    def test_zero_flag_still_zero_pads_the_numeric_conversions(self) -> None:
        """The `0`-is-ignored rule is specific to `s`; `%05d` must not change."""
        for value, expected in [(1, "00001"), (-1, "-0001")]:
            django_out, djust_out = render_both('{{ p|stringformat:"05d" }}', value)
            assert django_out == expected
            assert djust_out == expected

    def test_exhaustive_spec_sweep(self) -> None:
        """Every grammar-valid prefix up to length 4, crossed with the corpus.

        This is the check a curated table cannot be: the parser is a port of
        CPython's, so the thing worth asserting is that it AGREES with CPython
        over its whole input space rather than over the corners its author
        thought of. ~21k prefix cells; the value cross is sampled so the test
        stays under a second.
        """
        alphabet = ["-", "+", " ", "#", "0", "1", "2", ".", "h", "l", "L"]
        grammar = re.compile(r"^[-+ #0]*[0-9]*(?:\.[0-9]*)?[hlL]?$")
        rng = random.Random(2294)
        checked = 0
        for k in range(5):
            for combo in itertools.product(alphabet, repeat=k):
                prefix = "".join(combo)
                if not grammar.match(prefix):
                    continue
                for value in rng.sample(_VALUES, 4):
                    want = ("%" + prefix + "s") % value
                    got = _rust.render_template(
                        "{{ p|stringformat:arg|safe }}", {"p": value, "arg": prefix + "s"}
                    )
                    assert got == want, f"%{prefix}s on {value!r}: cpython={want!r} djust={got!r}"
                    checked += 1
        assert checked > 8000, f"the sweep collapsed to {checked} cells"

    def test_randomized_differential_against_django(self) -> None:
        """2 000 random (spec, value) pairs straight through both engines.

        The sweep above compares against CPython's ``%``; this one compares
        against Django's rendered output, so the filter's own ``except
        (ValueError, TypeError): return ""`` and the autoescape pass are in the
        loop too.
        """
        rng = random.Random(22940)
        for _ in range(2000):
            flags = "".join(rng.choices("-+ #0", k=rng.randint(0, 2)))
            width = "" if rng.random() < 0.3 else str(rng.randint(0, 25))
            prec = (
                ""
                if rng.random() < 0.5
                else "." + ("" if rng.random() < 0.2 else str(rng.randint(0, 8)))
            )
            spec = flags + width + prec + "s"
            value = rng.choice(_VALUES)
            assert_agrees("{{ p|stringformat:%r }}" % spec, value)

    def test_non_string_values_go_through_str_first(self) -> None:
        """`"%10s" % v` is `str(v)` padded.

        Tuples are excluded, and not because the filter mishandles them:
        ``normalize_django_value`` flattens a tuple to a list on the way in, so
        the renderer sees ``[1, 2]`` and can only ever spell it that way. That
        is the serializer artifact ``test_length_pprint_parity_2279_2277.py``
        already documents, one layer above this filter.
        """
        for value in [None, True, False, 0, 42, -7, 3.5, [1, 2], {"a": 1}]:
            assert_agrees('{{ p|stringformat:"12s" }}', value)
            assert_agrees('{{ p|stringformat:"-12s" }}', value)
            assert_agrees('{{ p|stringformat:".2s" }}', value)


# ---------------------------------------------------------------------------
# 2. center
# ---------------------------------------------------------------------------


class TestCenter:
    @pytest.mark.parametrize(
        ("value", "width", "expected"),
        [
            # The three cells #2294 quotes.
            ("ab", 5, "  ab "),
            ("abcd", 5, " abcd"),
            ("ab", 3, " ab"),
            # The two the issue names as coincidental agreements: they must
            # still agree, so a "fix" that just flips the bias is caught.
            ("a", 4, " a  "),
            ("abc", 6, " abc  "),
        ],
    )
    def test_reported_cells(self, value: str, width: int, expected: str) -> None:
        assert value.center(width) == expected, "the table's own expectation is wrong"
        source = '{{ p|center:"%d" }}' % width
        django_out, djust_out = render_both(source, value)
        assert django_out == expected
        assert djust_out == expected

    def test_exhaustive_length_by_width_sweep(self) -> None:
        """Every ``(len 0..12) x (width 0..24)`` pair, against ``str.center``.

        Both halves of ``marg // 2 + (marg & width & 1)`` are load-bearing and
        only this grid reaches both: the ``& 1`` term fires solely where margin
        and width are BOTH odd, which is a quarter of the grid and none of a
        table drawn from even margins.
        """
        for n in range(13):
            for width in range(25):
                value = "a" * n
                want = value.center(width)
                got = _rust.render_template('{{ p|center:"%d" }}' % width, {"p": value})
                assert got == want, f"{value!r}.center({width}): py={want!r} djust={got!r}"

    def test_width_counts_code_points_not_bytes(self) -> None:
        """`str.center` pads to a code-point count; so must this.

        ``|safe`` because the corpus carries markup and this compares against
        ``str.center`` rather than against a Django render — autoescape would
        otherwise be measured as a difference.
        """
        for value in _VALUES:
            for width in (0, 1, 5, 6, 7, 12):
                want = value.center(width)
                got = _rust.render_template('{{ p|center:"%d"|safe }}' % width, {"p": value})
                assert got == want, f"{value!r}.center({width}): py={want!r} djust={got!r}"
                assert len(got) == max(width, len(value))

    def test_randomized_differential_against_django(self) -> None:
        rng = random.Random(22941)
        for _ in range(2000):
            value = rng.choice(_VALUES)
            width = rng.randint(0, 30)
            assert_agrees('{{ p|center:"%d" }}' % width, value)

    def test_ljust_and_rjust_are_unchanged(self) -> None:
        """The two siblings that were already right stay right."""
        for value in _VALUES:
            for width in range(0, 14):
                for name in ("ljust", "rjust"):
                    want = getattr(value, name)(width)
                    got = _rust.render_template(
                        '{{ p|%s:"%d"|safe }}' % (name, width), {"p": value}
                    )
                    assert got == want, f"{value!r}.{name}({width})"


# ---------------------------------------------------------------------------
# 3. length of a Value::Object
# ---------------------------------------------------------------------------


def _a_model() -> Any:
    """An UNSAVED auth user — a real model, so `_serialize_model_safely` runs."""
    from django.contrib.auth.models import User

    return User(username="bob", first_name="Bob")


class TestLengthOfAnObject:
    @pytest.mark.parametrize(
        "value", [{}, {"a": 1}, {"a": 1, "b": 2}, {"é": "中"}, {str(i): i for i in range(30)}]
    )
    def test_a_dict_answers_its_length(self, value: dict) -> None:
        django_out, djust_out = render_both("{{ p|length }}", value)
        assert django_out == str(len(value)), "the expectation is wrong"
        assert djust_out == str(len(value))

    def test_a_model_instance_still_answers_zero(self) -> None:
        """`len(model)` raises `TypeError`; Django's `length` catches it.

        This is the case that makes ``o.len()`` alone the wrong fix: the model
        arrives at the renderer as a map of its serialized fields, so a naive
        length would answer the FIELD COUNT — a number with no meaning that
        Django never produces.
        """
        model = _a_model()
        with pytest.raises(TypeError):
            len(model)
        serialized = normalize_django_value({"p": model})["p"]
        assert isinstance(serialized, dict) and len(serialized) > 1, (
            "the model did not reach the renderer as a multi-key map, so this "
            "test no longer exercises the case it is named for"
        )
        django_out, djust_out = render_both("{{ p|length }}", model)
        assert django_out == "0"
        assert djust_out == "0"

    def test_model_at_the_depth_limit_still_answers_zero(self) -> None:
        """The shape that ruled ``"__model__"`` out as the marker, kept.

        When #2294 landed, four of the six sites stamping ``__str__`` omitted
        ``__model__``, so keying `length` on the marker would have made every
        depth-limited model answer its key count. #2322 closed that at the
        source — one producer, ``serialization.model_identity``, so every
        model-shorthand now carries it.

        This row stays, and is now about a stronger property than the one that
        motivated it: ``object_str()`` keys on ``__str__``, which means a map
        reaching the engine from an OLD wire payload, a hand-built dict, or any
        producer outside djust is read the same way. `length` must not depend
        on which key set the producer happened to emit, so the marker-less
        shorthand — still the exact shape a pre-#2322 client-side snapshot
        holds — is asserted directly.
        """
        shorthand = {"id": 7, "pk": 7, "__str__": "bob"}
        assert "__model__" not in shorthand
        got = _rust.render_template("{{ p|length }}", {"p": shorthand})
        assert got == "0", "a depth-limited model must not answer its key count"
        # And the shape djust emits TODAY, which does carry the marker.
        from djust.serialization import model_identity

        class _Bob:
            pk = 7

            def __str__(self) -> str:
                return "bob"

        current = model_identity(_Bob())
        assert current["__model__"] == "_Bob"
        assert _rust.render_template("{{ p|length }}", {"p": current}) == "0"

    def test_there_is_exactly_one_producer_of_a_model_identity_map(self) -> None:
        """What the 4-of-6 count pin became once #2322 closed the split.

        The count pin was right for its moment and cannot survive its own fix:
        with the split closed it can only ever read 6-with / 0-without, and a
        SEVENTH hand-rolled literal that happened to carry ``__model__`` would
        pass it while re-opening the drift on the next key — a pin that cannot
        go red is decorative (#1859). What is load-bearing after #2322 is that
        exactly one place BUILDS the map, which is why this asserts that
        instead.

        Kept here, next to the `length` cases that motivated it, and duplicated
        nowhere: the behavioural half — all six producers driven and their
        shapes compared — lives in
        ``python/djust/tests/test_model_identity_shape_2322.py``.
        """
        import re
        from pathlib import Path

        import djust

        root = Path(djust.__file__).parent
        files = [
            root / "serialization.py",
            root / "mixins" / "jit.py",
            root / "template" / "rendering.py",
        ]
        sites = [
            f"{path.name}:{i}"
            for path in files
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r'"__str__":\s*str\(', line)
        ]
        assert len(sites) == 1, (
            f"{len(sites)} sites build a model identity map by hand: {sites}. "
            f"There must be exactly ONE — `serialization.model_identity` — and "
            f"every other producer must call it (#2322). Six hand-rolled copies "
            f"is what made `__model__` unusable as a marker in the first place."
        )
        assert sites[0].startswith("serialization.py:"), sites

    def test_the_length_and_the_rendering_agree_about_what_a_map_is(self) -> None:
        """One predicate, so ``{{ p }}`` and ``{{ p|length }}`` cannot drift.

        ``object_str()`` is the definition; this asserts the two callers give
        the same verdict on the same value rather than each carrying a copy
        (#1646).
        """
        for value, is_object in [
            ({"a": 1, "b": 2}, False),
            ({}, False),
            ({"id": 1, "pk": 1, "__str__": "bob"}, True),
            # A `"__str__"` that is not a string is NOT a marker -- `Display`
            # falls back to dict repr for it, so `length` must count it.
            ({"a": 1, "__str__": None}, False),
            ({"a": 1, "__str__": 7}, False),
        ]:
            rendered = _rust.render_template("{{ p }}", {"p": value})
            looks_like_a_dict_repr = rendered.startswith("{") and rendered.endswith("}")
            length = _rust.render_template("{{ p|length }}", {"p": value})
            assert looks_like_a_dict_repr is (not is_object), rendered
            assert (length == "0" and len(value) != 0) is (is_object and len(value) != 0)
            if not is_object:
                assert length == str(len(value)), value

    def test_a_dict_carrying___str___is_WRONGLY_read_as_an_object(self) -> None:
        """**Pins a KNOWN-WRONG answer.** The residual the marker leaves.

        A hand-built ``dict`` that happens to hold a string ``"__str__"`` key is
        indistinguishable from a serialized model, so it answers 0 where Django
        answers 1. Not fixed, and not fixable without a marker the Python side
        does not currently emit: the SAME value already renders as its
        ``__str__`` under ``{{ p }}`` (#968), so the two behaviours are
        consistent with each other and inconsistent with Django together.
        """
        value = {"__str__": "surprise"}
        django_out, djust_out = render_both("{{ p|length }}", value)
        assert django_out == "1"
        assert djust_out == "0", "now AGREES -- delete this row"
        # ... and it is the SAME predicate that makes `{{ p }}` render the
        # `__str__`, which is why closing one without the other would be worse.
        assert _rust.render_template("{{ p }}", {"p": value}) == "surprise"

    def test_the_add_chain_UNMASKS_adds_own_documented_divergence(self) -> None:
        """**Pins a KNOWN-WRONG answer**, and the one cell the fix un-cancels.

        The two-build differential over 29,662 cells reports +30 newly agreeing
        and exactly one regression: ``{{ dict|add:"1"|length }}``, ``0`` before
        and ``2`` after. (Re-measured against ``1fa46c33`` after #2316 and
        #2318 both landed in ``filters.rs`` and grew this corpus; the first run,
        on 27,684 cells, gave the same three numbers.)

        It was not this filter's defect. Django's ``add`` third branch returns
        ``""`` for a value it can neither sum nor concatenate; djust returned
        the value UNCHANGED, which ``filters.rs``'s ``add`` arm called a
        "documented divergence, not an oversight". So ``add`` handed ``length``
        the dict itself where Django hands it ``""``, and a correct ``length``
        then answered 2 rather than 0.

        **#2359 closed it at the source**, which is what the docstring below
        said would have to happen: "Closing it means revisiting ``add``'s
        documented decision, which belongs to ``add``." The row is kept rather
        than deleted, with its assertion inverted, because the cell is the
        canonical example of two wrongs cancelling (#2272) and of the chain
        axis being the only place either was visible.

        The class is PRE-EXISTING: FOUR twins of this cell — ``l-plain``,
        ``l-mixed``, ``l-marked`` and ``t-marked`` (the marked TUPLE #2316
        added) — already read ``django='0' djust='2'`` on the baseline build,
        because the list and tuple arms of ``length`` were already right. The dict cell agreed only because two wrongs
        cancelled — the #2272 pattern, and the same shape as #2273 unmasking
        #2279. Closing it means revisiting ``add``'s documented decision, which
        belongs to ``add``.
        """
        value = {"a": 1, "b": 2}
        assert render_both('{{ p|add:"1"|length }}', value) == ("0", "0")
        # `add` is where the divergence WAS, and #2359 is where it closed.
        assert render_both('{{ p|add:"1" }}', value) == ("", "")
        # ...and the list AND tuple twins closed with it, which is what makes
        # this a CLASS rather than something the dict fix introduced. #2316's
        # marked-tuple corpus input made the tuple twin visible to the
        # differential; it is asserted here too so the claim "four twins" in
        # the docstring has a test behind each shape.
        assert render_both('{{ p|add:"1"|length }}', [1, 2]) == ("0", "0")
        assert render_both('{{ p|add:"1"|length }}', (1, 2)) == ("0", "0")

    def test_the_other_length_arms_are_unchanged(self) -> None:
        for value in ["", "abc", "中文", [], [1, 2, 3], (), (1,), None, True, 42, 3.5]:
            assert_agrees("{{ p|length }}", value)

    def test_randomized_differential_against_django(self) -> None:
        rng = random.Random(22942)
        for _ in range(1000):
            n = rng.randint(0, 12)
            value = {f"k{i}": rng.choice(_VALUES) for i in range(n)}
            assert_agrees("{{ p|length }}", value)


# ---------------------------------------------------------------------------
# 4. still open
# ---------------------------------------------------------------------------


class TestItem4IsStillOpen:
    """``truncatechars`` counting combining marks \u2014 CLOSED by #2319.

    The split was a recorded decision: closing it needed a Unicode
    normalization crate, which is a dependency decision that also closes
    ``slugify``'s NFKD fold, so the two were deferred into one change. That
    change landed \u2014 ``unicode-normalization``, adopted on the measured grounds
    that canonical combining class and canonical decomposition never move for
    an assigned code point (unlike ``str.isprintable()``, the sibling question
    in #2292, which is why those two issues got opposite answers).

    The class name is kept so the cross-reference from
    ``test_truncate_slugify_parity_2262.py`` and from #2294 still resolves; the
    assertion is inverted. Full coverage is in
    ``test_truncate_nfc_slugify_fold_2319.py``.
    """

    def test_truncatechars_no_longer_counts_combining_marks_fixed_by_2319(self) -> None:
        value = "\u00e1bcdefg"  # precomposed, so NFC is a no-op here and only
        # the combining skip could ever have been under test
        decomposed = "a\u0301bcdefg"
        assert unicodedata.normalize("NFC", decomposed) != decomposed, (
            "the value is not decomposed -- it was normalized on the way to disk"
        )
        django_out, djust_out = render_both("{{ p|truncatechars:5 }}", decomposed)
        assert django_out == "\u00e1bcd\u2026"
        # Was "a\u0301bc\u2026" -- one character short, and decomposed.
        assert djust_out == django_out
        # The precomposed spelling of the same text ALREADY agreed, which is
        # what made this a combining-mark bug rather than a counting one.
        assert_agrees("{{ p|truncatechars:5 }}", value)
