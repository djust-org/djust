"""A safety grant must travel with the value across a BINDING (#2361, #2363).

Three symptoms, one operation
-----------------------------
`{% with %}`, `{% include … with %}` and the `{% for %}` loop variable all
BIND a resolved value to a NEW NAME. djust's safety channel is keyed BY NAME
(``Context::safe_keys``, written by ``rust_bridge._collect_safe_keys``), and
before this fix a bind copied the VALUE and not the GRANT. That is one defect
with three faces, and it points in BOTH directions:

* **#2363** — a filter that produces safe output loses the grant across
  ``{% with %}``. ``{% with q=p|linebreaks %}{{ q }}{% endwith %}`` renders
  ``&lt;p&gt;a&lt;br&gt;b&lt;/p&gt;`` where Django renders live markup.
  Over-escaping: a lost capability, never a leak.
* **#2361** — a ``mark_safe`` VALUE reached through ``d.values`` / ``d.items``
  loses its mark, because the collector spells the path BY KEY NAME (``p.a``)
  while the loop's positional mapping looks for ``p.values.0``. Two spellings
  of one path that never meet. Also over-escaping.
* **the inverse, found measuring the two above** — a bind that SHADOWS a
  marked name KEPT the stale grant, so ``{% with p=hostile %}{{ p }}{% endwith %}``
  emitted the hostile value LIVE where Django escapes it. That one is an
  UNDER-escape, and it is the reason the cure is "a bind REPLACES the grant"
  rather than "a bind also carries a grant": fixing only the reported
  direction would have left this open.

Every test here goes through ``_rust.render_template_with_dirs`` — the only
Python entry point carrying ``safe_keys`` — with the keys produced by the
PRODUCTION collector, and asserts against LIVE Django rather than a
transcribed expectation.
"""

import pathlib

import pytest
from django.template import Context as DjangoContext
from django.template import Engine
from django.template import Template as DjangoTemplate
from django.utils.safestring import mark_safe

from djust import _rust
from djust.mixins.rust_bridge import _collect_safe_keys

MARKED = mark_safe("<b>ok</b>")
HOSTILE = "<img src=x onerror=alert(1)>"


def _safe_keys(ctx: dict) -> list[str]:
    keys: list[str] = []
    for key, value in ctx.items():
        keys.extend(_collect_safe_keys(value, key))
    return keys


def both(tpl: str, ctx: dict, dirs: list[str] | None = None) -> tuple[str, str]:
    """Render `tpl` on LIVE Django and on djust through the real safe-keys channel."""
    safe_keys = _safe_keys(ctx)
    if dirs:
        engine = Engine(dirs=dirs, libraries={})
        django_out = DjangoTemplate(tpl, engine=engine).render(DjangoContext(ctx))
    else:
        django_out = DjangoTemplate(tpl).render(DjangoContext(ctx))
    djust_out = _rust.render_template_with_dirs(tpl, ctx, dirs or [], safe_keys or None)
    return django_out, djust_out


def assert_agrees(tpl: str, ctx: dict, dirs: list[str] | None = None) -> str:
    django_out, djust_out = both(tpl, ctx, dirs)
    assert djust_out == django_out, (
        f"{tpl!r} over {ctx!r}\n  django {django_out!r}\n  djust  {djust_out!r}"
    )
    return djust_out


class TestTheGrantSurvivesAWithBinding:
    """#2363 — every safe-output filter, bound rather than emitted."""

    # Django's `is_safe`/markup-producing filters whose `{{ p|f }}` output is
    # live. `linenumbers` is deliberately absent: its output carries no markup,
    # so it agreed even while broken and could not distinguish a fix.
    @pytest.mark.parametrize(
        "expr",
        [
            "p|safe",
            "p|linebreaks",
            "p|linebreaksbr",
            "p|urlize",
            "p|urlizetrunc:9",
            "p|unordered_list",
            "p|escape",
            "p|force_escape",
        ],
    )
    def test_a_safe_output_filter_keeps_its_grant_across_with(self, expr):
        tpl = "{%% with q=%s %%}[{{ q }}]{%% endwith %%}" % expr
        assert_agrees(tpl, {"p": "a\nb <b>x</b> http://example.com/averylongpath"})

    def test_the_emit_twin_of_each_bind_already_agreed(self):
        """The EMIT path was never broken — that asymmetry IS the bug."""
        for expr in ("p|safe", "p|linebreaks", "p|urlize", "p|unordered_list"):
            assert_agrees("[{{ %s }}]" % expr, {"p": "a\nb <b>x</b>"})

    def test_a_context_mark_survives_a_bare_with_binding(self):
        out = assert_agrees("{% with q=p %}[{{ q }}]{% endwith %}", {"p": MARKED})
        assert out == "[<b>ok</b>]"

    def test_a_grant_does_not_outlive_the_with_block(self):
        assert_agrees("{% with q=p|safe %}{% endwith %}[{{ q }}]", {"p": "<b>x</b>"})

    def test_a_re_taint_after_the_bind_is_honoured(self):
        """`upper` is registered `is_safe=False`, so it re-taints — even a bound grant."""
        assert_agrees("{% with q=p|safe %}[{{ q|upper }}]{% endwith %}", {"p": "<b>x</b>"})

    def test_the_grant_survives_a_nested_with(self):
        assert_agrees(
            "{% with q=p|safe %}{% with r=q %}[{{ r }}]{% endwith %}{% endwith %}",
            {"p": "<b>x</b>"},
        )


class TestTheGrantReachesTheLoopVariableThroughADictView:
    """#2361 — `d.values` / `d.items`, where the collector's spelling is BY KEY."""

    def test_for_over_values_keeps_the_marked_value_live(self):
        out = assert_agrees("{% for v in p.values %}[{{ v }}]{% endfor %}", {"p": {"a": MARKED}})
        assert out == "[<b>ok</b>]"

    def test_for_over_items_keeps_the_marked_value_live(self):
        out = assert_agrees("{% for k, v in p.items %}[{{ v }}]{% endfor %}", {"p": {"a": MARKED}})
        assert out == "[<b>ok</b>]"

    def test_the_key_half_of_items_is_never_granted(self):
        """A key carries no mark — only the VALUE beside it does."""
        out = assert_agrees(
            "{% for k, v in p.items %}[{{ k }}]{% endfor %}",
            {"p": {"<b>k</b>": MARKED}},
        )
        assert out == "[&lt;b&gt;k&lt;/b&gt;]"

    def test_an_unmarked_sibling_value_is_still_escaped(self):
        out = assert_agrees(
            "{% for v in p.values %}[{{ v }}]{% endfor %}",
            {"p": {"a": MARKED, "b": HOSTILE}},
        )
        assert out == "[<b>ok</b>][&lt;img src=x onerror=alert(1)&gt;]"

    def test_the_unpacked_pair_itself_is_not_granted(self):
        """`{% for x in d.items %}` binds the 2-TUPLE; a tuple is not SafeData."""
        assert_agrees("{% for x in p.items %}[{{ x }}]{% endfor %}", {"p": {"a": MARKED}})

    def test_a_bare_dict_loop_grants_nothing(self):
        assert_agrees("{% for k in p %}[{{ k }}]{% endfor %}", {"p": {"<b>k</b>": MARKED}})

    def test_a_genuine_list_still_resolves_its_item_marks(self):
        """The #2287 case that already worked must keep working."""
        out = assert_agrees(
            "{% for x in p %}[{{ x }}]{% endfor %}", {"p": [MARKED, mark_safe("<i>y</i>")]}
        )
        assert out == "[<b>ok</b>][<i>y</i>]"

    def test_a_string_loop_grants_nothing(self):
        """Python iterates a str by CHARACTER, and no mark_safe marks a character."""
        assert_agrees("{% for c in p %}[{{ c }}]{% endfor %}", {"p": "<b>"})

    def test_tuple_unpacking_a_genuine_list_resolves_the_component_mark(self):
        """`{% for a, b in rows %}` — a SECOND channel from the dict-view one.

        Here the operand really is the sequence being iterated, so the
        collector's path for the component is positional on both sides:
        `[("x", mark_safe(…))]` under `p` is `p.0.1`. That correspondence is
        genuine — unlike a dict's, where the collector is by name and only the
        loop would be by index (#2334) — so it is the tuple-unpacking twin of
        the loop mapping the single-variable branch registers. Before this fix
        tuple unpacking registered neither, so the mark was lost.
        """
        out = assert_agrees("{% for a, b in p %}[{{ b }}]{% endfor %}", {"p": [("x", MARKED)]})
        assert out == "[<b>ok</b>]"

    def test_tuple_unpacking_leaves_an_unmarked_component_escaped(self):
        out = assert_agrees(
            "{% for a, b in p %}[{{ a }}|{{ b }}]{% endfor %}",
            {"p": [(HOSTILE, MARKED), (HOSTILE, HOSTILE)]},
        )
        assert out.count("<img") == 0

    def test_a_filtered_operand_grants_nothing_to_an_unpacked_component(self):
        """A filter breaks the positional correspondence — the #2325 argument."""
        out = _rust.render_template_with_dirs(
            "{% for a, b in p|slice:':2' %}[{{ b }}]{% endfor %}",
            {"p": [("x", HOSTILE), ("y", MARKED)]},
            [],
            _collect_safe_keys([("x", HOSTILE), ("y", MARKED)], "p"),
        )
        assert "<img" not in out, f"a filtered operand granted a shifted mark: {out!r}"

    def test_a_nested_dict_view_resolves_through_the_loop_alias(self):
        assert_agrees(
            "{% for row in rows %}{% for v in row.values %}[{{ v }}]{% endfor %}{% endfor %}",
            {"rows": [{"a": MARKED}, {"a": HOSTILE}]},
        )


class TestTheGrantCrossesAnIncludeWithBinding:
    """`{% include … with %}` is the third spelling of one binding (#2363).

    It needs a second template on disk, which is why it is its own class:
    `render_template` cannot reach it and `render_template_with_dirs` is the
    only entry point that takes BOTH `template_dirs` and `safe_keys`.
    """

    @pytest.fixture
    def dirs(self, tmp_path: pathlib.Path) -> list[str]:
        (tmp_path / "child.html").write_text("[{{ q }}]")
        return [str(tmp_path)]

    def test_a_filter_grant_crosses_the_include_binding(self, dirs):
        assert_agrees(
            '{% include "child.html" with q=p|linebreaks %}',
            {"p": "a\nb"},
            dirs,
        )

    def test_a_context_grant_crosses_the_include_binding(self, dirs):
        out = assert_agrees('{% include "child.html" with q=p %}', {"p": MARKED}, dirs)
        assert out == "[<b>ok</b>]"

    def test_an_unmarked_value_still_crosses_escaped(self, dirs):
        out = assert_agrees('{% include "child.html" with q=p %}', {"p": HOSTILE}, dirs)
        assert "<img" not in out

    def test_only_does_not_smuggle_a_grant_the_bound_value_lacks(self, dirs):
        """With `only`, the fresh context carries no grants; the bind supplies all of them."""
        out = assert_agrees('{% include "child.html" with q=p only %}', {"p": HOSTILE}, dirs)
        assert "<img" not in out


class TestTheHostileKeyGateStillHolds:
    """#2334 / #2341 — the collision that makes a dict's mapping a LIVE XSS.

    ``_collect_safe_keys`` writes a dict's paths BY KEY NAME. Any mechanism
    that maps a loop item BY POSITION can therefore resolve the mark of an
    ENTIRELY DIFFERENT, attacker-named key. The provenance lookup added for
    #2361 is by KEY NAME on both sides and must never reintroduce it.
    """

    @pytest.mark.parametrize("index_key", ["0", "1", "2"])
    def test_a_key_spelled_like_an_index_does_not_grant_a_positional_sibling(self, index_key):
        ctx = {"p": {index_key: MARKED, "zz": HOSTILE}}
        for tpl in (
            "{% for v in p.values %}[{{ v }}]{% endfor %}",
            "{% for k, v in p.items %}[{{ v }}]{% endfor %}",
            "{% for k in p %}[{{ k }}]{% endfor %}",
        ):
            out = assert_agrees(tpl, ctx)
            assert "<img" not in out, f"{tpl} leaked the hostile value: {out!r}"

    def test_an_int_key_does_not_grant_the_string_key_beside_it(self):
        out = assert_agrees(
            "{% for v in p.values %}[{{ v }}]{% endfor %}", {"p": {0: MARKED, "zz": HOSTILE}}
        )
        assert "<img" not in out

    def test_a_key_containing_a_dot_is_refused_rather_than_conflated(self):
        """``p.a.b`` is BOTH ``{"a.b": …}`` and ``{"a": {"b": …}}`` — refuse it.

        The collector's ``f"{prefix}.{key}"`` cannot distinguish the two, so a
        dotted key must never resolve a grant. Refusing escapes, which is the
        direction to fail in.
        """
        ctx = {"p": {"a": {"b": MARKED}, "a.b": HOSTILE}}
        out = _rust.render_template_with_dirs(
            "{% for v in p.values %}[{{ v }}]{% endfor %}",
            ctx,
            [],
            _collect_safe_keys(ctx["p"], "p"),
        )
        assert "<img" not in out, f"dotted-key collision leaked: {out!r}"


class TestABindReplacesTheGrantRatherThanAddingToIt:
    """The inverse direction: a bind that SHADOWS a marked name must REVOKE it.

    Each of these emitted the hostile payload LIVE before the fix — djust was
    MORE PERMISSIVE than Django, the one direction this machinery must never
    move in.
    """

    def test_with_rebinding_a_marked_name_drops_the_stale_grant(self):
        out = assert_agrees("{% with p=h %}[{{ p }}]{% endwith %}", {"p": MARKED, "h": HOSTILE})
        assert "<img" not in out

    def test_for_rebinding_a_marked_name_drops_the_stale_grant(self):
        out = assert_agrees(
            "{% for p in hs %}[{{ p }}]{% endfor %}", {"p": MARKED, "hs": [HOSTILE]}
        )
        assert "<img" not in out

    def test_a_rebind_drops_the_grants_BENEATH_the_name_too(self):
        """``p.a`` belonged to the SHADOWED ``p``; it must not survive the bind."""
        out = assert_agrees(
            "{% with p=h %}[{{ p.a }}]{% endwith %}",
            {"p": {"a": MARKED}, "h": {"a": HOSTILE}},
        )
        assert "<img" not in out

    def test_a_for_rebind_drops_the_grants_beneath_the_name_too(self):
        out = assert_agrees(
            "{% for p in hs %}[{{ p.a }}]{% endfor %}",
            {"p": {"a": MARKED}, "hs": [{"a": HOSTILE}]},
        )
        assert "<img" not in out

    def test_the_outer_grant_is_intact_after_the_block(self):
        """The revoke is scoped to the bind's own context, not the parent's."""
        out = assert_agrees("{% with p=h %}{% endwith %}[{{ p }}]", {"p": MARKED, "h": HOSTILE})
        assert out == "[<b>ok</b>]"

    def test_a_later_loop_iteration_does_not_inherit_the_previous_grant(self):
        out = assert_agrees(
            "{% for v in p.values %}[{{ v }}]{% endfor %}",
            {"p": {"a": MARKED, "b": HOSTILE, "c": HOSTILE}},
        )
        assert out.count("<img") == 0

    def test_tuple_unpacking_revokes_each_name_independently(self):
        out = assert_agrees(
            "{% for k, v in p.items %}[{{ k }}|{{ v }}]{% endfor %}",
            {"k": MARKED, "v": MARKED, "p": {"<b>x</b>": HOSTILE}},
        )
        assert "<img" not in out
