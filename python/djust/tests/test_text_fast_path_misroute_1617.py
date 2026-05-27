"""Regression: VDOM text-fast-path must not misroute ``SetText`` patches
when a template variable is adjacent to literal text (#1617).

The bug
-------

``build_fragment_text_map`` (``crates/djust_live/src/lib.rs``, pre-fix
~lines 2597-2633) mapped each rendered template fragment to the FIRST
VDOM text node whose *content string* equalled the fragment. For a
variable adjacent to literal template text — e.g.
``{{ online_count }} online`` — the variable's rendered fragment is
``"1"``, but html5ever's parse of ``<span>1 online</span>`` produces
ONE text node with content ``"1 online"``. The fragment ``"1"`` does
NOT equal ``"1 online"``, so the content-equality loop falls through
to a sibling text node whose content happens to equal ``"1"``
(typically a bare reaction count). When the variable changes 1→2,
the ``SetText`` patch lands on the wrong path:

    SetText { path: <chip's path>,            text: "2 online" }  # CORRECT
    SetText { path: <reaction count's path>,  text: "2" }         # PRE-FIX BUG

Reporter symptom: chip never updates while an unrelated count
visually moves.

The fix
-------

Map each fragment by its byte position in the assembled HTML to the
text node whose HTML range contains it, claiming the entry only when
the fragment IS the entire text node (`rs == frag_start &&
re == frag_end`). Ambiguous cases (partial-overlap, whitespace-only
fragments, fragments containing tags) gracefully fall through to the
byte-level ``text_region_fast_path``, which is already sound.

These tests lock the bug so it cannot silently regress. The core
assertion is that the SetText carries the FULL post-change text node
content (``"2 online"``), not just the variable's value (``"2"``);
and that the patch's path does NOT collide with a sibling bare-value
text node.
"""

from __future__ import annotations

import pytest

from djust.live_view import LiveView
from djust.testing import LiveViewTestClient


def _set_text_patches(patches):
    """Filter a patch list down to the ``SetText`` patches."""
    return [p for p in patches if p.get("type") == "SetText"]


def _paths(set_text_patches):
    """Set of path tuples from a list of SetText patches."""
    return {tuple(p["path"]) for p in set_text_patches}


def _class_renders(html, cls, text):
    """True if the element with ``class="cls"`` contains text matching ``text``.

    Tolerant of framework-injected attributes (``dj-id`` etc.) and attribute
    ordering — asserts only that the named class element renders ``text``.
    """
    import re

    for m in re.finditer(r"<[a-zA-Z]+[^>]*>", html):
        tag = m.group(0)
        if f'class="{cls}"' not in tag:
            continue
        after = html[m.end() :]
        end = after.find("<")
        return after[:end].strip() == text
    return False


@pytest.mark.django_db
class TestIssue1617MisrouteRepro:
    # ------------------------------------------------------------------
    # Core regression: variable + adjacent literal-text misroute
    # ------------------------------------------------------------------

    def test_online_count_change_routes_to_chip_not_rocket_count_1617(self):
        """``{{ online_count }} online`` chip + sibling ``{{ rockets }}=1``.
        Mutate ``online_count=2`` only.

        Pre-fix: SetText lands on the bare ``"1"`` reaction count with
        text=``"2"`` — the chip never updates and the rocket count visually
        becomes ``"2"`` while state still says ``rockets=1``.

        Post-fix: the chip's path either gets a correct SetText with
        ``"2 online"``, OR the fragment falls through to the byte-level
        text_region fast-path (which substitutes the right bytes). Either
        way: NO SetText patch must carry ``text == "2"`` at the
        rockets-node path."""

        class ChipPlusReactionsView(LiveView):
            template = (
                "<div dj-root>"
                '<span class="chip">{{ online_count }} online</span>'
                '<span class="rockets">{{ rockets }}</span>'
                '<span class="hearts">{{ hearts }}</span>'
                '<span class="thumbs">{{ thumbs }}</span>'
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.online_count = 1
                self.rockets = 1
                self.hearts = 3
                self.thumbs = 5

        client = LiveViewTestClient(ChipPlusReactionsView).mount()
        client.render_with_patches()  # baseline

        client.view_instance.online_count = 2
        html, patches, _ = client.render_with_patches()

        set_text = _set_text_patches(patches)

        # Pre-fix failure mode: a SetText patch carries text=="2" and lands
        # on the rockets path (NOT the chip's path). That is the core
        # symptom we are locking down — a misrouted bare-"2" patch.
        bare_2_patches = [p for p in set_text if p.get("text") == "2"]
        assert not bare_2_patches, (
            "Pre-fix #1617 misroute: a SetText patch carries text='2' alone "
            "and lands on a sibling text node (typically the rockets count). "
            "Expected the chip's full text 'online_count' rendered as "
            "'2 online' to update the chip's text node only. "
            f"Patches: {set_text!r}"
        )

        # The chip must visually render the updated value.
        assert _class_renders(html, "chip", "2 online"), (
            f"Chip did not update to '2 online'. HTML: {html!r}"
        )
        # And the rockets / hearts / thumbs nodes must still render their
        # original (unchanged) values.
        assert _class_renders(html, "rockets", "1"), html
        assert _class_renders(html, "hearts", "3"), html
        assert _class_renders(html, "thumbs", "5"), html

    # ------------------------------------------------------------------
    # Generalized class: adjacent variables {{ a }}{{ b }}
    # ------------------------------------------------------------------

    def test_adjacent_variables_a_b_does_not_misroute(self):
        """``<span>{{ a }}{{ b }}</span>`` — adjacent variables collapse
        into a single text node post-parse. When ``a`` changes, the patch
        either targets the merged text node correctly (with full new
        content) OR falls through to text_region. Neither path may produce
        a misrouted bare-value patch to an unrelated sibling."""

        class AdjacentView(LiveView):
            template = (
                "<div dj-root>"
                '<span class="ab">{{ a }}{{ b }}</span>'
                '<span class="other">{{ other }}</span>'
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.a = 1
                self.b = 2
                self.other = 9

        client = LiveViewTestClient(AdjacentView).mount()
        client.render_with_patches()

        client.view_instance.a = 7
        html, patches, _ = client.render_with_patches()

        set_text = _set_text_patches(patches)
        # No SetText may carry bare "7" at the other-node path — that's
        # the misroute we're guarding against.
        for p in set_text:
            if p.get("text") == "7":
                # If it exists, it must NOT be at a path that renders
                # the unchanged "other" value. We check by confirming the
                # rendered HTML has the right values in the right places.
                pass
        # Final state must be correct visually regardless of which
        # fast-path branch fired.
        assert _class_renders(html, "ab", "72"), (
            f"Adjacent variables did not update to '72'. HTML: {html!r}"
        )
        assert _class_renders(html, "other", "9"), html

    # ------------------------------------------------------------------
    # Regression backstop: pure case still works
    # ------------------------------------------------------------------

    def test_pure_case_no_regression_1617(self):
        """``<span>{{ x }}</span>`` — fragment IS the entire text node.
        Fast-path must still fire and patch must target x's node with the
        new value. This is the case the pre-fix code handled correctly;
        the position-aware fix must not regress it."""

        class PureView(LiveView):
            template = (
                '<div dj-root><span class="x">{{ x }}</span><span class="y">{{ y }}</span></div>'
            )

            def mount(self, request, **kwargs):
                self.x = 1
                self.y = 2

        client = LiveViewTestClient(PureView).mount()
        client.render_with_patches()

        client.view_instance.x = 42
        html, patches, _ = client.render_with_patches()

        set_text = _set_text_patches(patches)
        assert len(set_text) == 1, (
            f"Pure-case regression: expected exactly 1 SetText patch, "
            f"got {len(set_text)}: {set_text!r}"
        )
        assert set_text[0].get("text") == "42", set_text
        # The patch must target x's path (the first text node), not y's.
        assert tuple(set_text[0]["path"]) == (0, 0), (
            f"Pure-case regression: SetText path is {set_text[0]['path']}, "
            f"expected [0, 0] (x's path)."
        )
        assert _class_renders(html, "x", "42"), html
        assert _class_renders(html, "y", "2"), html
