"""One rule, three instances: djust must not collapse tuple-ness (#2317, #2321).

Python and Django both preserve the container a sequence arrived in. ``t[:2]``
of a tuple is a tuple; ``isinstance(x, (list, tuple))`` accepts either as a
sublist. djust matched ``Value::List`` alone at several points and rebuilt a
list at others, so the tuple half of each of those decisions was wrong:

===================================== =================== ===================
cell                                  django              djust (before)
===================================== =================== ===================
``{{ p|slice:":2" }}`` on a tuple     ``('a', 'b')``      ``['a', 'b']``
``{{ p|unordered_list }}`` sublist    nested ``<ul>``     escaped tuple repr
===================================== =================== ===================

A third instance of the same rule — the ``PyTuple`` arm of
``filter_registry::mark_input_safety`` — already shipped in #2316. Fixing two
named cells and leaving the class open is the point fix this drain keeps
finding one more instance of, so :class:`TestEveryRebuildSiteIsAccountedFor`
pins the whole enumeration mechanically rather than in prose.

What the fix is NOT
-------------------
It is not an escaping change in either direction. A tuple's ``repr`` was
escaped before and its elements are ``conditional_escape``d after, and the
renderer's safety machinery already matched ``Value::List(x) | Value::Tuple(x)``
at every site, so a tuple reaching a filter is subject to exactly the checks a
list is. ``TestNotMorePermissiveThanDjango`` is the empirical half of that
claim; the two-build differential in ``scripts/filter-parity-differential.py``
is the other half (0 introduced live-payload leaks over 29,723 cells).

Reproduction fidelity
---------------------
``normalize_django_value`` collapses a Python tuple to a list before the
framework paths cross into Rust (``serialization.py``), so **every test here
hands the context to ``_rust.render_template`` un-normalized** — the direct
API a caller uses, and the only entry point through which a ``Value::Tuple``
reaches these filters at all. :class:`TestTheNormalizationBoundary` pins that
premise instead of asserting it in a docstring; a test that went through
``normalize_django_value`` would render a list and pass no matter what the
Rust side does.
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils.html import escape as html_escape  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

FILTERS_RS = (
    Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "filters.rs"
)


def django_render(src: str, value) -> str:
    return DjangoTemplate(src).render(DjangoContext({"p": value}))


def djust_render(src: str, value) -> str:
    # UN-normalized on purpose — see the module docstring.
    return _rust.render_template(src, {"p": value})


def assert_agrees(src: str, value) -> None:
    d, r = django_render(src, value), djust_render(src, value)
    assert r == d, f"{src} on {value!r}: django={d!r} djust={r!r}"


# ---------------------------------------------------------------------------


class TestTheNormalizationBoundary:
    """The premise every other test in this file rests on.

    Stated in both issues and worth pinning rather than trusting: if
    ``normalize_django_value`` ever stopped collapsing tuples, the tests below
    would still pass while measuring a different thing, and if a test here
    accidentally routed through it, it would pass while measuring nothing.
    """

    def test_normalize_collapses_a_tuple_to_a_list(self) -> None:
        assert normalize_django_value({"p": ("a", "b")}) == {"p": ["a", "b"]}

    def test_the_un_normalized_path_keeps_the_tuple(self) -> None:
        # `{{ p }}` renders a tuple's repr with parentheses (#2203), which is
        # the observable proof that `Value::Tuple` survived the crossing.
        assert djust_render("{{ p }}", ("a", "b")) == "(&#x27;a&#x27;, &#x27;b&#x27;)"
        assert djust_render("{{ p }}", ["a", "b"]) == "[&#x27;a&#x27;, &#x27;b&#x27;]"


class TestTheReportedCells:
    """Both issues' reproductions, byte for byte against live Django."""

    def test_2321_slice_of_a_tuple_is_a_tuple(self) -> None:
        assert django_render('{{ p|slice:":2" }}', ("a", "b", "c")) == (
            "(&#x27;a&#x27;, &#x27;b&#x27;)"
        )
        assert_agrees('{{ p|slice:":2" }}', ("a", "b", "c"))

    def test_2321_a_list_input_still_slices_to_a_list(self) -> None:
        assert djust_render('{{ p|slice:":2" }}', ["a", "b", "c"]) == (
            "[&#x27;a&#x27;, &#x27;b&#x27;]"
        )
        assert_agrees('{{ p|slice:":2" }}', ["a", "b", "c"])

    def test_2321_an_empty_slice_of_a_tuple_is_an_empty_tuple(self) -> None:
        # The second of `slice`'s two rebuild sites, and a genuinely separate
        # branch: `()` and `[]` are different reprs.
        assert django_render('{{ p|slice:"5:9" }}', ("a", "b", "c")) == "()"
        assert_agrees('{{ p|slice:"5:9" }}', ("a", "b", "c"))

    def test_2321_the_join_composition_that_already_agreed_still_does(self) -> None:
        # The issue's own note on why no test caught this: every consumer that
        # ITERATES was unaffected. It must stay unaffected.
        assert_agrees('{{ p|slice:":2"|join:"," }}', ("a", "b", "c"))

    def test_2317_a_tuple_is_a_sublist(self) -> None:
        assert_agrees("{{ p|unordered_list }}", ["a", ("b", ("c",))])

    def test_2317_an_empty_tuple_sublist_is_consumed_like_an_empty_list(self) -> None:
        # Django's `walk_items` consumes the empty sequence and emits no
        # `<ul>`; djust emitted a second `<li>()`. Same branch as `["a", []]`,
        # which already agreed — which is the point.
        assert_agrees("{{ p|unordered_list }}", ["a", ()])
        assert_agrees("{{ p|unordered_list }}", ["a", []])

    def test_2317_a_tuple_at_top_level_still_lists_its_items(self) -> None:
        assert_agrees("{{ p|unordered_list }}", ("a", "b", "c"))


class TestTheTwoMechanismsAreIndependentlyReachable:
    """One test per mechanism the fix introduces (#2129/#2135).

    ``slice``'s shape-preserving rebuild and ``unordered_list``'s tuple sublist
    arm are separate edits, and a suite in which every case exercises both
    cannot tell you that either one works. Each case below goes red when only
    its own mechanism is removed.
    """

    def test_only_the_slice_rebuild_decides_this_cell(self) -> None:
        # No `unordered_list` anywhere in the chain.
        assert_agrees('{{ p|slice:"1:" }}', ("a", "b", "c"))

    def test_only_the_unordered_list_sublist_arm_decides_this_cell(self) -> None:
        # No `slice` anywhere in the chain, and the tuple is at the NESTING
        # position, which is the only position the sublist arm reads.
        assert_agrees("{{ p|unordered_list }}", ["a", ("b", "c")])

    def test_both_together(self) -> None:
        # `slice` must hand `unordered_list` a value whose SUBLISTS survive:
        # the tuple element has to stay a tuple through the slice AND be
        # recognised at the far end.
        assert_agrees('{{ p|slice:":2"|unordered_list }}', ["a", ("b", ("c",)), "d"])


class TestNeighbouringSequenceFiltersAreUnchanged:
    """The filters the enumeration decided to LEAVE alone, pinned as agreeing.

    Each of these builds or consumes a sequence and each already matches
    Django; a shape change that over-reached would break one of them.
    """

    @pytest.mark.parametrize(
        "src",
        [
            "{{ p|first }}",
            "{{ p|last }}",
            "{{ p|length }}",
            '{{ p|join:"," }}',
            "{{ p|safeseq }}",
            "{{ p|escapeseq }}",
            "{{ p|make_list }}",
            "{{ p|pprint }}",
            "{{ p }}",
            "{{ p|safe }}",
            "{{ p|dictsort:0 }}",
            "{{ p|dictsortreversed:0 }}",
        ],
    )
    @pytest.mark.parametrize(
        "value",
        [
            ("a", "b", "c"),
            ["a", "b", "c"],
            (),
            ("<b>", "x"),
        ],
    )
    def test_agrees_with_django(self, src, value) -> None:
        assert_agrees(src, value)

    @pytest.mark.parametrize(
        "src",
        [
            "{{ p|first }}",
            "{{ p|last }}",
            "{{ p|length }}",
            '{{ p|join:"," }}',
            "{{ p|escapeseq }}",
            "{{ p|make_list }}",
            "{{ p|pprint }}",
            "{{ p }}",
            "{{ p|safe }}",
            "{{ p|dictsort:0 }}",
            "{{ p|dictsortreversed:0 }}",
            "{{ p|unordered_list }}",
            # Joined the nested table when #2324 landed — see below.
            "{{ p|safeseq }}",
        ],
    )
    @pytest.mark.parametrize(
        "value",
        [
            (("y", 2), ("x", 1)),
            [["y", 2], ["x", 1]],
            ["a", ("b", "c")],
            [("a",), ["b"]],
        ],
    )
    def test_nested_sequences_agree_with_django(self, src, value) -> None:
        """Same table, restricted to the filters that handle NESTED sequences.

        ``safeseq`` joined this table in #2324, which made it replace each item
        with the item's ``str()`` the way ``mark_safe`` does. Before that it was
        the one filter here that read a sublist as a sublist, and its exclusion
        was pinned by ``TestKnownAdjacentDivergences``; that pin is gone and the
        rows it named live in
        ``python/tests/test_safeseq_stringifies_its_items_2324.py``.
        """
        assert_agrees(src, value)


class TestKnownAdjacentDivergences:
    """Divergences this PR deliberately does NOT fix, pinned so they are known.

    Each is pre-existing, shape-INDEPENDENT (it reproduces identically for a
    list and a tuple), and belongs to a different rule than tuple-ness. Pinning
    them is what keeps their exclusion from the tables above honest: if one is
    fixed, this class goes red and points at the row to delete.
    """

    def test_make_list_of_a_tuple_is_the_repr_characters_because_django_says_so(
        self,
    ) -> None:
        # `make_list` is `@stringfilter` + `list(value)` — it collapses the
        # tuple to `str(value)` FIRST, so a list of the repr's characters is
        # Django's answer, not a djust defect. Written down because "make_list
        # rebuilds a sequence" is exactly the shape that invites an
        # over-reaching fix.
        assert djust_render("{{ p|make_list }}", ("a",)).startswith("[&#x27;(&#x27;")
        assert_agrees("{{ p|make_list }}", ("a",))

    def test_dictsort_of_a_tuple_is_a_list_because_sorted_returns_one(self) -> None:
        # `sorted()` returns a list whatever it was given — Python's own answer,
        # so preserving tuple-ness here would be the divergence.
        assert sorted((("y", 2), ("x", 1))) == [("x", 1), ("y", 2)]
        assert djust_render("{{ p|dictsort:0 }}", (("y", 2), ("x", 1))).startswith("[")
        assert_agrees("{{ p|dictsort:0 }}", (("y", 2), ("x", 1)))


# ---------------------------------------------------------------------------
# The randomised half. A curated table samples one axis and goes blind on the
# next; Django is one call away, so prefer asking it (v1.1.1-2 retro).
# ---------------------------------------------------------------------------

LEAVES = ["a", "b", "<b>x</b>", "", "héllo", 42, None, "a < b"]


def _random_shape(rng: random.Random, depth: int = 0, tuple_bias: float = 0.5):
    """A nesting whose SUBLISTS are randomly lists or tuples."""
    out = []
    for _ in range(rng.randint(0, 4)):
        if depth < 4 and rng.random() < 0.4:
            sub = _random_shape(rng, depth + 1, tuple_bias)
            out.append(tuple(sub) if rng.random() < tuple_bias else sub)
        else:
            out.append(rng.choice(LEAVES))
    return out


class TestRandomisedUnorderedListWithTupleSublists:
    """3,000 nestings in which any sublist may be a tuple.

    #2301's sweep is the same generator with tuples excluded — it reported
    3000/3000 while this exact class of divergence was live, because its
    shapes could not construct a tuple at a nesting position. That is the
    enumerate-every-variant lesson (v1.0.0rc4 finding #1) in one sentence.
    """

    def test_three_thousand_mixed_nestings_all_agree(self) -> None:
        rng = random.Random(23172321)  # fixed: the suite must be deterministic
        shapes = [_random_shape(rng) for _ in range(3000)]

        def has_tuple(s) -> bool:
            return any(isinstance(x, tuple) or (isinstance(x, list) and has_tuple(x)) for x in s)

        with_tuple = sum(1 for s in shapes if has_tuple(s))
        assert with_tuple > 500, (
            f"only {with_tuple}/3000 shapes contain a tuple sublist — the generator "
            "stopped producing the case this file is about, so the sweep is vacuous"
        )
        bad = [
            s
            for s in shapes
            if django_render("{{ p|unordered_list }}", s)
            != djust_render("{{ p|unordered_list }}", s)
        ]
        assert not bad, f"{len(bad)}/3000 disagree, first: {bad[0]!r}"


#: Every slice spec, across the whole shape axis.
#:
#: This list used to be two-part non-negative specs ONLY, because djust's
#: ``parse_slice_indices`` clamped instead of wrapping and ignored ``:step``;
#: negative indices and steps were excluded as a separate, pre-existing gap,
#: and a ``test_the_negative_index_gap_is_list_shaped_too`` guard kept that
#: exclusion honest by going red the day it was fixed. #2326 fixed it, so the
#: guard is gone and the specs it named are here — including the one-part form
#: (``"2"`` is ``slice(stop)``, i.e. ``p[:2]``), which the old code read as a
#: START and so answered with the complement.
SLICE_SPECS = [
    ":",
    ":0",
    ":1",
    ":2",
    ":3",
    ":9",
    "0:",
    "1:",
    "2:",
    "3:",
    "9:",
    "1:2",
    "0:3",
    "2:2",
    "1:9",
    # One part — `slice(stop)`.
    "0",
    "2",
    "9",
    "-1",
    # Negative indices, which wrap rather than clamping.
    "-1:",
    ":-1",
    "-2:-1",
    "-9:",
    ":-9",
    "-3:2",
    "1:-1",
    # Steps, including the negative step that reverses and swaps the defaults.
    "::2",
    "1::2",
    ":3:2",
    "::-1",
    "::-2",
    "3:1:-1",
    "-1::-1",
    ":-4:-1",
    "0:4:3",
]


class TestRandomisedSliceShape:
    """Every slice spec × every sequence, tuple and list, against Django."""

    def test_every_supported_spec_agrees_for_both_containers(self) -> None:
        rng = random.Random(2321)
        bad = []
        cells = 0
        for _ in range(200):
            n = rng.randint(0, 5)
            items = [rng.choice(LEAVES) for _ in range(n)]
            for container in (list, tuple):
                value = container(items)
                for spec in SLICE_SPECS:
                    src = '{{ p|slice:"%s" }}' % spec
                    cells += 1
                    if django_render(src, value) != djust_render(src, value):
                        bad.append((spec, value))
        assert cells == 200 * 2 * len(SLICE_SPECS)
        assert not bad, f"{len(bad)}/{cells} disagree, first: {bad[0]!r}"

    def test_every_supported_spec_matches_pythons_own_slice(self) -> None:
        """PYTHON as the reference, not Django — the shape's actual source.

        ``t[:2]`` being a tuple is a language fact; Django agrees with it only
        because its ``slice`` filter is a passthrough. Asserting against
        ``repr(value[a:b])`` says the rule directly, and would still hold if
        Django's implementation changed.
        """
        rng = random.Random(999)
        for _ in range(200):
            items = [rng.choice(["a", "b", "c", "d"]) for _ in range(rng.randint(0, 5))]
            for spec in SLICE_SPECS:
                # Django's own parse: `[None if not x else int(x) for x in
                # arg.split(":")]`, then `slice(*bits)`. One part is
                # `slice(stop)`, which is why this cannot partition on the
                # first colon.
                sl = slice(*[int(x) if x else None for x in spec.split(":")])
                for container in (list, tuple):
                    value = container(items)
                    got = djust_render('{{ p|slice:"%s" }}' % spec, value)
                    want = html_escape(repr(value[sl]))
                    assert got == want, (
                        f"slice:{spec!r} of {value!r}: djust={got!r} python={want!r}"
                    )

    def test_the_specs_the_old_gap_excluded_are_now_in_slice_specs(self) -> None:
        """#2326 is fixed, so its specs are measured rather than excluded.

        The guard this replaces asserted the negative-index gap still EXISTED,
        and named itself as the thing to delete once it did not. Rather than
        delete it outright, it inverts: the exclusion is gone, and this pins
        that the specs it excluded really did come back — otherwise "extend
        SLICE_SPECS" could be quietly skipped and the coverage lost with the
        guard that was protecting it.
        """
        for spec in ("-1:", ":-1", "::2", "::-1", "2"):
            assert spec in SLICE_SPECS, (
                f"slice:{spec!r} was excluded by the pre-#2326 gap and must now "
                "be measured by TestRandomisedSliceShape"
            )


class TestNotMorePermissiveThanDjango:
    """The hard constraint: no cell may emit live markup Django escapes.

    Four XSSes were fixed in this machinery in one week. A shape fix has no
    business granting a capability, and the way that claim is checked is by
    running it rather than by reading the diff.
    """

    HOSTILE = "<img src=x onerror=alert(1)>"
    FRAGMENTS = ["<img", "onerror="]

    @pytest.mark.parametrize(
        "src",
        [
            "{{ p|unordered_list }}",
            '{{ p|slice:":2" }}',
            '{{ p|slice:":2"|join:"," }}',
            '{{ p|slice:":2"|unordered_list }}',
            '{{ p|slice:":2"|safe }}',
            "{{ p|safeseq|unordered_list }}",
            '{{ p|safeseq|slice:":2" }}',
            '{{ p|escapeseq|slice:":2"|join:"," }}',
            "{{ p|unordered_list|safe }}",
            '{{ p|slice:":2"|pprint }}',
        ],
    )
    @pytest.mark.parametrize("container", [list, tuple])
    def test_djust_leaks_no_fragment_django_does_not(self, src, container) -> None:
        for value in (
            container([self.HOSTILE, "x"]),
            container(["a", container([self.HOSTILE])]),
        ):
            dj = django_render(src, value)
            du = djust_render(src, value)
            for frag in self.FRAGMENTS:
                if frag in du:
                    assert frag in dj, (
                        f"{src} on {value!r} emits {frag!r} live where Django does not:\n"
                        f"  django={dj!r}\n  djust ={du!r}"
                    )


# ---------------------------------------------------------------------------
# The mechanical enumeration. "N places rebuild a sequence" must be a grep,
# not a sentence.
# ---------------------------------------------------------------------------


def _production_lines() -> list[tuple[int, str]]:
    """``filters.rs`` with every ``#[cfg(test)]`` module removed, and with
    ``//`` comments stripped.

    Comments must not decide the verdict — only real code does, which is the
    same rule ``test_bool_before_int_converters_2212._strip_comments`` states.
    Without it, a comment that merely NAMES ``Value::List(items)`` while
    explaining an adjacent arm reads as a new construction site: #2340 tripped
    exactly that, and "reword the comment" would have been fixing the prose to
    suit the grep.
    """
    lines = [
        # Only a whole-line or trailing `//`; a `//` inside a string literal
        # would be mangled, and `filters.rs` has none on a `Value::List(` line.
        ln.split("//", 1)[0] if "//" in ln else ln
        for ln in FILTERS_RS.read_text().splitlines()
    ]
    skip: set[int] = set()
    i = 0
    while i < len(lines):
        if lines[i].strip() == "#[cfg(test)]":
            j = i
            while j < len(lines) and not lines[j].startswith("}"):
                skip.add(j)
                j += 1
            skip.add(j)
            i = j
        i += 1
    return [(n + 1, ln) for n, ln in enumerate(lines) if n not in skip]


class TestEveryRebuildSiteIsAccountedFor:
    """Pins the enumeration this PR's decision table is written against.

    Five production sites in ``filters.rs`` construct a ``Value::List``. Four
    are list-always because Django's own implementation is a list
    comprehension or ``sorted()``; one is ``slice``'s single sequence exit,
    which rebuilds in the input's shape. (It was two until #2326 collapsed
    ``slice``'s populated and empty branches into one.) A sixth site —
    ``unordered_list``'s sublist test — MATCHED ``Value::List`` alone and now
    matches both.

    A new sequence filter that builds a bare ``Value::List`` fails this test,
    which is the point: it forces the author to say which column it belongs in.
    """

    #: FILTER site → (how many times that exact line appears, why it is allowed
    #: to produce a list unconditionally). Every reason is Django's own
    #: implementation, not a djust convention.
    #:
    #: The multiplicity is pinned, not just the set: ``safeseq`` and
    #: ``escapeseq`` now spell their construction identically — both map every
    #: item through a function, since #2324 made ``safeseq``'s items
    #: ``str(item)`` the way ``mark_safe`` does — so a set alone could not tell
    #: two such sites from three.
    LIST_ALWAYS = {
        "Ok(Value::List(items))": (
            1,
            "dictsort/dictsortreversed — Django is sorted(), which returns a list",
        ),
        "Ok(Value::List(chars))": (
            1,
            "make_list — Django is @stringfilter + list(str(value))",
        ),
        "Value::List(": (
            2,
            "safeseq and escapeseq — Django is a list comprehension in both",
        ),
    }

    #: Not a filter: ``rebuild_like``'s own total-function default, for a
    #: non-sequence input no caller can reach today. Enumerated rather than
    #: excluded — a wildcard arm producing a list is exactly the shape this
    #: test exists to make somebody justify.
    HELPER_DEFAULT = {"_ => Value::List(items),": (1, "rebuild_like's default arm")}

    #: ``slice`` routes through the helper on its ONE sequence exit. It had two
    #: — populated and empty — until #2326 replaced the hand-rolled index math
    #: with Python's own slice algorithm, which computes a (possibly empty)
    #: position list and rebuilds from it unconditionally. One exit is the
    #: STRONGER form of the same guarantee: with two, the pin was "neither
    #: branch forgot"; with one there is no branch left that could.
    #:
    #: Pinned as a COUNT inside ``apply_slice`` rather than as literal source
    #: lines, because ``rustfmt`` rewraps a long call across lines and a
    #: line-literal pin would go red on formatting rather than on meaning.
    SHAPE_PRESERVING_CALLS = 1

    #: The prose claim this PR makes, as a number.
    TOTAL_REBUILD_SITES = 5

    def test_every_bare_list_site_is_one_of_the_documented_list_always_four(self) -> None:
        """The grep that catches BOTH halves of the rule.

        A bare ``Value::List(x)`` — with no ``| Value::Tuple(x)`` on the line —
        is either a construction that collapses a tuple on the way OUT
        (``slice``) or a match that fails to see one on the way IN
        (``unordered_list``'s sublist test, #2317). One expression finds both,
        which is why there is no second test asserting the match half
        separately.
        """
        built = [
            (n, ln.strip())
            for n, ln in _production_lines()
            if "Value::List(" in ln and "| Value::Tuple(" not in ln
        ]
        found = Counter(ln for _, ln in built)
        expected = Counter(
            {
                line: count
                for line, (count, _reason) in {
                    **self.LIST_ALWAYS,
                    **self.HELPER_DEFAULT,
                }.items()
            }
        )
        assert found == expected, (
            "the bare Value::List sites in filters.rs changed.\n"
            f"  new/more:  {sorted((found - expected).elements())}\n"
            f"  gone/less: {sorted((expected - found).elements())}\n"
            "Every one is a place tuple-ness can be lost (#2317/#2321). Decide it "
            "explicitly: add it to LIST_ALWAYS with Django's own reason for "
            "returning a list, route it through rebuild_like(), or pair the match "
            "with | Value::Tuple(x)."
        )
        assert len(built) == sum(expected.values())

    def test_slices_sequence_exit_routes_through_the_shared_helper(self) -> None:
        """The other column of the table, and the count the PR body states.

        A separate grep from the one above, because after the fix ``slice``'s
        branches contain no ``Value::List`` at all — they are invisible to it.
        """
        lines = _production_lines()
        # The body of `fn apply_slice`, from its signature to the next
        # column-0 `}`.
        start = next(n for n, ln in lines if ln.startswith("fn apply_slice("))
        end = next(n for n, ln in lines if n > start and ln.startswith("}"))
        in_slice = [(n, ln) for n, ln in lines if start < n < end and "rebuild_like(" in ln]
        assert len(in_slice) == self.SHAPE_PRESERVING_CALLS, (
            "apply_slice's sequence arm must return through rebuild_like, and "
            "must have exactly one exit that does — a second would be a branch "
            f"that could forget (#2321/#2326); found {len(in_slice)}: {in_slice}"
        )
        # And nothing else in production calls it, so the helper has exactly
        # the two consumers the decision table names.
        all_calls = [
            (n, ln)
            for n, ln in lines
            if "rebuild_like(" in ln and not ln.startswith("fn rebuild_like(")
        ]
        assert len(all_calls) == self.SHAPE_PRESERVING_CALLS, (
            f"rebuild_like gained a caller outside apply_slice: {all_calls}"
        )
        list_always_sites = sum(count for count, _reason in self.LIST_ALWAYS.values())
        assert list_always_sites + self.SHAPE_PRESERVING_CALLS == self.TOTAL_REBUILD_SITES
