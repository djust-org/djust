"""File Tree component — file browser with icons and context menu."""

import html
from typing import Optional

from djust import Component


class FileTree(Component):
    """File browser tree with icons, expand/collapse, and selection.

    Renders a tree of file/folder nodes. Folders can be expanded/collapsed.
    Uses ``dj-hook="FileTree"`` for client-side interactions.

    Usage in a LiveView::

        self.tree = FileTree(
            nodes=[
                {"name": "src", "type": "folder", "children": [
                    {"name": "main.py", "type": "file"},
                    {"name": "utils.py", "type": "file"},
                ]},
                {"name": "README.md", "type": "file"},
            ],
            selected="main.py",
            event="select_file",
        )

    In template::

        {{ tree|safe }}

    CSS Custom Properties::

        --dj-file-tree-bg: background color
        --dj-file-tree-fg: text color
        --dj-file-tree-selected-bg: selected item background
        --dj-file-tree-hover-bg: hover background
        --dj-file-tree-indent: indentation per level
        --dj-file-tree-font-size: font size
        --dj-file-tree-radius: border radius

    Args:
        nodes: list of node dicts with name, type (file/folder), children
        selected: name/path of currently selected file
        event: djust event fired on file selection
        show_icons: show file/folder icons (default True)
        custom_class: additional CSS classes
    """

    FILE_ICONS = {
        ".py": "&#x1F40D;",
        ".js": "&#x2B50;",
        ".ts": "&#x1F7E6;",
        ".html": "&#x1F310;",
        ".css": "&#x1F3A8;",
        ".json": "&#x1F4CB;",
        ".md": "&#x1F4DD;",
        ".txt": "&#x1F4C4;",
        ".yml": "&#x2699;",
        ".yaml": "&#x2699;",
        ".toml": "&#x2699;",
    }
    FOLDER_ICON = "&#x1F4C1;"
    FOLDER_OPEN_ICON = "&#x1F4C2;"
    DEFAULT_FILE_ICON = "&#x1F4C4;"

    def __init__(
        self,
        nodes: Optional[list] = None,
        selected: str = "",
        event: str = "select_file",
        show_icons: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            nodes=nodes,
            selected=selected,
            event=event,
            show_icons=show_icons,
            custom_class=custom_class,
            **kwargs,
        )
        self.nodes = nodes or []
        self.selected = selected
        self.event = event
        self.show_icons = show_icons
        self.custom_class = custom_class

    def _get_icon(self, name: str, node_type: str, expanded: bool = False) -> str:
        if node_type == "folder":
            return self.FOLDER_OPEN_ICON if expanded else self.FOLDER_ICON
        for ext, icon in self.FILE_ICONS.items():
            if name.endswith(ext):
                return icon
        return self.DEFAULT_FILE_ICON

    def _render_node(self, node: dict, depth: int = 0) -> str:
        if not isinstance(node, dict):
            return ""

        name = str(node.get("name", ""))
        node_type = str(node.get("type", "file"))
        children = node.get("children", [])
        expanded = node.get("expanded", True)
        e_name = html.escape(name)

        is_selected = name == self.selected
        selected_cls = " dj-file-tree__node--selected" if is_selected else ""
        type_cls = f" dj-file-tree__node--{html.escape(node_type)}"

        icon_html = ""
        if self.show_icons:
            icon = self._get_icon(name, node_type, expanded)
            icon_html = f'<span class="dj-file-tree__icon" aria-hidden="true">{icon}</span>'

        indent_style = f' style="padding-left:{depth * 1.25}rem"'

        e_event = html.escape(self.event)

        if node_type == "folder" and isinstance(children, list) and children:
            expand_cls = " dj-file-tree__node--expanded" if expanded else ""
            toggle = (
                f'<span class="dj-file-tree__toggle" role="button" tabindex="0" '
                f'aria-expanded="{"true" if expanded else "false"}">'
                f"{'&#9660;' if expanded else '&#9654;'}</span>"
            )
            children_html = []
            for child in children:
                children_html.append(self._render_node(child, depth + 1))
            child_display = ' style="display:none"' if not expanded else ""
            return (
                f'<div class="dj-file-tree__node{type_cls}{selected_cls}{expand_cls}"'
                f'{indent_style} data-name="{e_name}" data-type="{html.escape(node_type)}">'
                f"{toggle}{icon_html}"
                f'<span class="dj-file-tree__name">{e_name}</span></div>'
                f'<div class="dj-file-tree__children"{child_display}>'
                f"{''.join(children_html)}</div>"
            )

        return (
            f'<div class="dj-file-tree__node{type_cls}{selected_cls}"'
            f'{indent_style} data-name="{e_name}" data-type="{html.escape(node_type)}" '
            f'dj-click="{e_event}" role="treeitem" tabindex="0">'
            f'{icon_html}<span class="dj-file-tree__name">{e_name}</span></div>'
        )

    def _render_custom(self) -> str:
        classes = ["dj-file-tree"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_event = html.escape(self.event)
        e_selected = html.escape(self.selected)

        nodes_html = []
        for node in self.nodes:
            nodes_html.append(self._render_node(node))

        return (
            f'<div class="{class_str}" dj-hook="FileTree" '
            f'data-event="{e_event}" data-selected="{e_selected}" '
            f'role="tree">{"".join(nodes_html)}</div>'
        )
