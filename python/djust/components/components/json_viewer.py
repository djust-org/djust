"""JSON Viewer component — interactive collapsible JSON tree."""

import html
import json

from djust import Component


class JsonViewer(Component):
    """Interactive collapsible JSON tree viewer.

    Renders JSON data as a navigable, collapsible tree with syntax-colored
    values. Uses ``dj-hook="JsonViewer"`` for client-side expand/collapse.

    Usage in a LiveView::

        self.viewer = JsonViewer(data={"name": "djust", "version": 2})

    In template::

        {{ viewer|safe }}

    CSS Custom Properties::

        --dj-json-bg: background color
        --dj-json-fg: text color
        --dj-json-key-color: key color
        --dj-json-string-color: string value color
        --dj-json-number-color: number value color
        --dj-json-bool-color: boolean/null value color
        --dj-json-bracket-color: bracket/brace color
        --dj-json-radius: border radius
        --dj-json-font-size: font size

    Args:
        data: JSON-serializable data (dict, list, string, number, etc.)
        collapsed_depth: depth at which nodes start collapsed (default 2)
        root_label: label for root node
        copy_button: show copy-to-clipboard button (default True)
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        data=None,
        collapsed_depth: int = 2,
        root_label: str = "root",
        copy_button: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            data=data,
            collapsed_depth=collapsed_depth,
            root_label=root_label,
            copy_button=copy_button,
            custom_class=custom_class,
            **kwargs,
        )
        self.data = data
        self.collapsed_depth = collapsed_depth
        self.root_label = root_label
        self.copy_button = copy_button
        self.custom_class = custom_class

    def _render_node(self, value, depth: int = 0) -> str:
        """Recursively render a JSON value as HTML tree nodes."""
        collapsed = depth >= self.collapsed_depth

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
                e_key = html.escape(str(k))
                items.append(
                    f'<div class="dj-json__pair">'
                    f'<span class="dj-json__key">"{e_key}"</span>'
                    f'<span class="dj-json__colon">: </span>'
                    f"{self._render_node(v, depth + 1)}</div>"
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
                    f"{self._render_node(v, depth + 1)}"
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
            return (
                f'<span class="dj-json__value dj-json__value--string">"{html.escape(value)}"</span>'
            )
        if isinstance(value, bool):
            return f'<span class="dj-json__value dj-json__value--bool">{"true" if value else "false"}</span>'
        if isinstance(value, (int, float)):
            return f'<span class="dj-json__value dj-json__value--number">{value}</span>'
        if value is None:
            return '<span class="dj-json__value dj-json__value--null">null</span>'

        return f'<span class="dj-json__value">{html.escape(str(value))}</span>'

    def _render_custom(self) -> str:
        classes = ["dj-json-viewer"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        copy_html = ""
        if self.copy_button:
            copy_html = (
                '<button class="dj-json-viewer__copy" type="button" '
                'aria-label="Copy JSON">Copy</button>'
            )

        try:
            raw_json = json.dumps(self.data, indent=2, default=str)
        except (TypeError, ValueError):
            raw_json = str(self.data)

        tree_html = self._render_node(self.data)

        return (
            f'<div class="{class_str}" dj-hook="JsonViewer" '
            f'data-collapsed-depth="{self.collapsed_depth}">'
            f'<div class="dj-json-viewer__header">'
            f'<span class="dj-json-viewer__label">'
            f"{html.escape(self.root_label)}</span>"
            f"{copy_html}</div>"
            f'<div class="dj-json-viewer__tree">{tree_html}</div>'
            f'<script type="application/json" class="dj-json-viewer__raw">'
            f"{html.escape(raw_json)}</script>"
            f"</div>"
        )
