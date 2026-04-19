"""Notification Popover component — bell icon with unread badge + popover list."""

import html
from typing import List, Optional, Union

from djust import Component


class NotificationPopover(Component):
    """Style-agnostic notification popover using CSS custom properties.

    Renders a bell icon button with an unread badge count and a toggleable
    popover panel listing notifications.

    Usage in a LiveView::

        self.notifs = NotificationPopover(
            notifications=[
                {"id": "1", "title": "Deploy", "body": "v2 deployed", "time": "2m ago"},
                {"id": "2", "title": "PR", "body": "PR #42 merged", "read": True},
            ],
            unread_count=1,
            is_open=False,
        )

    In template::

        {{ notifs|safe }}

    CSS Custom Properties::

        --dj-notif-popover-bg: panel background (default: white)
        --dj-notif-popover-border: panel border (default: #e5e7eb)
        --dj-notif-popover-shadow: panel shadow
        --dj-notif-popover-badge-bg: badge background (default: #ef4444)
        --dj-notif-popover-badge-fg: badge text color (default: white)
        --dj-notif-popover-width: panel width (default: 20rem)

    Args:
        notifications: List of notification dicts with id, title, body, time, read.
        unread_count: Number of unread notifications (shown on badge).
        mark_read_event: djust event fired when an unread item is clicked.
        toggle_event: djust event to open/close popover.
        is_open: Whether the popover is open.
        title: Header text (default "Notifications").
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        notifications: Optional[List[Union[dict, object]]] = None,
        unread_count: int = 0,
        mark_read_event: str = "mark_read",
        toggle_event: str = "toggle_notifications",
        is_open: bool = False,
        title: str = "Notifications",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            notifications=notifications,
            unread_count=unread_count,
            mark_read_event=mark_read_event,
            toggle_event=toggle_event,
            is_open=is_open,
            title=title,
            custom_class=custom_class,
            **kwargs,
        )
        self.notifications = notifications or []
        self.unread_count = unread_count
        self.mark_read_event = mark_read_event
        self.toggle_event = toggle_event
        self.is_open = is_open
        self.title = title
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the notification popover HTML."""
        e_toggle = html.escape(str(self.toggle_event))
        e_mark = html.escape(str(self.mark_read_event))
        e_title = html.escape(str(self.title))

        # Badge
        badge_html = ""
        if self.unread_count > 0:
            display = "99+" if self.unread_count > 99 else str(self.unread_count)
            badge_html = f'<span class="dj-notif-popover__badge">{display}</span>'

        open_cls = "dj-notif-popover--open" if self.is_open else ""
        classes = ["dj-notif-popover", open_cls]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        cls = " ".join(c for c in classes if c)

        bell_html = (
            f'<button class="dj-notif-popover__bell" dj-click="{e_toggle}" '
            f'aria-label="Notifications">'
            f'<svg class="dj-notif-popover__icon" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" width="20" height="20">'
            f'<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>'
            f'<path d="M13.73 21a2 2 0 0 1-3.46 0"/>'
            f"</svg>"
            f"{badge_html}"
            f"</button>"
        )

        items_html = []
        for notif in self.notifications:
            if isinstance(notif, dict):
                n_id = notif.get("id", "")
                n_title = notif.get("title", "")
                n_body = notif.get("body", notif.get("message", ""))
                n_time = notif.get("time", "")
                n_read = notif.get("read", False)
            else:
                n_id = getattr(notif, "id", "")
                n_title = getattr(notif, "title", "")
                n_body = getattr(notif, "body", getattr(notif, "message", ""))
                n_time = getattr(notif, "time", "")
                n_read = getattr(notif, "read", False)
            e_n_id = html.escape(str(n_id))
            e_n_title = html.escape(str(n_title))
            e_n_body = html.escape(str(n_body))
            e_n_time = html.escape(str(n_time))
            read_cls = "dj-notif-popover__item--read" if n_read else ""
            mark_attr = ""
            if not n_read:
                mark_attr = f' dj-click="{e_mark}" data-id="{e_n_id}"'
            items_html.append(
                f'<div class="dj-notif-popover__item {read_cls}"{mark_attr}>'
                f'<div class="dj-notif-popover__item-title">{e_n_title}</div>'
                f'<div class="dj-notif-popover__item-body">{e_n_body}</div>'
                f'<div class="dj-notif-popover__item-time">{e_n_time}</div>'
                f"</div>"
            )

        panel_html = ""
        if self.is_open:
            empty = ""
            if not self.notifications:
                empty = '<div class="dj-notif-popover__empty">No notifications</div>'
            panel_html = (
                f'<div class="dj-notif-popover__panel">'
                f'<div class="dj-notif-popover__header">{e_title}</div>'
                f"{''.join(items_html)}{empty}"
                f"</div>"
            )

        return f'<div class="{cls}">{bell_html}{panel_html}</div>'
