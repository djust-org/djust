"""
Developer-tool template tags — JsonViewer (Terminal, MarkdownEditor, LogViewer
and FileTree were removed in 2.0, #4002).

Extracted from the monolithic djust_components.py for maintainability.
All tags register on the shared ``register`` from ``_registry``.
"""

import json as _json
from typing import Any, Dict

from django import template

# ``conditional_escape`` comes from where it is DEFINED, not through ``._registry``: the safe-markup
# guard trusts an escaper by its origin, and a re-export is a different origin (#3774, #3820).
from django.utils.html import conditional_escape


from ._registry import register, _resolve, _parse_kv_args, mark_safe


def _safe(html: str) -> str:
    """``mark_safe`` wrapper with a typed ``str`` return.

    Django's ``mark_safe`` is untyped (returns ``Any``); this thin wrapper
    keeps the strict-island return-type contract without an ``Any`` leak.
    """
    marked: str = mark_safe(html)
    return marked


# ---------------------------------------------------------------------------
# JSON Viewer
# ---------------------------------------------------------------------------


class JsonViewerNode(template.Node):
    def __init__(self, kwargs: Dict[str, Any]) -> None:
        self.kwargs = kwargs

    def _render_node(self, value: Any, depth: int, collapsed_depth: int) -> str:
        collapsed = depth >= collapsed_depth

        if isinstance(value, dict):
            if not value:
                return '<span class="dj-json__bracket">{}</span>'
            collapse_cls = " dj-json__node--collapsed" if collapsed else ""
            toggle = (
                f'<span class="dj-json__toggle" role="button" tabindex="0" '
                f'aria-expanded="{"false" if collapsed else "true"}">'
                f"{'&#9654;' if collapsed else '&#9660;'}</span>"
            )
            items = []
            for k, v in value.items():
                e_key = conditional_escape(str(k))
                items.append(
                    f'<div class="dj-json__pair">'
                    f'<span class="dj-json__key">"{e_key}"</span>'
                    f'<span class="dj-json__colon">: </span>'
                    f"{self._render_node(v, depth + 1, collapsed_depth)}</div>"
                )
            count = f' <span class="dj-json__count">({len(value)} keys)</span>' if collapsed else ""
            return (
                f'<div class="dj-json__node dj-json__node--object{collapse_cls}">'
                f"{toggle}"
                f'<span class="dj-json__bracket">{{</span>{count}'
                f'<div class="dj-json__children">{"".join(items)}</div>'
                f'<span class="dj-json__bracket">}}</span></div>'
            )

        if isinstance(value, list):
            if not value:
                return '<span class="dj-json__bracket">[]</span>'
            collapse_cls = " dj-json__node--collapsed" if collapsed else ""
            toggle = (
                f'<span class="dj-json__toggle" role="button" tabindex="0" '
                f'aria-expanded="{"false" if collapsed else "true"}">'
                f"{'&#9654;' if collapsed else '&#9660;'}</span>"
            )
            items = []
            for i, v in enumerate(value):
                items.append(
                    f'<div class="dj-json__item">'
                    f"{self._render_node(v, depth + 1, collapsed_depth)}"
                    f"{',' if i < len(value) - 1 else ''}</div>"
                )
            count = (
                f' <span class="dj-json__count">({len(value)} items)</span>' if collapsed else ""
            )
            return (
                f'<div class="dj-json__node dj-json__node--array{collapse_cls}">'
                f"{toggle}"
                f'<span class="dj-json__bracket">[</span>{count}'
                f'<div class="dj-json__children">{"".join(items)}</div>'
                f'<span class="dj-json__bracket">]</span></div>'
            )

        if isinstance(value, str):
            return f'<span class="dj-json__value dj-json__value--string">"{conditional_escape(value)}"</span>'
        if isinstance(value, bool):
            return f'<span class="dj-json__value dj-json__value--bool">{"true" if value else "false"}</span>'
        if isinstance(value, (int, float)):
            return f'<span class="dj-json__value dj-json__value--number">{value}</span>'
        if value is None:
            return '<span class="dj-json__value dj-json__value--null">null</span>'

        return f'<span class="dj-json__value">{conditional_escape(str(value))}</span>'

    def render(self, context: Any) -> str:
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        data = kw.get("data", None)
        collapsed_depth = kw.get("collapsed_depth", 2)
        root_label = kw.get("root_label", "root")
        copy_button = kw.get("copy_button", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        e_label = conditional_escape(str(root_label))

        try:
            collapsed_depth = int(collapsed_depth)
        except (ValueError, TypeError):
            collapsed_depth = 2

        classes = ["dj-json-viewer"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        copy_html = ""
        if copy_button:
            copy_html = (
                '<button class="dj-json-viewer__copy" type="button" '
                'aria-label="Copy JSON">Copy</button>'
            )

        try:
            raw_json = _json.dumps(data, indent=2, default=str)
        except (TypeError, ValueError):
            raw_json = str(data)

        tree_html = self._render_node(data, 0, collapsed_depth)

        return _safe(
            f'<div class="{class_str}" dj-hook="JsonViewer" '
            f'data-collapsed-depth="{collapsed_depth}">'
            f'<div class="dj-json-viewer__header">'
            f'<span class="dj-json-viewer__label">{e_label}</span>'
            f"{copy_html}</div>"
            f'<div class="dj-json-viewer__tree">{tree_html}</div>'
            f'<script type="application/json" class="dj-json-viewer__raw">'
            f"{conditional_escape(raw_json)}</script>"
            f"</div>"
        )


@register.tag("json_viewer")
def do_json_viewer(parser: Any, token: Any) -> template.Node:
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return JsonViewerNode(kwargs)
