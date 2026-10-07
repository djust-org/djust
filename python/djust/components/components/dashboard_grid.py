"""Dashboard Grid component — CSS Grid with draggable, resizable panels."""

import html
from typing import Any, Optional

from django.utils.html import conditional_escape
from djust import Component


#: The largest grid a dashboard renders: columns and rows are capped so a stored
#: or forged number cannot ask the browser for millions of tracks.
MAX_COLUMNS = 48
MAX_ROWS = 100


def clamp_columns(columns: Any) -> int:
    """``columns`` as a whole number of columns, ``1 <= n <= MAX_COLUMNS``."""
    return max(1, min(int(columns), MAX_COLUMNS))


def clamp_cells(
    columns: Any, col: Any, row: Any, width: Any, height: Any
) -> tuple[int, int, int, int]:
    """``(col, row, width, height)`` forced onto the grid: ``1 <= col`` and
    ``col + width - 1 <= columns``, ``1 <= row`` and ``row + height - 1 <= MAX_ROWS``.

    The renderer uses it for every panel, and an event handler can use it to
    validate what the browser sent (the values are untrusted)."""
    cols = clamp_columns(columns)
    col = max(1, min(int(col), cols))
    width = max(1, min(int(width), cols - col + 1))
    row = max(1, min(int(row), MAX_ROWS))
    height = max(1, min(int(height), MAX_ROWS - row + 1))
    return col, row, width, height


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

    What the hook sends (``djust_components/dashboard-grid.js``): dragging a
    panel's header (or Space/Enter, then the arrow keys, on a focused panel)
    sends ``move_event`` with ``{"id": "chart", "col": 2, "row": 1}``, and
    dragging its bottom handle (or Shift+arrow keys while grabbed) sends
    ``resize_event`` with ``{"id": "chart", "width": 3, "height": 2}``. All
    numbers are whole grid units, 1-based for ``col`` and ``row``. Nothing is
    moved on the client: the panel stays where it is until the view
    re-renders with the new numbers, so an app that ignores the event leaves
    the dashboard as it was.

    These payloads come from the browser and are **untrusted**: a forged
    ``row=1000000000`` would, stored as it is, ask every viewer's browser for a
    grid millions of tracks tall. Check that the ``id`` is one of this
    dashboard's panels and that the user may change it, and force the numbers
    onto the grid with ``clamp_cells`` (the renderer applies the same limits,
    ``MAX_COLUMNS`` = 48 columns and ``MAX_ROWS`` = 100 rows, to whatever is
    stored)::

        from djust.components.components.dashboard_grid import clamp_cells

        @event_handler()
        def dashboard_move(self, id: str = "", col: int = 1, row: int = 1, **kwargs):
            panel = self.panels_by_id.get(id)  # unknown id: ignore
            if panel is None or not self.can_edit(panel):
                return
            panel["col"], panel["row"], _, _ = clamp_cells(
                self.columns, col, row, panel["width"], panel["height"]
            )

        @event_handler()
        def dashboard_resize(self, id: str = "", width: int = 1, height: int = 1, **kwargs):
            panel = self.panels_by_id.get(id)
            if panel is None or not self.can_edit(panel):
                return
            _, _, panel["width"], panel["height"] = clamp_cells(
                self.columns, panel["col"], panel["row"], width, height
            )

    (djust rejects a value that is not an ``int`` before the handler runs.)

    Overlap is the app's call: the grid places panels where their numbers say.
    Panels are matched by ``id``, so give every panel a unique one.

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
        **kwargs: Any,
    ) -> None:
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

        cols = clamp_columns(self.columns)

        panels_html = []
        for panel in self.panels:
            if not isinstance(panel, dict):
                continue
            pid = html.escape(str(panel.get("id", "")))
            title = html.escape(str(panel.get("title", "")))
            content = panel.get("content", "")
            col, row, w, h = clamp_cells(
                cols,
                panel.get("col", 1),
                panel.get("row", 1),
                panel.get("width", 1),
                panel.get("height", 1),
            )

            style = f"grid-column:{col}/span {w};grid-row:{row}/span {h}"

            panels_html.append(
                f'<div class="dj-dashboard-grid__panel" data-panel-id="{pid}" '
                f'style="{style}" draggable="true">'
                f'<div class="dj-dashboard-grid__panel-header">'
                f'<span class="dj-dashboard-grid__panel-title">{title}</span>'
                f'<span class="dj-dashboard-grid__panel-drag" aria-hidden="true">&#x2630;</span>'
                f"</div>"
                f'<div class="dj-dashboard-grid__panel-body">{conditional_escape(content)}</div>'
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
