"""NotificationCenter component."""

import html

from djust import Component


class NotificationCenter(Component):
    """Notification bell with dropdown list component.

    Args:
        notifications: list of dicts with keys: id, message, time, unread
        unread_count: number of unread notifications
        open_event, mark_read_event, clear_event: dj-click events"""

    def __init__(
        self,
        notifications: list = None,
        unread_count: int = 0,
        open_event: str = "toggle_notifications",
        mark_read_event: str = "mark_notification_read",
        clear_event: str = "clear_notifications",
        custom_class: str = "",
        **kwargs,
    ):
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
        e_open = html.escape(self.open_event)
        e_read = html.escape(self.mark_read_event)
        badge_html = (
            f'<span class="notif-badge">{self.unread_count}</span>' if self.unread_count > 0 else ""
        )
        items_html = ""
        for n in notifications:
            if not isinstance(n, dict):
                continue
            nid = html.escape(str(n.get("id", "")))
            msg = html.escape(str(n.get("message", "")))
            time_ = html.escape(str(n.get("time", "")))
            unread_cls = " notif-item-unread" if n.get("unread", False) else ""
            time_html = f'<span class="notif-item-time">{time_}</span>' if time_ else ""
            items_html += (
                f'<div class="notif-item{unread_cls}" dj-click="{e_read}" data-value="{nid}">'
                f'<div class="notif-item-msg">{msg}</div>{time_html}</div>'
            )
        if not items_html:
            items_html = '<div class="notif-empty">No notifications</div>'
        return (
            f'<div class="{cls}">'
            f'<button class="notif-trigger" dj-click="{e_open}">'
            f'<span class="notif-bell">&#128276;</span>{badge_html}</button>'
            f'<div class="notif-dropdown">'
            f'<div class="notif-list">{items_html}</div>'
            f"</div></div>"
        )
