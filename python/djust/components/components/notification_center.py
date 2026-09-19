"""NotificationCenter component."""

import html

from djust import Component
from typing import Any, Optional


class NotificationCenter(Component):
    """Notification bell with dropdown list component.

    Args:
        notifications: list of dicts with keys: id, message, time, unread
        unread_count: number of unread notifications
        open_event, mark_read_event, clear_event: dj-click events"""

    #: ADR-033 D3: the walked state keys; ``notifications`` (the data) compares by
    #: identity, so reassign it to re-render — never a per-node walk per click.
    fingerprint_fields = ()

    def __init__(
        self,
        notifications: Optional[list] = None,
        unread_count: int = 0,
        open_event: str = "toggle_notifications",
        mark_read_event: str = "mark_notification_read",
        clear_event: str = "clear_notifications",
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            notifications=notifications,
            unread_count=unread_count,
            open_event=open_event,
            mark_read_event=mark_read_event,
            clear_event=clear_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.notifications = notifications or []
        self.unread_count = unread_count
        self.open_event = open_event
        self.mark_read_event = mark_read_event
        self.clear_event = clear_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the notificationcenter HTML."""
        notifications = self.notifications or []
        cls = "notif-center"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        badge_html = (
            f'<span class="notif-badge">{self.unread_count}</span>' if self.unread_count > 0 else ""
        )
        items_html = ""
        for n in notifications:
            if not isinstance(n, dict):
                continue
            nid = n.get("id", "")
            msg = html.escape(str(n.get("message", "")))
            time_ = html.escape(str(n.get("time", "")))
            unread_cls = " notif-item-unread" if n.get("unread", False) else ""
            time_html = f'<span class="notif-item-time">{time_}</span>' if time_ else ""
            items_html += (
                f'<div class="notif-item{unread_cls}" {self.event_attrs(self.mark_read_event, value=nid)}>'
                f'<div class="notif-item-msg">{msg}</div>{time_html}</div>'
            )
        if not items_html:
            items_html = '<div class="notif-empty">No notifications</div>'
        return (
            f'<div class="{cls}">'
            f'<button class="notif-trigger" {self.event_attrs(self.open_event)}>'
            f'<span class="notif-bell">&#128276;</span>{badge_html}</button>'
            f'<div class="notif-dropdown">'
            f'<div class="notif-list">{items_html}</div>'
            f"</div></div>"
        )
