"""SortableList, SortableGrid and JsonViewer ship their interactions (#2985).

The three components rendered a ``dj-hook`` that no shipped script answered.
Each now has a script under ``djust_components/`` that registers the hook. What
the server renders has to stay what that script (and any hook an app already
registered) reads, on every one of the three render paths: the component
class, the Django template tag, and the Rust-engine handler.
"""

from __future__ import annotations

import re

import pytest
from django.template import Context, Template

from djust.components.components.json_viewer import JsonViewer
from djust.components.components.sortable_grid import SortableGrid
from djust.components.components.sortable_list import SortableList

ITEMS = [
    {"id": "a", "label": "Alpha"},
    {"id": "b", "label": "Beta"},
    {"id": "c", "label": "Gamma"},
]
# The VDOM diff cannot key these: it falls back to positional diffing and warns
# (DJE-050 for keyed + unkeyed siblings, DJE-051 for a repeated key).
UNKEYABLE = {
    "empty id": [{"id": "a", "label": "A"}, {"id": "", "label": "No id"}],
    "missing id": [{"id": "a", "label": "A"}, {"label": "No id"}],
    "duplicate id": [{"id": "a", "label": "A"}, {"id": "a", "label": "Again"}],
}


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _sortable_list_paths(ITEMS=ITEMS) -> dict[str, str]:
    return {
        "class": SortableList(items=ITEMS, move_event="reorder", custom_class="mine").render(),
        "tag": _tag(
            '{% sortable_list items=items move_event="reorder" class="mine" %}', items=ITEMS
        ),
    }


def _sortable_grid_paths(ITEMS=ITEMS) -> dict[str, str]:
    return {
        "class": SortableGrid(items=ITEMS, columns=4, custom_class="mine").render(),
        "tag": _tag('{% sortable_grid items=items columns=4 class="mine" %}', items=ITEMS),
    }


@pytest.mark.parametrize("make", [_sortable_list_paths, _sortable_grid_paths])
class TestSortableMarkup:
    def test_items_carry_a_diff_key_matching_their_id(self, make):
        """The client moves the dragged node itself; a keyed diff moves the same
        node when the server re-renders, instead of rewriting text in place."""
        for path, html in make().items():
            assert 'data-id="a" data-key="a"' in html, path
            assert 'data-id="b" data-key="b"' in html, path

    @pytest.mark.parametrize("case", sorted(UNKEYABLE))
    def test_items_that_cannot_all_be_keyed_get_no_key_at_all(self, make, case):
        """Keyed and unkeyed siblings, or a repeated key, make the differ warn on
        every render; so no item is keyed unless all can be."""
        for path, html in make(UNKEYABLE[case]).items():
            assert "data-key" not in html, (path, case)
            assert "data-id=" in html, (path, case)

    def test_markup_an_app_hook_reads_is_unchanged(self, make):
        """An app's own hook of this name reads these attributes; they stay."""
        for path, html in make().items():
            assert "mine" in html, path
            assert 'data-move-event="reorder"' in html, path
            assert 'draggable="true"' in html, path
            assert re.search(r'dj-hook="Sortable(List|Grid)"', html), path

    def test_the_three_render_paths_agree(self, make):
        paths = make()
        assert paths["class"] == paths["tag"]


def test_a_disabled_list_keeps_its_markers():
    html = SortableList(items=ITEMS, disabled=True).render()
    assert 'data-disabled="true"' in html
    assert "draggable" not in html


class TestJsonViewerMarkupTheHookReads:
    DATA = {"name": "djust", "nested": {"deep": [1, 2, {"x": 'q"uote'}]}}

    def _paths(self) -> dict[str, str]:
        return {
            "class": JsonViewer(data=self.DATA, collapsed_depth=1).render(),
            "tag": _tag("{% json_viewer data=data collapsed_depth=1 %}", data=self.DATA),
            # ``json_viewer`` has no native handler class since #3756 (it dispatches on a
            # value's type, so the Python node renders it through the bridge); the third
            # path is the Rust engine's render of the tag.
        }

    def test_the_toggle_copy_and_raw_hooks_are_there(self):
        for path, html in self._paths().items():
            assert 'class="dj-json__toggle" role="button" tabindex="0"' in html, path
            assert 'aria-expanded="false"' in html, path
            assert "dj-json-viewer__copy" in html, path
            assert 'class="dj-json-viewer__raw"' in html, path
            assert 'dj-hook="JsonViewer"' in html, path

    def test_the_raw_json_is_escaped_inside_its_script_block(self):
        """Browsers do not decode entities inside <script>, so the hook decodes
        before it copies; this pins the escaping that makes that necessary."""
        for path, html in self._paths().items():
            assert "q&quot;uote" in html, path

    def test_the_three_render_paths_agree(self):
        paths = self._paths()
        assert paths["class"] == paths["tag"]


@pytest.mark.parametrize(
    "component, hook, script",
    [
        ("sortable_list", "SortableList", "sortable-list.js"),
        ("sortable_grid", "SortableGrid", "sortable-grid.js"),
        ("json_viewer", "JsonViewer", "json-viewer.js"),
    ],
)
class TestScriptsAreShippedAndFound:
    def test_the_catalogue_reports_the_script_and_the_hook(self, component, hook, script):
        from djust.theming.gallery.component_registry import component_client

        assert component_client(component) == {
            "hook": hook,
            "script": f"djust_components/{script}",
            "hook_shipped": True,
        }
