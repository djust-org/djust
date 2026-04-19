"""Dashboard Grid component — CSS Grid with draggable, resizable panels."""

import html
from typing import Optional

from djust import Component


class DashboardGrid(Component):
    """CSS Grid layout with draggable and resizable dashboard panels.

    Uses ``dj-hook="DashboardGrid"`` for client-side drag/resize interactions.

    Usage in a LiveView::

        self.dashboard = DashboardGrid(
            panels=[
                {"id": "chart", "title": "Revenue", "col": 1, "row": 1,
                 "width": 2, "height": 1, "content": "<canvas>...</canvas>"},
                {"id": "stats", "title": "Users", "col": 3, "row": 1,
                 "width": 1, "height": 1, "content": "<p>1234</p>"},
            ],
            columns=4,
            move_event="dashboard_move",
            resize_event="dashboard_resize",
        )

    In template::

        {{ dashboard|safe }}

    Args:
        panels: list of panel dicts with id, title, col, row, width, height, content
        columns: number of grid columns (default 4)
        row_height: CSS row height (default "200px")
        gap: CSS gap (default "1rem")
        move_event: djust event on panel drag
        resize_event: djust event on panel resize
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        panels: Optional[list] = None,
        columns: int = 4,
        row_height: str = "200px",
        gap: str = "1rem",
        move_event: str = "dashboard_move",
        resize_event: str = "dashboard_resize",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            panels=panels,
            columns=columns,
            row_height=row_height,
            gap=gap,
            move_event=move_event,
            resize_event=resize_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.panels = panels or []
        self.columns = columns
        self.row_height = row_height
        self.gap = gap
        self.move_event = move_event
        self.resize_event = resize_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-dashboard-grid"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_move = html.escape(self.move_event)
        e_resize = html.escape(self.resize_event)
        e_gap = html.escape(self.gap)
        e_row_height = html.escape(self.row_height)

        cols = int(self.columns)

        panels_html = []
        for panel in self.panels:
            if not isinstance(panel, dict):
                continue
            pid = html.escape(str(panel.get("id", "")))
            title = html.escape(str(panel.get("title", "")))
            content = panel.get("content", "")
            col = int(panel.get("col", 1))
            row = int(panel.get("row", 1))
            w = int(panel.get("width", 1))
            h = int(panel.get("height", 1))

            style = f"grid-column:{col}/span {w};grid-row:{row}/span {h}"

            panels_html.append(
                f'<div class="dj-dashboard-grid__panel" data-panel-id="{pid}" '
                f'style="{style}" draggable="true">'
                f'<div class="dj-dashboard-grid__panel-header">'
                f'<span class="dj-dashboard-grid__panel-title">{title}</span>'
                f'<span class="dj-dashboard-grid__panel-drag" aria-hidden="true">&#x2630;</span>'
                f"</div>"
                f'<div class="dj-dashboard-grid__panel-body">{content}</div>'
                f'<div class="dj-dashboard-grid__panel-resize" role="separator"></div>'
                f"</div>"
            )

        grid_style = (
            f'style="display:grid;grid-template-columns:repeat({cols},1fr);'
            f'grid-auto-rows:minmax({e_row_height},auto);gap:{e_gap}"'
        )

        return (
            f'<div class="{class_str}" dj-hook="DashboardGrid" '
            f'data-move-event="{e_move}" data-resize-event="{e_resize}" '
            f'data-columns="{cols}" {grid_style}>{"".join(panels_html)}</div>'
        )
