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

        ``safeseq`` is absent on purpose — see
        :class:`TestKnownAdjacentDivergences` (#2324).
        """
        assert_agrees(src, value)


class TestKnownAdjacentDivergences:
    """Divergences this PR deliberately does NOT fix, pinned so they are known.

    Each is pre-existing, shape-INDEPENDENT (it reproduces identically for a
    list and a tuple), and belongs to a different rule than tuple-ness. Pinning
    them is what keeps their exclusion from the tables above honest: if one is
    fixed, this class goes red and points at the row to delete.
    """

    def test_safeseq_does_not_stringify_its_items(self) -> None:
        """#2324. ``mark_safe(obj)`` is ``SafeString(obj)`` — a ``str`` for ANY input.

        Django's ``safeseq`` therefore replaces every item with its ``str()``;
        djust keeps the typed value. Visible only on a direct render or when a
        LATER filter reads the item's type — ``{{ p|safeseq|unordered_list }}``
        nests in djust because the item is still a sequence, where Django sees
        the string it made of it.

        Not fixable inside this PR's rule: matching ``mark_safe`` needs a
        Python-``str()``-of-a-``Value`` helper, and ``Value``'s ``Display`` is
        deliberately Django's ``numberformat.format`` for ``Float`` and
        ``Decimal`` (see the arms in ``djust_core/src/lib.rs``), so ``1e20``
        and ``Decimal('1E-9')`` would need their own spelling. That is a
        numeric-formatting change, not a container-shape one. Filed as #2324.
        """
        for value in (["<b>", ["c", ["d"]]], ["<b>", ("c", ("d",))]):
            src = "{{ p|safeseq|unordered_list }}"
            assert djust_render(src, value) != django_render(src, value), (
                f"safeseq now agrees with Django for {value!r} — the gap this "
                "test pins is closed. Delete this test and restore safeseq to "
                "TestNeighbouringSequenceFiltersAreUnchanged's nested table."
            )
        # The point of the pin: the divergence is the SAME for both containers,
        # so it is not the tuple-ness rule and #2317 did not introduce it.
        assert djust_render("{{ p|safeseq }}", [1, 2]) != django_render(
            "{{ p|safeseq }}", [1, 2]
        ), "a plain list of ints diverges too — the gap is not about tuples"

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


#: Slice specs djust's ``parse_slice_indices`` supports: two parts, both
#: non-negative or empty. Negative indices and a step are a SEPARATE,
#: pre-existing gap (``{{ p|slice:"-1:" }}`` diverges for a list too), so
#: including them here would measure that instead of the shape axis this file
#: is about. :meth:`test_the_negative_index_gap_is_list_shaped_too` pins that
#: separation so the exclusion cannot quietly become a blind spot.
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
                lo, _, hi = spec.partition(":")
                sl = slice(int(lo) if lo else None, int(hi) if hi else None)
                for container in (list, tuple):
                    value = container(items)
                    got = djust_render('{{ p|slice:"%s" }}' % spec, value)
                    want = html_escape(repr(value[sl]))
                    assert got == want, (
                        f"slice:{spec!r} of {value!r}: djust={got!r} python={want!r}"
                    )

    def test_the_negative_index_gap_is_list_shaped_too(self) -> None:
        """#2326: a pre-existing, SHAPE-INDEPENDENT divergence, not fixed here.

        ``parse_slice_indices`` clamps rather than wrapping, so negative
        indices diverge for a list exactly as much as for a tuple. Pinning it
        keeps ``SLICE_SPECS``'s exclusion honest: it is excluded because it is
        a different bug, not because the tuple half of it fails.
        """
        src = '{{ p|slice:"-1:" }}'
        as_list = djust_render(src, ["a", "b", "c"])
        assert as_list != django_render(src, ["a", "b", "c"]), (
            "the #2326 negative-index gap has been fixed — extend SLICE_SPECS and delete this test"
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
    """``filters.rs`` with every ``#[cfg(test)]`` module removed."""
    lines = FILTERS_RS.read_text().splitlines()
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

    Six production sites in ``filters.rs`` construct a ``Value::List``. Four
    are list-always because Django's own implementation is a list
    comprehension or ``sorted()``; two are ``slice``'s and now rebuild in the
    input's shape. A seventh site — ``unordered_list``'s sublist test — MATCHED
    ``Value::List`` alone and now matches both.

    A new sequence filter that builds a bare ``Value::List`` fails this test,
    which is the point: it forces the author to say which column it belongs in.
    """

    #: FILTER site → why it is allowed to produce a list unconditionally.
    #: Every reason is Django's own implementation, not a djust convention.
    LIST_ALWAYS = {
        "Ok(Value::List(items))": "dictsort/dictsortreversed — Django is sorted(), which returns a list",
        "Ok(Value::List(chars))": "make_list — Django is @stringfilter + list(str(value))",
        "Value::List(items),": "safeseq — Django is a list comprehension",
        "Value::List(": "escapeseq — Django is a list comprehension",
    }

    #: Not a filter: ``rebuild_like``'s own total-function default, for a
    #: non-sequence input no caller can reach today. Enumerated rather than
    #: excluded — a wildcard arm producing a list is exactly the shape this
    #: test exists to make somebody justify.
    HELPER_DEFAULT = {"_ => Value::List(items),"}

    #: ``slice`` has exactly two return branches — populated and empty — and
    #: both must route through the helper. Pinned as a COUNT inside
    #: ``apply_slice`` rather than as literal source lines, because ``rustfmt``
    #: rewraps a long call across lines and a line-literal pin would go red on
    #: formatting rather than on meaning.
    SHAPE_PRESERVING_CALLS = 2

    #: The prose claim this PR makes, as a number.
    TOTAL_REBUILD_SITES = 6

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
        found = {ln for _, ln in built}
        expected = set(self.LIST_ALWAYS) | self.HELPER_DEFAULT
        assert found == expected, (
            "the set of bare Value::List sites in filters.rs changed.\n"
            f"  new/changed: {sorted(found - expected)}\n"
            f"  gone:        {sorted(expected - found)}\n"
            "Every one is a place tuple-ness can be lost (#2317/#2321). Decide it "
            "explicitly: add it to LIST_ALWAYS with Django's own reason for "
            "returning a list, route it through rebuild_like(), or pair the match "
            "with | Value::Tuple(x)."
        )
        assert len(built) == len(expected)

    def test_both_slice_branches_route_through_the_shared_helper(self) -> None:
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
            "apply_slice must route BOTH its return branches — populated and "
            f"empty — through rebuild_like; found {len(in_slice)}: {in_slice}"
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
        assert len(self.LIST_ALWAYS) + self.SHAPE_PRESERVING_CALLS == self.TOTAL_REBUILD_SITES
