"""SkeletonFactory component for auto-generating skeleton loading states."""

import html

from djust import Component


class SkeletonFactory(Component):
    """Style-agnostic skeleton loading state generator.

    Auto-generates matching skeleton placeholder UI for common components
    like data tables, cards, and lists. Uses CSS pulse animation.

    Usage in a LiveView::

        # Table skeleton
        self.loading = SkeletonFactory(component="data_table", columns=5, rows=10)

        # Card skeleton
        self.card_loading = SkeletonFactory(component="card")

        # List skeleton
        self.list_loading = SkeletonFactory(component="list", rows=6)

        # Generic lines skeleton
        self.text_loading = SkeletonFactory(component="text", rows=4)

    In template::

        {{ loading|safe }}

    CSS Custom Properties::

        --dj-skeleton-bg: skeleton element background (default: #e5e7eb)
        --dj-skeleton-shine: shimmer highlight color (default: #f3f4f6)
        --dj-skeleton-radius: border radius (default: 0.25rem)
        --dj-skeleton-speed: animation duration (default: 1.5s)

    Args:
        component: Target component type (data_table, card, list, text)
        columns: Number of columns (for data_table, default: 4)
        rows: Number of rows (for data_table/list/text, default: 5)
        custom_class: Additional CSS classes
    """

    SUPPORTED_COMPONENTS = {"data_table", "card", "list", "text"}

    def __init__(
        self,
        component: str = "text",
        columns: int = 4,
        rows: int = 5,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            component=component,
            columns=columns,
            rows=rows,
            custom_class=custom_class,
            **kwargs,
        )
        self.component = component
        self.columns = columns
        self.rows = rows
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-skeleton"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        component = self.component
        if component not in self.SUPPORTED_COMPONENTS:
            component = "text"

        cols = int(self.columns)
        rows = int(self.rows)

        if component == "data_table":
            return self._render_table(cls, cols, rows)
        elif component == "card":
            return self._render_card(cls)
        elif component == "list":
            return self._render_list(cls, rows)
        else:
            return self._render_text(cls, rows)

    def _render_table(self, cls: str, cols: int, rows: int) -> str:
        header_cells = "".join(
            '<th><span class="dj-skeleton__line dj-skeleton__pulse" '
            'style="width:70%">&nbsp;</span></th>'
            for _ in range(cols)
        )
        header = f"<thead><tr>{header_cells}</tr></thead>"

        body_rows = []
        for _ in range(rows):
            cells = "".join(
                '<td><span class="dj-skeleton__line dj-skeleton__pulse">&nbsp;</span></td>'
                for _ in range(cols)
            )
            body_rows.append(f"<tr>{cells}</tr>")
        body = f"<tbody>{''.join(body_rows)}</tbody>"

        return (
            f'<div class="{cls} dj-skeleton--data-table" '
            f'role="status" aria-label="Loading">'
            f'<table class="dj-skeleton__table">{header}{body}</table>'
            f"</div>"
        )

    def _render_card(self, cls: str) -> str:
        return (
            f'<div class="{cls} dj-skeleton--card" '
            f'role="status" aria-label="Loading">'
            f'<div class="dj-skeleton__card-image dj-skeleton__pulse">&nbsp;</div>'
            f'<div class="dj-skeleton__card-body">'
            f'<span class="dj-skeleton__line dj-skeleton__pulse" '
            f'style="width:60%">&nbsp;</span>'
            f'<span class="dj-skeleton__line dj-skeleton__pulse" '
            f'style="width:90%">&nbsp;</span>'
            f'<span class="dj-skeleton__line dj-skeleton__pulse" '
            f'style="width:40%">&nbsp;</span>'
            f"</div></div>"
        )

    def _render_list(self, cls: str, rows: int) -> str:
        items = []
        for _ in range(rows):
            items.append(
                '<div class="dj-skeleton__list-item">'
                '<span class="dj-skeleton__circle dj-skeleton__pulse">&nbsp;</span>'
                '<span class="dj-skeleton__line dj-skeleton__pulse" '
                'style="width:80%">&nbsp;</span>'
                "</div>"
            )
        return (
            f'<div class="{cls} dj-skeleton--list" '
            f'role="status" aria-label="Loading">'
            f"{''.join(items)}</div>"
        )

    def _render_text(self, cls: str, rows: int) -> str:
        widths = [95, 85, 90, 70, 80, 60, 75, 88, 65, 92]
        lines = []
        for i in range(rows):
            w = widths[i % len(widths)]
            lines.append(
                f'<span class="dj-skeleton__line dj-skeleton__pulse" '
                f'style="width:{w}%">&nbsp;</span>'
            )
        return (
            f'<div class="{cls} dj-skeleton--text" '
            f'role="status" aria-label="Loading">'
            f"{''.join(lines)}</div>"
        )
