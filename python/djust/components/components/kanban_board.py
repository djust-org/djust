"""KanbanBoard component."""

import html

from djust import Component


class KanbanBoard(Component):
    """Kanban board component.

    Args:
        columns: list of dicts with keys: id, title, color, cards (list of dicts)
        move_event: dj-click event for drag-drop
        add_card_event: dj-click event for adding cards"""

    def __init__(
        self,
        columns: list = None,
        move_event: str = "kanban_move",
        add_card_event: str = "kanban_add_card",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            columns=columns,
            move_event=move_event,
            add_card_event=add_card_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.columns = columns or []
        self.move_event = move_event
        self.add_card_event = add_card_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the kanbanboard HTML."""
        columns = self.columns or []
        if not columns:
            return '<div class="kanban"></div>'
        cls = "kanban"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_add = html.escape(self.add_card_event)
        cols_html = ""
        for col in columns:
            if not isinstance(col, dict):
                continue
            col_id = html.escape(str(col.get("id", "")))
            col_title = html.escape(str(col.get("title", "")))
            cards = col.get("cards", [])
            cards_html = ""
            for card in cards:
                if not isinstance(card, dict):
                    continue
                card_title = html.escape(str(card.get("title", "")))
                cards_html += f'<div class="kanban-card"><div class="kanban-card-title">{card_title}</div></div>'
            cols_html += (
                f'<div class="kanban-col" data-col-id="{col_id}">'
                f'<div class="kanban-col-header"><span class="kanban-col-title">{col_title}</span></div>'
                f'<div class="kanban-cards">{cards_html}</div>'
                f'<button class="kanban-add-card" dj-click="{e_add}" data-value="{col_id}">+ Add card</button>'
                f"</div>"
            )
        return f'<div class="{cls}">{cols_html}</div>'
