"""SortableList, SortableGrid, JsonViewer and LogViewer ship their interactions (#2985).

The four components rendered a ``dj-hook`` that no shipped script answered.
Each now has a script under ``djust_components/`` that registers the hook. What
the server renders has to stay what that script (and any hook an app already
registered) reads, on every one of the three render paths: the component
class, the Django template tag, and the Rust-engine handler.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.template import Context, Template

from djust.components import rust_handlers as rh
from djust.components.components.json_viewer import JsonViewer
from djust.components.components.log_viewer import LogViewer
from djust.components.components.sortable_grid import SortableGrid
from djust.components.components.sortable_list import SortableList

STATIC = Path(rh.__file__).resolve().parent / "static" / "djust_components"

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
        "handler": str(
            rh.SortableListHandler().render(
                ["items=items", "move_event='reorder'", "class='mine'"], {"items": ITEMS}
            )
        ),
    }


def _sortable_grid_paths(ITEMS=ITEMS) -> dict[str, str]:
    return {
        "class": SortableGrid(items=ITEMS, columns=4, custom_class="mine").render(),
        "tag": _tag('{% sortable_grid items=items columns=4 class="mine" %}', items=ITEMS),
        "handler": str(
            rh.SortableGridHandler().render(
                ["items=items", "columns=4", "class='mine'"], {"items": ITEMS}
            )
        ),
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
        assert paths["class"] == paths["tag"] == paths["handler"]


def test_a_disabled_list_keeps_its_markers():
    html = SortableList(items=ITEMS, disabled=True).render()
    assert 'data-disabled="true"' in html
    assert "draggable" not in html


class TestLogViewerHookAttributes:
    LINES = ["12:00 INFO up", "12:01 ERROR down", "no level"]

    def _paths(self, **kw) -> dict[str, str]:
        cls_kw = {k: v for k, v in kw.items()}
        tag_args = " ".join(
            f"{k}={json.dumps(v) if isinstance(v, str) else v}" for k, v in kw.items()
        )
        handler_args = [f"{k}={v!r}" for k, v in kw.items()]
        return {
            "class": LogViewer(lines=self.LINES, **cls_kw).render(),
            "tag": _tag("{% log_viewer lines=lines " + tag_args + " %}", lines=self.LINES),
            "handler": str(
                rh.LogViewerHandler().render(["lines=lines", *handler_args], {"lines": self.LINES})
            ),
        }

    def test_the_hook_is_told_how_to_render_a_streamed_line(self):
        for path, html in self._paths(max_lines=50, filter_level="ERROR").items():
            assert 'data-line-numbers="true"' in html, path
            assert 'data-max-lines="50"' in html, path
            assert 'data-filter-level="error"' in html, path

    def test_no_limits_no_attributes(self):
        for path, html in self._paths(show_line_numbers=False).items():
            assert "data-line-numbers" not in html, path
            assert "data-max-lines" not in html, path
            assert "data-filter-level" not in html, path

    def test_markup_an_app_hook_reads_is_unchanged(self):
        for path, html in self._paths(stream_event="new_logs").items():
            assert 'dj-hook="LogViewer"' in html, path
            assert 'data-stream-event="new_logs"' in html, path
            assert 'data-auto-scroll="true"' in html, path
            assert 'role="log" aria-live="polite"' in html, path
            assert "dj-log-viewer__body" in html, path

    def test_the_three_render_paths_agree(self):
        paths = self._paths(stream_event="new_logs", max_lines=50, filter_level="error")
        assert paths["class"] == paths["tag"] == paths["handler"]


class TestJsonViewerMarkupTheHookReads:
    DATA = {"name": "djust", "nested": {"deep": [1, 2, {"x": 'q"uote'}]}}

    def _paths(self) -> dict[str, str]:
        return {
            "class": JsonViewer(data=self.DATA, collapsed_depth=1).render(),
            "tag": _tag("{% json_viewer data=data collapsed_depth=1 %}", data=self.DATA),
            "handler": str(
                rh.JsonViewerHandler().render(
                    ["data=data", "collapsed_depth=1"], {"data": self.DATA}
                )
            ),
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
        assert paths["class"] == paths["tag"] == paths["handler"]


@pytest.mark.parametrize(
    "component, hook, script",
    [
        ("sortable_list", "SortableList", "sortable-list.js"),
        ("sortable_grid", "SortableGrid", "sortable-grid.js"),
        ("json_viewer", "JsonViewer", "json-viewer.js"),
        ("log_viewer", "LogViewer", "log-viewer.js"),
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

    def test_the_script_registers_without_replacing_an_app_hook(self, component, hook, script):
        """It registers in window.DjustHooks, which window.djust.hooks overrides,
        and only when neither registry already holds the hook."""
        source = (STATIC / script).read_text()
        assert f"window.DjustHooks.{hook} = " in source
        assert f"!appHooks.{hook} && !window.DjustHooks.{hook}" in source
