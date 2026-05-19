"""Regression: VDOM incremental diff must not mis-path ``SetText`` patches
when 2+ dynamic ``{{ }}`` text values share identical baseline content (#1529).

The bug
-------

When two or more dynamic ``{{ }}`` text values change in a single update
*and those values rendered the same baseline string*, ``render_with_diff()``
emits ``SetText`` patches that all carry the FIRST matching text node's path.
The second value's patch lands on the first node; the second node never
updates.

The defect is **not** in the VDOM differ (``crates/djust_vdom/src/diff.rs``).
It is in the text-fast-path of ``djust_live``: ``build_fragment_text_map``
(``crates/djust_live/src/lib.rs``) maps each rendered fragment to the FIRST
VDOM text node whose *content string* equals the fragment. Content equality
is not a unique key — two template variables that render to the same baseline
string (e.g. ``{{ a }}`` and ``{{ b }}`` both ``0`` at mount) both match the
*first* ``"0"`` text node. Both map entries point at the same path, so::

    SetText { path: [0,0], text: "7"  }
    SetText { path: [0,0], text: "99" }   # should be path [1,0]

The fix
-------

``build_fragment_text_map`` claims each VDOM text node at most once: it tracks
a ``Vec<bool>`` parallel to the collected text nodes and, for each fragment,
picks the first *unclaimed* node whose content matches. The mapping becomes a
bijection over matched fragments instead of a many-to-one collapse.

These tests lock the bug so it can't silently regress. The core assertion is
that the two ``SetText`` patches land at DISTINCT paths.
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


def _path_for_text(set_text_patches, text):
    """Return the path of the SetText patch carrying ``text`` (or None)."""
    for p in set_text_patches:
        if p.get("text") == text:
            return tuple(p["path"])
    return None


def _class_renders(html, cls, text):
    """True if the element carrying ``class="cls"`` contains ``>text<``.

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
class TestVdomSetTextMispath1529:
    # ------------------------------------------------------------------
    # Core regression: two values, identical baseline
    # ------------------------------------------------------------------

    def test_two_identical_baseline_values_get_distinct_paths(self):
        """Two ``{{ }}`` values with identical baseline (``a=0, b=0``) that
        both change must produce two ``SetText`` patches at DISTINCT paths."""

        class TwoValueView(LiveView):
            template = '<div dj-root><div class="a">{{ a }}</div><div class="b">{{ b }}</div></div>'

            def mount(self, request, **kwargs):
                self.a = 0
                self.b = 0

        client = LiveViewTestClient(TwoValueView).mount()
        # Establish the VDOM baseline.
        client.render_with_patches()

        client.view_instance.a = 7
        client.view_instance.b = 99
        html, patches, _ = client.render_with_patches()

        set_text = _set_text_patches(patches)
        assert len(set_text) == 2, (
            f"Expected exactly 2 SetText patches, got {len(set_text)}: {set_text!r}"
        )
        # The load-bearing assertion: the two patches land at distinct paths.
        assert len(_paths(set_text)) == 2, (
            f"Both SetText patches collapsed onto the same path — #1529 "
            f"content-match collapse. Patches: {set_text!r}"
        )
        assert _path_for_text(set_text, "7") == (0, 0), set_text
        assert _path_for_text(set_text, "99") == (1, 0), set_text

        # The rendered HTML must be correct on both nodes.
        assert _class_renders(html, "a", "7"), html
        assert _class_renders(html, "b", "99"), html

    # ------------------------------------------------------------------
    # 3+ changed values, identical baselines
    # ------------------------------------------------------------------

    def test_three_identical_baseline_values_get_distinct_paths(self):
        """Three sibling ``{{ }}`` values all ``0`` at baseline, then
        ``1/2/3`` — assert 3 SetText patches at 3 distinct paths."""

        class ThreeValueView(LiveView):
            template = (
                "<div dj-root>"
                '<div class="a">{{ a }}</div>'
                '<div class="b">{{ b }}</div>'
                '<div class="c">{{ c }}</div>'
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.a = 0
                self.b = 0
                self.c = 0

        client = LiveViewTestClient(ThreeValueView).mount()
        client.render_with_patches()

        client.view_instance.a = 1
        client.view_instance.b = 2
        client.view_instance.c = 3
        html, patches, _ = client.render_with_patches()

        set_text = _set_text_patches(patches)
        assert len(set_text) == 3, f"Expected 3 SetText patches, got {len(set_text)}: {set_text!r}"
        assert _paths(set_text) == {(0, 0), (1, 0), (2, 0)}, set_text
        assert _path_for_text(set_text, "1") == (0, 0), set_text
        assert _path_for_text(set_text, "2") == (1, 0), set_text
        assert _path_for_text(set_text, "3") == (2, 0), set_text

        assert _class_renders(html, "a", "1"), html
        assert _class_renders(html, "b", "2"), html
        assert _class_renders(html, "c", "3"), html

    # ------------------------------------------------------------------
    # Nested element — deeper distinct paths
    # ------------------------------------------------------------------

    def test_nested_identical_baseline_values_get_distinct_deep_paths(self):
        """Identical-baseline values inside a nested element must still get
        distinct (deeper) paths."""

        class NestedView(LiveView):
            template = (
                "<div dj-root>"
                "<section>"
                '<span class="a">{{ a }}</span>'
                '<span class="b">{{ b }}</span>'
                "</section>"
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.a = 0
                self.b = 0

        client = LiveViewTestClient(NestedView).mount()
        client.render_with_patches()

        client.view_instance.a = 7
        client.view_instance.b = 99
        html, patches, _ = client.render_with_patches()

        set_text = _set_text_patches(patches)
        assert len(set_text) == 2, set_text
        assert len(_paths(set_text)) == 2, (
            f"Nested identical-baseline values collapsed onto one path — "
            f"#1529. Patches: {set_text!r}"
        )
        assert _path_for_text(set_text, "7") == (0, 0, 0), set_text
        assert _path_for_text(set_text, "99") == (0, 1, 0), set_text

        assert _class_renders(html, "a", "7"), html
        assert _class_renders(html, "b", "99"), html

    # ------------------------------------------------------------------
    # Text + attribute change together
    # ------------------------------------------------------------------

    def test_text_and_attr_change_together_keep_distinct_paths(self):
        """A text change on one node and an attribute change on another, in
        the same update — the fix must not interfere with the non-text
        patch path. The text node and the attr node start with identical
        baseline text so the #1529 collapse would otherwise be a risk."""

        class TextAndAttrView(LiveView):
            template = (
                "<div dj-root>"
                '<div class="txt">{{ a }}</div>'
                '<div title="{{ b }}">{{ a }}</div>'
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.a = 0
                self.b = "old"

        client = LiveViewTestClient(TextAndAttrView).mount()
        client.render_with_patches()

        client.view_instance.a = 7
        client.view_instance.b = "new"
        html, patches, _ = client.render_with_patches()

        set_text = _set_text_patches(patches)
        # Both text nodes render {{ a }}, so both change to "7".
        assert len(set_text) == 2, set_text
        assert len(_paths(set_text)) == 2, (
            f"Two {{ a }} text nodes collapsed onto one path — #1529. Patches: {set_text!r}"
        )
        assert _paths(set_text) == {(0, 0), (1, 0)}, set_text

        # The attribute change must still land correctly.
        assert 'title="new"' in html, html
        assert _class_renders(html, "txt", "7"), html

    # ------------------------------------------------------------------
    # Only the second value changed — sharpest mapping assertion
    # ------------------------------------------------------------------

    def test_only_second_value_changed_targets_second_node(self):
        """Baselines ``a=0, b=0``; change ONLY ``b``. Exactly 1 SetText
        patch, and it must carry path ``[1,0]`` — NOT ``[0,0]``. This is the
        sharpest assertion that the mapping points at the right node."""

        class SecondOnlyView(LiveView):
            template = '<div dj-root><div class="a">{{ a }}</div><div class="b">{{ b }}</div></div>'

            def mount(self, request, **kwargs):
                self.a = 0
                self.b = 0

        client = LiveViewTestClient(SecondOnlyView).mount()
        client.render_with_patches()

        client.view_instance.b = 42  # only the second value changes
        html, patches, _ = client.render_with_patches()

        set_text = _set_text_patches(patches)
        assert len(set_text) == 1, (
            f"Expected exactly 1 SetText patch, got {len(set_text)}: {set_text!r}"
        )
        assert tuple(set_text[0]["path"]) == (1, 0), (
            f"SetText for the second value landed at {set_text[0]['path']} — must be [1,0]. #1529."
        )
        assert set_text[0].get("text") == "42", set_text

        assert _class_renders(html, "a", "0"), html
        assert _class_renders(html, "b", "42"), html

    # ------------------------------------------------------------------
    # Regression guard: distinct baselines still work (passed pre-fix)
    # ------------------------------------------------------------------

    def test_distinct_baseline_values_still_get_distinct_paths(self):
        """Distinct baselines (``a=1, b=2``) — this case already worked on
        main (each fragment matched a distinct node by content). Included so
        the claim-once fix doesn't regress the happy path and to document
        the pre-fix trigger boundary."""

        class DistinctView(LiveView):
            template = '<div dj-root><div class="a">{{ a }}</div><div class="b">{{ b }}</div></div>'

            def mount(self, request, **kwargs):
                self.a = 1
                self.b = 2

        client = LiveViewTestClient(DistinctView).mount()
        client.render_with_patches()

        client.view_instance.a = 7
        client.view_instance.b = 99
        html, patches, _ = client.render_with_patches()

        set_text = _set_text_patches(patches)
        assert len(set_text) == 2, set_text
        assert _paths(set_text) == {(0, 0), (1, 0)}, set_text
        assert _path_for_text(set_text, "7") == (0, 0), set_text
        assert _path_for_text(set_text, "99") == (1, 0), set_text

        assert _class_renders(html, "a", "7"), html
        assert _class_renders(html, "b", "99"), html
