"""TreeView component."""

import html

from djust import Component


class TreeView(Component):
    """Expandable tree view component.

    Args:
        nodes: list of dicts with keys: id, label, expanded (bool), children (list)
        expand_event: dj-click event for expanding nodes
        select_event: dj-click event for selecting nodes
        selected: currently selected node id"""

    def __init__(
        self,
        nodes: list = None,
        expand_event: str = "tree_expand",
        select_event: str = "tree_select",
        selected: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            nodes=nodes,
            expand_event=expand_event,
            select_event=select_event,
            selected=selected,
            custom_class=custom_class,
            **kwargs,
        )
        self.nodes = nodes or []
        self.expand_event = expand_event
        self.select_event = select_event
        self.selected = selected
        self.custom_class = custom_class

    def _render_node(self, node, depth):
        """Render a single tree node recursively."""
        if not isinstance(node, dict):
            return ""
        nid = html.escape(str(node.get("id", "")))
        label = html.escape(str(node.get("label", "")))
        children = node.get("children", [])
        expanded = node.get("expanded", False)
        has_children = bool(children)
        sel_cls = " tree-node-selected" if str(node.get("id", "")) == self.selected else ""
        exp_cls = " tree-node-expanded" if expanded else ""
        indent = depth * 1.25
        e_expand = html.escape(self.expand_event)
        e_select = html.escape(self.select_event)
        toggle_html = (
            f'<button class="tree-toggle" dj-click="{e_expand}" data-value="{nid}">'
            f"{'&#9662;' if expanded else '&#9656;'}</button>"
            if has_children
            else '<span class="tree-toggle-placeholder"></span>'
        )
        children_html = ""
        if has_children and expanded:
            children_html = (
                '<div class="tree-children">'
                + "".join(self._render_node(c, depth + 1) for c in children)
                + "</div>"
            )
        return (
            f'<div class="tree-node{sel_cls}{exp_cls}" style="padding-left:{indent}rem">'
            f'<div class="tree-node-row">{toggle_html}'
            f'<button class="tree-node-label" dj-click="{e_select}" data-value="{nid}">'
            f"{label}</button></div>"
            f"{children_html}</div>"
        )

    def _render_custom(self) -> str:
        """Render the treeview HTML."""
        if not self.nodes:
            return '<div class="tree"></div>'
        cls = "tree"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_expand = html.escape(self.expand_event)
        e_select = html.escape(self.select_event)
        nodes_html = "".join(self._render_node(n, 0) for n in self.nodes)
        return f'<div class="{cls}">{nodes_html}</div>'
