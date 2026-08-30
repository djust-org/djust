"""A template BINDING must not carry a stale safety grant (1.1.2).

The defect
----------
djust's context safety channel is keyed BY NAME: ``Context::safe_keys`` holds
dotted paths written by ``rust_bridge._collect_safe_keys``, and
``Context::is_safe`` answers by looking a NAME up in it. Every construct that
binds a resolved value to a name — ``{% with %}``, ``{% include … with %}``,
the ``{% for %}`` loop variable, and an assign tag's ``{% … as x %}`` merge —
copied the VALUE and left the GRANT attached. Rebinding a name the view had
marked safe therefore emitted the NEW, attacker-controlled value RAW::

    safe_keys = ["p"],  p = mark_safe("<b>trusted</b>")
    {% with p=hostile %}{{ p }}{% endwith %}   ->  hostile emitted LIVE

That is an UNDER-escape — djust MORE permissive than Django, the one direction
this machinery must never move in.

The cure is a rule about the OPERATION, not the values: **a bind REPLACES the
grant**. ``Context::bind`` revokes ``name`` and every ``name.…`` beneath it
before attaching the new value. Stating it as "a bind also CARRIES a grant"
would have fixed only the over-escape half that ``main``'s #2361/#2363 reported
and left this leak open; both directions are the same rule.

Both directions are asserted here
---------------------------------
* ``TestABindRevokesAStaleGrant`` — the leak. Each case emitted the payload
  LIVE on unmodified 1.1.1.
* ``TestALegitimateGrantStillReachesItsValue`` — the inverse. The fix must not
  start silently dropping grants a value genuinely carries; the ``{% for %}``
  per-item channel (``set_loop_mapping``) and a plain ``{% include %}``'s
  inherited context are the two that exist on this branch, and both stay live.

The control pair
----------------
``render_template_with_dirs``'s fourth argument IS the safe-key channel, and a
probe that never engages it proves nothing. Every assertion here therefore runs
the SAME template twice — once with ``safe_keys=None`` and once with the keys
the PRODUCTION collector emits — and pins both outputs. Without that pairing a
green cell can mean "the channel was never engaged", which is how a whole test
file in this repo turned out to be measuring nothing.

Known divergences from Django, unchanged by this fix
----------------------------------------------------
* ``{% with q=p %}`` over a marked ``p`` binds a NEW name and stays ESCAPED
  where Django renders it live. This arm resolves its operand with a bare
  ``Context::get``, which carries no runtime-safe bool to attach, so the
  honest replacement grant is "none". Over-escaping, and ``main`` behaves the
  same way. Pinned by ``test_a_new_name_binding_carries_no_grant_either_way``.
* ``{% with q=p|filter %}`` does not resolve the filter at all on 1.1.x (it
  binds the literal token) — a separate pre-existing gap, not an escaping
  defect. Pinned by ``test_a_filtered_with_operand_is_unresolved_not_leaked``.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402

MARKED = mark_safe("<b>ok</b>")
HOSTILE = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"

# An `<img …>` OPENING TAG carrying an onerror handler, tolerant of attribute
# order. This is the shape a browser actually executes; a bare substring scan
# reports a genuine XSS as inert once anything reorders attributes.
_LIVE_ELEMENT = re.compile(r"<\s*img\b[^>]*\bonerror\s*=", re.I)


def _safe_keys(ctx: dict) -> list[str]:
    """The keys the PRODUCTION collector emits for this context."""
    keys: list[str] = []
    for key, value in ctx.items():
        keys.extend(_collect_safe_keys(value, key))
    return keys


def render_pair(tpl: str, ctx: dict, dirs: list[str] | None = None) -> tuple[str, str]:
    """Render `tpl` with the safe-key channel OFF and then ON.

    The pair is the control: a difference proves the channel was engaged, and
    an assertion about the ON output means nothing without knowing the OFF one.
    """
    dirs = dirs or []
    off = _rust.render_template_with_dirs(tpl, ctx, dirs, None)
    on = _rust.render_template_with_dirs(tpl, ctx, dirs, _safe_keys(ctx) or None)
    return off, on


def django_render(tpl: str, ctx: dict, dirs: list[str] | None = None) -> str:
    if dirs:
        return DjangoTemplate(tpl, engine=Engine(dirs=dirs, libraries={})).render(
            DjangoContext(ctx)
        )
    return DjangoTemplate(tpl).render(DjangoContext(ctx))


def assert_inert_both_ways(tpl: str, ctx: dict, dirs: list[str] | None = None) -> None:
    """The payload is escaped whether or not the safe-key channel is engaged.

    Also pins that the safe keys really do name the shadowed variable — a probe
    whose ``safe_keys`` came out empty would pass this vacuously.
    """
    assert _safe_keys(ctx), f"the probe engaged NO safe keys, so it proves nothing: {ctx!r}"
    off, on = render_pair(tpl, ctx, dirs)
    assert not _LIVE_ELEMENT.search(off), f"{tpl!r} was live even with the channel OFF: {off!r}"
    assert not _LIVE_ELEMENT.search(on), f"{tpl!r} leaked a stale grant: {on!r}"
    assert ESCAPED in on, f"{tpl!r} never emitted the payload at all: {on!r}"
    assert on == django_render(tpl, ctx, dirs), (
        f"{tpl!r}\n  django {django_render(tpl, ctx, dirs)!r}\n  djust  {on!r}"
    )


class TestABindRevokesAStaleGrant:
    """The leak. Each of these emitted the payload LIVE on unmodified 1.1.1."""

    def test_with_rebinding_a_marked_name_drops_the_stale_grant(self):
        assert_inert_both_ways(
            "{% with p=hostile %}{{ p }}{% endwith %}",
            {"p": MARKED, "hostile": HOSTILE},
        )

    def test_with_rebinding_drops_the_grants_BENEATH_the_name_too(self):
        """``p.a`` belonged to the SHADOWED ``p``; it must not survive the bind."""
        assert_inert_both_ways(
            "{% with p=hostile %}{{ p.a }}{% endwith %}",
            {"p": {"a": MARKED}, "hostile": {"a": HOSTILE}},
        )

    def test_with_binding_a_new_name_that_the_context_marked(self):
        """The bound name need not be the operand's — any marked name shadows."""
        assert_inert_both_ways(
            "{% with q=hostile %}{{ q }}{% endwith %}",
            {"q": MARKED, "hostile": HOSTILE},
        )

    def test_for_rebinding_a_marked_name_drops_the_stale_grant(self):
        assert_inert_both_ways(
            "{% for p in hs %}{{ p }}{% endfor %}",
            {"p": MARKED, "hs": [HOSTILE]},
        )

    def test_for_rebinding_drops_the_grants_beneath_the_name_too(self):
        assert_inert_both_ways(
            "{% for p in hs %}{{ p.a }}{% endfor %}",
            {"p": {"a": MARKED}, "hs": [{"a": HOSTILE}]},
        )

    def test_for_tuple_unpacking_revokes_each_bound_name_independently(self):
        """Both halves of ``{% for k, v in rows %}`` are binds, so both revoke."""
        assert_inert_both_ways(
            "{% for k, v in rows %}{{ k }}|{{ v }}{% endfor %}",
            {"k": MARKED, "v": MARKED, "rows": [[HOSTILE, HOSTILE]]},
        )

    def test_include_with_rebinding_a_marked_name_drops_the_stale_grant(self, dirs):
        assert_inert_both_ways(
            '{% include "child.html" with q=hostile %}',
            {"q": MARKED, "hostile": HOSTILE},
            dirs,
        )

    def test_include_with_only_never_carried_a_grant_and_still_does_not(self, dirs):
        """``only`` builds a FRESH context, so the revoke is a no-op here.

        Kept as its own case so a future change that starts seeding the fresh
        context from the parent cannot reintroduce the leak unnoticed.
        """
        assert_inert_both_ways(
            '{% include "child.html" with q=hostile only %}',
            {"q": MARKED, "hostile": HOSTILE},
            dirs,
        )

    def test_an_assign_tag_binding_a_marked_name_drops_the_stale_grant(self, leak_probe_tag):
        """``{% … as x %}`` is the fourth bind sink.

        An assign handler returns plain ``Value``s across the PyO3 boundary
        with no safety channel at all, so a handler's output landing on a
        marked name was emitted RAW.

        No Django comparison: the tag is djust-registry-only, so there is no
        live-Django rendering of it to agree with. The control pair still
        applies, and the escaped-payload assertion keeps the case from passing
        by the payload never reaching the output.
        """
        ctx = {"p": MARKED}
        assert _safe_keys(ctx) == ["p"]
        off, on = render_pair("{% leakprobe112 as p %}{{ p }}", ctx)
        assert not _LIVE_ELEMENT.search(off), f"live with the channel OFF: {off!r}"
        assert not _LIVE_ELEMENT.search(on), f"the assign merge leaked a stale grant: {on!r}"
        assert off == on == ESCAPED

    def test_the_outer_grant_is_intact_after_the_block(self):
        """The revoke is scoped to the bind's own context, not the parent's."""
        off, on = render_pair("{% with p=h %}{% endwith %}{{ p }}", {"p": MARKED, "h": HOSTILE})
        assert off == "&lt;b&gt;ok&lt;/b&gt;"
        assert on == "<b>ok</b>", "the block's revoke escaped into the parent context"


class TestALegitimateGrantStillReachesItsValue:
    """The inverse direction: the fix must not start DROPPING earned grants.

    Two channels on this branch legitimately carry a grant across a bind, and
    both must survive: the ``{% for %}`` positional loop mapping, and a plain
    ``{% include %}``'s inherited parent context.
    """

    def test_a_for_loop_still_resolves_its_per_item_mark(self):
        """``set_loop_mapping`` resolves ``x`` -> ``p.<index>``; untouched."""
        off, on = render_pair("{% for x in p %}{{ x }}{% endfor %}", {"p": [MARKED]})
        assert off == "&lt;b&gt;ok&lt;/b&gt;"
        assert on == "<b>ok</b>", "the loop variable lost its genuine per-item mark"
        assert on == django_render("{% for x in p %}{{ x }}{% endfor %}", {"p": [MARKED]})

    def test_a_for_loop_marks_only_the_marked_item(self):
        ctx = {"p": [MARKED, HOSTILE]}
        off, on = render_pair("{% for x in p %}[{{ x }}]{% endfor %}", ctx)
        assert off == "[&lt;b&gt;ok&lt;/b&gt;][" + ESCAPED + "]"
        assert on == "[<b>ok</b>][" + ESCAPED + "]"
        assert not _LIVE_ELEMENT.search(on)

    def test_a_nested_dotted_per_item_mark_still_resolves(self):
        ctx = {"rows": [{"body": MARKED}]}
        tpl = "{% for r in rows %}{{ r.body }}{% endfor %}"
        off, on = render_pair(tpl, ctx)
        assert off == "&lt;b&gt;ok&lt;/b&gt;"
        assert on == "<b>ok</b>", "a dotted per-item mark was revoked"

    def test_a_plain_include_still_inherits_the_parent_grant(self, dirs):
        """No ``with``, so no bind — the inherited grant must survive."""
        off, on = render_pair('{% include "child.html" %}', {"q": MARKED}, dirs)
        assert off == "[&lt;b&gt;ok&lt;/b&gt;]"
        assert on == "[<b>ok</b>]", "a plain include lost the parent's grant"

    def test_an_unrelated_name_keeps_its_grant_across_a_bind(self):
        """The revoke is scoped to the bound name's own subtree."""
        ctx = {"p": MARKED, "other": MARKED, "h": HOSTILE}
        off, on = render_pair("{% with p=h %}{{ other }}{% endwith %}", ctx)
        assert off == "&lt;b&gt;ok&lt;/b&gt;"
        assert on == "<b>ok</b>", "the bind revoked an unrelated name"

    def test_a_prefix_sharing_sibling_keeps_its_grant(self):
        """``pp`` starts with ``p`` but is not beneath it — only ``p.`` is."""
        ctx = {"p": MARKED, "pp": MARKED, "h": HOSTILE}
        off, on = render_pair("{% with p=h %}{{ pp }}{% endwith %}", ctx)
        assert off == "&lt;b&gt;ok&lt;/b&gt;"
        assert on == "<b>ok</b>", "a prefix-sharing SIBLING was revoked"


class TestTheAcceptedDivergences:
    """Shapes that do NOT reproduce the leak, pinned so a change is visible."""

    def test_a_new_name_binding_carries_no_grant_either_way(self):
        """``{% with q=p %}`` over a marked ``p``: escaped, where Django is live.

        This arm has no runtime-safe bool to attach, so the honest grant is
        none. Over-escaping — a rendering divergence, not a leak — and ``main``
        behaves identically. Unchanged by this fix.
        """
        tpl = "{% with q=p %}{{ q }}{% endwith %}"
        off, on = render_pair(tpl, {"p": MARKED})
        assert off == on == "&lt;b&gt;ok&lt;/b&gt;"
        assert django_render(tpl, {"p": MARKED}) == "<b>ok</b>"

    def test_a_filtered_with_operand_is_unresolved_not_leaked(self):
        """``{% with p=h|upper %}`` binds the literal token on 1.1.x.

        A separate pre-existing resolution gap; what matters here is that the
        marked name ``p`` does not make the bound token live.
        """
        tpl = "{% with p=h|upper %}{{ p }}{% endwith %}"
        off, on = render_pair(tpl, {"p": MARKED, "h": HOSTILE})
        assert off == on == "h|upper"


class TestEveryAssignTagMergeSiteUsesTheBind:
    """The mechanical-replacement pin (CLAUDE.md #1104 / #1125).

    Only ``render_nodes_with_loader``'s assign-merge is reachable through
    ``render_template_with_dirs``; the ``_collecting`` and ``_partial`` render
    functions carry the SAME merge loop and are exercised by the streaming /
    partial paths. A behavioural test cannot reach them from here, so the set
    is pinned structurally: a fourth site added with a bare ``set`` fails this.
    """

    RENDERER = (
        pathlib.Path(__file__).resolve().parents[3]
        / "crates"
        / "djust_templates"
        / "src"
        / "renderer.rs"
    )

    def test_no_assign_merge_site_still_uses_a_bare_set(self):
        src = self.RENDERER.read_text()
        assert "ctx.set(k, v);" not in src, (
            "an assign-tag merge site still binds with a bare `set`, which "
            "leaves a stale by-name safety grant attached"
        )

    def test_all_three_assign_merge_sites_are_present(self):
        src = self.RENDERER.read_text()
        assert src.count("ctx.bind(k, v);") == 3, (
            "expected exactly 3 assign-tag merge sites (with_loader / "
            "collecting / partial); the set changed — decide the new one "
            "explicitly rather than letting it default to `set`"
        )


@pytest.fixture
def dirs(tmp_path: pathlib.Path) -> list[str]:
    """A one-template directory for the ``{% include %}`` cases."""
    (tmp_path / "child.html").write_text("[{{ q }}]")
    return [str(tmp_path)]


@pytest.fixture
def leak_probe_tag():
    """Register an assign tag whose handler returns an attacker-controlled str.

    Registered and unregistered around the test so the process-global Rust
    assign registry is left exactly as it was found.
    """
    from djust._rust import register_assign_tag_handler, unregister_assign_tag_handler
    from djust.template_tags import AssignTagHandler

    class _LeakProbe(AssignTagHandler):
        RESOLVE_ARG_POSITIONS: set[int] = set()

        def render(self, args, context):
            return {args[-1]: HOSTILE}

    register_assign_tag_handler("leakprobe112", _LeakProbe())
    try:
        yield
    finally:
        unregister_assign_tag_handler("leakprobe112")
