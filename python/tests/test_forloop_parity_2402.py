"""`{{ forloop.* }}` binds Django's seven loop members (#2402).

The defect
----------
Django's ``ForNode.render`` writes ``context["forloop"]`` — a dict carrying
``parentloop``, ``counter0``, ``counter``, ``revcounter``, ``revcounter0``,
``first`` and ``last``, updated on every iteration. djust's ``Node::For``
bound none of them, so every one of those names MISSED and rendered
``string_if_invalid``:

    {% for a in p %}[{{ forloop.counter }}]{% endfor %}   over [1, 2, 3]
      django  '[1][2][3]'          djust  '[][][]'

A numbered list with no numbers, ``{% if forloop.first %}`` never true, and
``{% if not forloop.last %},{% endif %}`` a comma after the last element —
silent under-render, with no error anywhere. Same class as
``{% for x in p|slice %}`` (#2325), ``{% for k in d %}`` (#2334) and
``{% for a,b in p %}`` (#2377): the whole region disappears and nothing says so.

Why the differential could not see it
-------------------------------------
No cell in ``PATH_SHAPES`` / ``TAG_SHAPES`` / ``BUILTIN_SHAPES`` referenced a
``forloop`` name, so the tool reported clean over the entire corpus while all
seven members were empty. ``TestTheCorpusGapThatHidThisFromTheDifferential``
pins that the corpus can now construct a cell per member, and that the
requirement is read out of Django's own source rather than transcribed.

Every expectation here is LIVE Django, never a transcription.
"""

import pathlib
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template.defaulttags import ForNode
from django.utils.safestring import mark_safe

from djust import _rust
from djust.mixins.rust_bridge import _collect_safe_keys

REPO = pathlib.Path(__file__).resolve().parents[2]

#: The members, in the order `ForNode.render` writes them — which is also the
#: order `{{ forloop }}` renders them in, since a Python dict keeps insertion
#: order. Read off Django's source below rather than trusted from this list.
MEMBERS = (
    "parentloop",
    "counter0",
    "counter",
    "revcounter",
    "revcounter0",
    "first",
    "last",
)

MARKED = mark_safe("<b>ok</b>")
HOSTILE = "<script>alert(1)</script>"


def _safe_keys(ctx: dict) -> list[str]:
    keys: list[str] = []
    for key, value in ctx.items():
        keys.extend(_collect_safe_keys(value, key))
    return keys


def render_both(tpl: str, ctx: dict) -> tuple[str, str]:
    django_out = DjangoTemplate(tpl).render(DjangoContext(dict(ctx)))
    safe_keys = _safe_keys(ctx)
    djust_out = _rust.render_template_with_dirs(tpl, ctx, [], safe_keys or None)
    return django_out, djust_out


def assert_agrees(tpl: str, ctx: dict) -> str:
    django_out, djust_out = render_both(tpl, ctx)
    assert djust_out == django_out, (
        f"{tpl!r} over {ctx!r}\n  django {django_out!r}\n  djust  {djust_out!r}"
    )
    return djust_out


#: Until #2519 the plain render entry used here leaked the LiveView VDOM
#: `<!--dj-if …-->` boundary marker (#1832) into its output, so every
#: `{% if %}` cell in this file had to compare modulo the marker and
#: `TestTheDjIfMarkerIsOrthogonal` was a tripwire that failed the day the leak
#: was fixed ("the marker artifact has gone — drop the stripping"). It fired in
#: #2519: the plain entries now render with `emit_dj_if_markers` off, so the
#: cells compare byte-for-byte and the class below pins the absence instead.
IF_MARKER = re.compile(r"<!--/?dj-if(?: id=\"[^\"]*\")?-->")


class TestEverySevenMembers:
    """One case per member, over the sequence lengths that move each one."""

    @pytest.mark.parametrize("member", MEMBERS)
    @pytest.mark.parametrize("seq", [[1], [1, 2], [1, 2, 3], list(range(8))])
    def test_member_agrees_with_django(self, member, seq):
        assert_agrees("{%% for a in p %%}[{{ forloop.%s }}]{%% endfor %%}" % member, {"p": seq})

    def test_no_member_renders_empty(self):
        """The symptom itself: every member produced `''` before the fix.

        Asserted as a PROPERTY of the output rather than by comparing to
        Django, so it goes red on the original defect even if Django's own
        answer were somehow also empty.
        """
        for member in MEMBERS:
            out = _rust.render_template(
                "{%% for a in p %%}[{{ forloop.%s }}]{%% endfor %%}" % member, {"p": [1, 2, 3]}
            )
            assert out != "[][][]", f"forloop.{member} still renders empty: {out!r}"

    def test_the_whole_dict_renders_in_djangos_key_order(self):
        """`{{ forloop }}` — the one cell that sees a member added or REORDERED."""
        out = assert_agrees("{% for a in p %}[{{ forloop }}]{% endfor %}", {"p": [1, 2]})
        # The repr is HTML-escaped by both engines, so the quotes are entities.
        order = [m for m in re.findall(r"#x27;(\w+)&#x27;:", out)]
        assert order[: len(MEMBERS)] == list(MEMBERS), order

    def test_an_unknown_member_is_still_empty(self):
        assert_agrees("{% for a in p %}[{{ forloop.nope }}]{% endfor %}", {"p": [1, 2]})


class TestArithmeticBoundaries:
    """counter/counter0 differ by one; revcounter needs the LENGTH."""

    def test_counter_and_counter0_are_offset_by_one(self):
        assert_agrees(
            "{% for a in p %}[{{ forloop.counter }}/{{ forloop.counter0 }}]{% endfor %}",
            {"p": [1, 2, 3]},
        )

    def test_revcounter_counts_down_to_one_and_revcounter0_to_zero(self):
        assert_agrees(
            "{% for a in p %}[{{ forloop.revcounter }}/{{ forloop.revcounter0 }}]{% endfor %}",
            {"p": [1, 2, 3, 4]},
        )

    def test_a_single_element_loop_is_both_first_and_last(self):
        out = assert_agrees(
            "{% for a in p %}[{{ forloop.first }}{{ forloop.last }}{{ forloop.revcounter }}]"
            "{% endfor %}",
            {"p": [9]},
        )
        assert out == "[TrueTrue1]", out

    def test_counter_meets_revcounter_at_the_middle(self):
        assert_agrees(
            "{% for a in p %}{% if forloop.counter == forloop.revcounter %}M{% endif %}"
            "{% endfor %}",
            {"p": [1, 2, 3]},
        )


class TestReversedUsesTheIterationOrdinalNotTheItemIndex:
    """Django reverses the sequence and THEN enumerates.

    The one shape where the render ordinal and the item's own index disagree.
    An implementation reading the item index — which the dj-if loop path
    (`Context::dj_if_loop_path`, #1832/#2529) deliberately does — agrees on
    every forward loop and silently reverses the
    numbering here, so this is the case that distinguishes the two.
    """

    def test_counter_counts_up_in_render_order(self):
        out = assert_agrees(
            "{% for a in p reversed %}[{{ a }}:{{ forloop.counter }}]{% endfor %}",
            {"p": [1, 2, 3]},
        )
        assert out == "[3:1][2:2][1:3]", out

    def test_first_is_the_first_item_rendered(self):
        out = assert_agrees(
            "{% for a in p reversed %}[{{ forloop.first }}{{ forloop.last }}]{% endfor %}",
            {"p": [1, 2, 3]},
        )
        assert out == "[TrueFalse][FalseFalse][FalseTrue]", out

    def test_revcounter_counts_down_in_render_order(self):
        assert_agrees(
            "{% for a in p reversed %}[{{ forloop.revcounter }}]{% endfor %}", {"p": [1, 2, 3]}
        )


class TestParentloopAndNesting:
    def test_parentloop_is_an_empty_dict_at_the_outermost_level(self):
        """NOT missing — Django's `parentloop = {}` renders `{}`."""
        out = assert_agrees("{% for a in p %}[{{ forloop.parentloop }}]{% endfor %}", {"p": [1, 2]})
        assert out == "[{}][{}]", out

    def test_a_member_of_the_outermost_parentloop_is_empty(self):
        assert_agrees("{% for a in p %}[{{ forloop.parentloop.counter }}]{% endfor %}", {"p": [1]})

    def test_parentloop_counter_inside_two_loops(self):
        out = assert_agrees(
            "{% for a in p %}{% for b in p %}[{{ forloop.parentloop.counter }}."
            "{{ forloop.counter }}]{% endfor %}{% endfor %}",
            {"p": [1, 2]},
        )
        assert out == "[1.1][1.2][2.1][2.2]", out

    def test_parentloop_first_and_last_track_the_outer_loop(self):
        assert_agrees(
            "{% for a in p %}{% for b in p %}[{{ forloop.parentloop.first }}"
            "{{ forloop.parentloop.last }}]{% endfor %}{% endfor %}",
            {"p": [1, 2, 3]},
        )

    def test_parentloop_chains_three_deep(self):
        assert_agrees(
            "{% for a in p %}{% for b in p %}{% for c in p %}"
            "[{{ forloop.parentloop.parentloop.counter }}]{% endfor %}{% endfor %}{% endfor %}",
            {"p": [1, 2]},
        )

    def test_the_outer_forloop_is_restored_after_an_inner_loop_finishes(self):
        out = assert_agrees(
            "{% for a in p %}{% for b in p %}{% endfor %}[{{ forloop.counter }}]{% endfor %}",
            {"p": [1, 2, 3]},
        )
        assert out == "[1][2][3]", out

    def test_forloop_is_gone_after_the_loop(self):
        assert_agrees("{% for a in p %}{% endfor %}[{{ forloop.counter }}]!", {"p": [1, 2]})

    def test_reversed_outer_with_a_forward_inner(self):
        assert_agrees(
            "{% for a in p reversed %}{% for b in p %}[{{ forloop.parentloop.counter }}"
            ":{{ forloop.counter }}]{% endfor %}{% endfor %}",
            {"p": [1, 2, 3]},
        )


class TestEmptyBranch:
    """Django writes `loop_dict` only AFTER the `len(values) < 1` early return."""

    def test_forloop_does_not_leak_into_a_top_level_empty_branch(self):
        out = assert_agrees(
            "{% for a in e %}x{% empty %}[{{ forloop.counter }}]{% endfor %}", {"e": []}
        )
        assert out == "[]", out

    def test_a_nested_empty_branch_sees_the_OUTER_forloop(self):
        out = assert_agrees(
            "{% for a in p %}{% for b in e %}x{% empty %}[{{ forloop.counter }}]{% endfor %}"
            "{% endfor %}",
            {"p": [1, 2], "e": []},
        )
        assert out == "[1][2]", out

    def test_a_doubly_nested_empty_branch_sees_the_innermost_live_loop(self):
        assert_agrees(
            "{% for a in p %}{% for b in p %}{% for c in e %}x{% empty %}"
            "[{{ forloop.counter }}]{% endfor %}{% endfor %}{% endfor %}",
            {"p": [1, 2], "e": []},
        )

    def test_a_non_iterable_operand_takes_the_empty_branch_with_no_forloop(self):
        assert_agrees(
            "{% for a in n %}x{% empty %}[{{ forloop.counter }}]{% endfor %}", {"n": None}
        )


class TestEveryOperandShapeTheLoopNormalises:
    """The dict / view / string paths #2334, #2340 and #2325 rewrote.

    A `forloop` implementation that read the operand's own length rather than
    the NORMALISED sequence's would answer `revcounter` from the wrong number
    on every one of these.
    """

    def test_a_bare_dict_iterates_its_keys(self):
        out = assert_agrees(
            "{% for k in d %}[{{ k }}:{{ forloop.counter }}/{{ forloop.revcounter }}]{% endfor %}",
            {"d": {"x": 1, "y": 2, "z": 3}},
        )
        assert out == "[x:1/3][y:2/2][z:3/1]", out

    def test_dict_items_unpacked(self):
        assert_agrees(
            "{% for k, v in d.items %}[{{ k }}{{ v }}:{{ forloop.counter }}]{% endfor %}",
            {"d": {"x": 1, "y": 2}},
        )

    def test_dict_keys_view(self):
        assert_agrees(
            "{% for k in d.keys %}[{{ forloop.last }}]{% endfor %}", {"d": {"x": 1, "y": 2}}
        )

    def test_dict_values_view(self):
        assert_agrees(
            "{% for v in d.values %}[{{ v }}:{{ forloop.revcounter0 }}]{% endfor %}",
            {"d": {"x": 1, "y": 2}},
        )

    def test_a_string_iterates_by_character(self):
        out = assert_agrees(
            "{% for c in s %}[{{ c }}{{ forloop.counter }}]{% endfor %}", {"s": "abc"}
        )
        assert out == "[a1][b2][c3]", out

    def test_a_filtered_operand_counts_the_FILTERED_length(self):
        out = assert_agrees(
            "{% for a in p|slice:':2' %}[{{ forloop.counter }}/{{ forloop.revcounter }}]"
            "{% endfor %}",
            {"p": [1, 2, 3, 4, 5]},
        )
        assert out == "[1/2][2/1]", out

    def test_a_tuple_operand(self):
        assert_agrees(
            "{% for a in t %}[{{ forloop.counter }}{{ forloop.last }}]{% endfor %}",
            {"t": (7, 8, 9)},
        )


class TestUnpackingBothSpellings:
    """#2377's two spellings, now carrying a `forloop` reference."""

    @pytest.mark.parametrize("names", ["a, b", "a,b", "a ,b"])
    def test_forloop_counts_the_ROWS_not_the_components(self, names):
        out = assert_agrees(
            "{%% for %s in rows %%}[{{ forloop.counter }}:{{ a }}{{ b }}]{%% endfor %%}" % names,
            {"rows": [(1, 2), (3, 4), (5, 6)]},
        )
        assert out == "[1:12][2:34][3:56]", out

    def test_three_names(self):
        assert_agrees(
            "{% for a,b,c in rows %}[{{ forloop.revcounter }}]{% endfor %}",
            {"rows": [(1, 2, 3), (4, 5, 6)]},
        )


class TestCoordinatingTagsInsideTheLoop:
    def test_cycle_advances_alongside_the_counter(self):
        out = assert_agrees(
            "{% for a in p %}[{% cycle 'x' 'y' %}{{ forloop.counter }}]{% endfor %}",
            {"p": [1, 2, 3]},
        )
        assert out == "[x1][y2][x3]", out

    def test_cycle_can_take_forloop_as_an_operand(self):
        assert_agrees(
            "{% for a in p %}{% cycle forloop.counter 'z' %}{% endfor %}", {"p": [1, 2, 3]}
        )

    def test_firstof_resolves_forloop(self):
        assert_agrees(
            "{% for a in p %}{% firstof forloop.counter 'F' %}{% endfor %}", {"p": [1, 2]}
        )

    def test_widthratio_resolves_forloop(self):
        assert_agrees(
            "{% for a in p %}{% widthratio forloop.counter 3 100 %}|{% endfor %}", {"p": [1, 2, 3]}
        )

    def test_with_binds_a_forloop_member(self):
        assert_agrees(
            "{% for a in p %}{% with c=forloop.counter %}[{{ c }}]{% endwith %}{% endfor %}",
            {"p": [1, 2, 3]},
        )

    def test_the_comma_separator_idiom(self):
        out = assert_agrees(
            "{% for a in p %}{{ forloop.counter }}{% if not forloop.last %},{% endif %}{% endfor %}",
            {"p": [1, 2, 3]},
        )
        assert out.startswith("1,2,3"), out


class TestTheDjIfMarkerIsOrthogonal:
    """`{% if %}` in a loop, with NO forloop present, agrees byte-for-byte.

    The inverse of the tripwire this class used to be: since #2519 the plain
    render entry emits no `<!--dj-if …-->` boundary marker, so a forloop-free
    conditional inside a loop is byte-equal to Django and the marker regex
    matches nothing. If a marker ever leaks back into the plain path, the
    second assertion names it before any forloop cell can be blamed.
    """

    def test_a_forloop_free_if_in_a_loop_agrees_byte_for_byte(self):
        tpl = "{% for a in p %}{% if a %}Y{% endif %}{% endfor %}"
        ctx = {"p": [1, 0, 1]}
        django_out, djust_out = render_both(tpl, ctx)
        assert "forloop" not in tpl
        assert djust_out == django_out, (django_out, djust_out)
        assert IF_MARKER.search(djust_out) is None, djust_out


class TestFilterChainsOnForloop:
    @pytest.mark.parametrize(
        "expr",
        [
            "forloop.counter|add:'10'",
            "forloop.first|yesno",
            "forloop.counter|stringformat:'03d'",
            "forloop.counter0|divisibleby:'2'",
            "forloop|length",
            "forloop|pprint",
        ],
    )
    def test_chain_agrees(self, expr):
        assert_agrees("{%% for a in p %%}[{{ %s }}]{%% endfor %%}" % expr, {"p": [1, 2, 3]})


class TestShadowing:
    """`forloop` is a name like any other until the loop takes it."""

    def test_the_loop_shadows_a_context_variable_named_forloop(self):
        out = assert_agrees(
            "[{{ forloop.counter }}]{% for a in p %}[{{ forloop.counter }}]{% endfor %}"
            "[{{ forloop.counter }}]",
            {"p": [1], "forloop": {"counter": "OUTER"}},
        )
        assert out == "[OUTER][1][OUTER]", out

    def test_a_loop_variable_spelled_forloop_wins_over_the_dict(self):
        """Django writes `loop_dict` at the top of the iteration and the loop
        variable at the bottom, so the ITEM is what `{{ forloop }}` renders."""
        out = assert_agrees("{% for forloop in p %}[{{ forloop }}]{% endfor %}", {"p": [7, 8]})
        assert out == "[7][8]", out

    def test_a_loop_variable_spelled_forloop_hides_the_members(self):
        assert_agrees("{% for forloop in p %}[{{ forloop.counter }}]{% endfor %}", {"p": [7, 8]})

    def test_a_dict_whose_KEY_is_a_member_name_does_not_reach_forloop(self):
        assert_agrees(
            "{% for k in d %}[{{ forloop.counter }}]{% endfor %}",
            {"d": {"counter": 1, "first": 2, "parentloop": 3}},
        )


class TestTheEscapingDirection:
    """No `forloop` path may open a channel that skips escaping.

    Seven live-payload classes were fixed in this machinery in one week, so
    "a counter is inert" is checked rather than assumed. Two directions
    matter: the engine's own dict must be escaped like any other value, and a
    grant on a SHADOWED context variable named `forloop` must not survive into
    the body and license emitting it raw.
    """

    def test_a_marked_context_forloop_does_not_grant_the_engines_dict(self):
        out = assert_agrees(
            "{% for a in p %}[{{ forloop }}]{% endfor %}",
            {"p": [1], "forloop": mark_safe(HOSTILE)},
        )
        assert "<script>" not in out, out
        assert "&lt;script&gt;" in out, out

    def test_a_marked_member_of_a_shadowed_forloop_does_not_grant_the_counter(self):
        out = assert_agrees(
            "{% for a in p %}[{{ forloop.counter }}]{% endfor %}",
            {"p": [1], "forloop": {"counter": mark_safe(HOSTILE)}},
        )
        assert out == "[1]", out

    def test_the_marked_outer_value_is_still_live_OUTSIDE_the_loop(self):
        """The revoke must not leak out of the loop and over-escape the
        original — otherwise the fix would be a silent behaviour change for
        every page that has a `forloop` context variable."""
        out = assert_agrees(
            "[{{ forloop }}]{% for a in p %}x{% endfor %}[{{ forloop }}]",
            {"p": [1], "forloop": mark_safe(HOSTILE)},
        )
        assert out == f"[{HOSTILE}]x[{HOSTILE}]", out

    def test_an_alias_onto_a_marked_forloop_made_before_the_loop(self):
        assert_agrees(
            "{% with q=forloop %}{% for a in p %}[{{ q }}][{{ forloop.counter }}]{% endfor %}"
            "{% endwith %}",
            {"p": [1], "forloop": {"counter": mark_safe(HOSTILE)}},
        )

    def test_an_alias_onto_the_engines_own_dict_made_inside_the_loop(self):
        assert_agrees(
            "{% for a in p %}{% with q=forloop %}[{{ q.counter }}]{% endwith %}{% endfor %}",
            {"p": [1, 2]},
        )

    def test_hostile_ITEMS_are_unaffected_by_the_forloop_binding(self):
        out = assert_agrees(
            "{% for a in h %}[{{ forloop.counter }}{{ a }}]{% endfor %}",
            {"h": [HOSTILE, MARKED]},
        )
        assert out == f"[1{HOSTILE.replace('<', '&lt;').replace('>', '&gt;')}][2{MARKED}]", out

    def test_the_dict_repr_is_escaped_like_any_other_value(self):
        out = _rust.render_template("{% for a in p %}{{ forloop }}{% endfor %}", {"p": [1]})
        assert "&#x27;" in out and "'parentloop'" not in out, out


class TestTheCorpusGapThatHidThisFromTheDifferential:
    """The corpus can now construct a cell per member, mechanically (#2402).

    A corpus gap is silent by construction — the tool reported 0 MISSING on
    nine axes over ~315,000 cells while all seven members rendered empty,
    because no cell referenced one. These pin the closure so it cannot be
    quietly dropped.
    """

    def _differential(self):
        pytest.importorskip("django")
        source = (REPO / "scripts" / "filter-parity-differential.py").read_text()
        namespace: dict = {
            "__name__": "_differential_2402",
            "__file__": str(REPO / "scripts" / "filter-parity-differential.py"),
        }
        exec(compile(source, "filter-parity-differential.py", "exec"), namespace)  # noqa: S102
        return namespace

    def test_the_required_set_is_read_out_of_djangos_own_source(self):
        ns = self._differential()
        required = ns["_required_forloop_members"]()
        assert set(required) == set(MEMBERS), required
        assert all(v == "django defaulttags.ForNode.render" for v in required.values())

    def test_the_requirement_matches_what_django_actually_writes(self):
        """The parse, checked against the reference implementation itself."""
        source = inspect_getsource(ForNode.render)
        for member in MEMBERS:
            assert member in source, member

    def test_the_corpus_sweeps_every_member(self):
        ns = self._differential()
        swept = ns["_swept_forloop_members"]()
        missing = set(MEMBERS) - swept
        assert not missing, f"the corpus builds no cell referencing {sorted(missing)}"

    def test_the_manifest_reports_the_axis_with_no_missing_member(self):
        ns = self._differential()
        rows = {row["axis"]: row for row in ns["manifest"]()["axes"]}
        assert "loop-variable" in rows, sorted(rows)
        assert rows["loop-variable"]["missing"] == [], rows["loop-variable"]

    def test_the_axis_goes_RED_when_a_member_leaves_the_corpus(self):
        """The load-bearing half (#1859): a pin that cannot go red is decor."""
        ns = self._differential()
        shapes = ns["PATH_SHAPES"]
        removed = {k: v for k, v in shapes.items() if "forloop.revcounter0" in v}
        assert removed, "no cell references revcounter0 — the sweep below is vacuous"
        for key in removed:
            del shapes[key]
        try:
            rows = {row["axis"]: row for row in ns["manifest"]()["axes"]}
            assert rows["loop-variable"]["missing"] == ["revcounter0"], rows["loop-variable"]
        finally:
            shapes.update(removed)


def inspect_getsource(func):
    import inspect as _inspect

    return _inspect.getsource(func)
