"""Org Chart component — hierarchical tree visualization."""

import html
from typing import Optional

from djust import Component


class OrgChart(Component):
    """Hierarchical org chart / tree visualization.

    Usage in a LiveView::

        self.org = OrgChart(
            nodes=[
                {"id": "ceo", "name": "Alice", "title": "CEO"},
                {"id": "cto", "name": "Bob", "title": "CTO", "parent": "ceo"},
                {"id": "dev1", "name": "Carol", "title": "Dev", "parent": "cto"},
            ],
            root="ceo",
        )

    In template::

        {{ org|safe }}

    Args:
        nodes: List of dicts with ``id``, ``name``, ``title``, optional ``parent``, ``avatar``
        root: ID of root node (auto-detected if omitted)
        event: djust click event for node selection
        direction: "vertical" (default) or "horizontal"
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        nodes: Optional[list] = None,
        root: Optional[str] = None,
        event: str = "",
        direction: str = "vertical",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            nodes=nodes,
            root=root,
            event=event,
            direction=direction,
            custom_class=custom_class,
            **kwargs,
        )
        self.nodes = nodes or []
        self.root = root
        self.event = event
        self.direction = direction if direction in ("vertical", "horizontal") else "vertical"
        self.custom_class = custom_class

    def _build_tree(self):
        """Build node lookup and children map."""
        node_map = {}
        children = {}
        parent_ids = set()
        child_ids = set()

        for n in self.nodes:
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id", ""))
            if not nid:
                continue
            node_map[nid] = n
            parent = n.get("parent", "")
            if parent:
                parent = str(parent)
                children.setdefault(parent, []).append(nid)
                parent_ids.add(parent)
                child_ids.add(nid)

        # Detect root
        root_id = self.root
        if not root_id:
            # Root = node that is a parent but not a child, or first node
            roots = [nid for nid in node_map if nid not in child_ids]
            root_id = roots[0] if roots else (list(node_map.keys())[0] if node_map else "")

        return node_map, children, root_id

    def _render_node(self, nid, node_map, children, e_event, depth=0):
        """Recursively render a node and its children."""
        node = node_map.get(nid)
        if not node:
            return ""

        name = html.escape(str(node.get("name", "")))
        title = html.escape(str(node.get("title", "")))
        avatar = node.get("avatar", "")

        click_attr = ""
        if e_event:
            click_attr = f' dj-click="{e_event}" data-value="{html.escape(nid)}"'

        avatar_html = ""
        if avatar:
            avatar_html = (
                f'<img class="dj-org__avatar" src="{html.escape(str(avatar))}" alt="{name}" />'
            )
        else:
            initials = "".join(w[0] for w in name.split()[:2]).upper() if name else "?"
            avatar_html = f'<span class="dj-org__initials">{html.escape(initials)}</span>'

        node_html = (
            f'<div class="dj-org__card" data-id="{html.escape(nid)}"{click_attr}>'
            f"{avatar_html}"
            f'<div class="dj-org__info">'
            f'<span class="dj-org__name">{name}</span>'
            f'<span class="dj-org__title">{title}</span>'
            f"</div></div>"
        )

        child_ids = children.get(nid, [])
        if not child_ids:
            return f'<li class="dj-org__node">{node_html}</li>'

        child_items = "".join(
            self._render_node(cid, node_map, children, e_event, depth + 1) for cid in child_ids
        )
        return (
            f'<li class="dj-org__node">{node_html}'
            f'<ul class="dj-org__children">{child_items}</ul></li>'
        )

    def _render_custom(self) -> str:
        classes = ["dj-org"]
        if self.direction == "horizontal":
            classes.append("dj-org--horizontal")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        node_map, children_map, root_id = self._build_tree()

        if not node_map or not root_id:
            return f'<div class="{class_str}" role="tree"></div>'

        e_event = html.escape(self.event) if self.event else ""

        tree_html = self._render_node(root_id, node_map, children_map, e_event)

        return (
            f'<div class="{class_str}" role="tree"><ul class="dj-org__root">{tree_html}</ul></div>'
        )
