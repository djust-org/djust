"""Activity Feed component for real-time activity stream via WebSocket."""

import html
from typing import Any, List, Optional

from djust import Component
from djust.components.utils import url_attr


class ActivityFeed(Component):
    """Real-time activity feed with streaming support.

    Renders a chronological list of activity events with user avatars,
    timestamps, and action descriptions. Supports WebSocket streaming
    for live updates.

    With ``stream_event`` the component carries ``dj-hook="ActivityFeed"``; the
    page must include ``djust_components/activity-feed.js``. The hook puts the
    events the view pushes at the top of the feed (newest first, as rendered),
    keeps at most ``max_items`` rows, announces new activity in a polite live
    region, and makes the feed keyboard-navigable (each article focusable,
    Page Down / Page Up between them, as in the WAI-ARIA feed pattern)::

        self.push_event("activity_update", {"events": [
            {"user": "Alice", "action": "commented on", "target": "Issue #42",
             "time": "now"}]})

    Every field is shown as text, and an avatar only if it is an http(s),
    relative or inline image URL. Stream the events or re-render ``events``,
    not both for the same feed.
    An app's own ``ActivityFeed`` hook, in ``window.djust.hooks`` or
    ``window.DjustHooks``, replaces the shipped one (a hook assigned with
    ``window.DjustHooks = {...}`` after the script also drops it; merge with
    ``Object.assign`` instead).

    Usage in a LiveView::

        self.feed = ActivityFeed(
            events=[
                {"user": "Alice", "action": "commented on", "target": "Issue #42",
                 "time": "2m ago", "avatar": "/img/alice.jpg"},
                {"user": "Bob", "action": "merged", "target": "PR #17",
                 "time": "5m ago"},
            ],
            stream_event="activity_update",
        )

    In template::

        {{ feed|safe }}

    CSS Custom Properties::

        --dj-activity-bg: background color
        --dj-activity-border-color: item separator color
        --dj-activity-time-color: timestamp text color
        --dj-activity-avatar-size: avatar diameter (default: 2rem)

    Args:
        events: List of event dicts with user, action, target, time, avatar.
        stream_event: WebSocket event name for live updates.
        max_items: Maximum number of visible items (default: 50).
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        events: Optional[List[dict]] = None,
        stream_event: str = "",
        max_items: int = 50,
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            events=events,
            stream_event=stream_event,
            max_items=max_items,
            custom_class=custom_class,
            **kwargs,
        )
        self.events = events or []
        self.stream_event = stream_event
        self.max_items = max_items
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-activity-feed"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        attrs = [f'class="{cls}"', 'role="feed"', 'aria-label="Activity feed"']
        if self.stream_event:
            attrs.append(f'data-stream-event="{html.escape(self.stream_event)}"')
            attrs.append('dj-hook="ActivityFeed"')
            # What the hook needs to keep a streamed feed to the same length.
            try:
                attrs.append(f'data-max-items="{int(self.max_items)}"')
            except (ValueError, TypeError):
                pass

        visible = self.events[: self.max_items]

        items = []
        for event in visible:
            if not isinstance(event, dict):
                continue

            user = html.escape(str(event.get("user", "")))
            action = html.escape(str(event.get("action", "")))
            target = html.escape(str(event.get("target", "")))
            time = html.escape(str(event.get("time", "")))
            avatar_src = url_attr(event.get("avatar", ""), image=True)
            icon = html.escape(str(event.get("icon", "")))

            initials = (
                html.escape(
                    "".join(w[0].upper() for w in str(event.get("user", "")).split()[:2] if w)
                )
                or "?"
            )

            if avatar_src:
                avatar_html = (
                    f'<img src="{avatar_src}" alt="{user}" class="dj-activity-feed__avatar-img">'
                )
            else:
                avatar_html = f'<span class="dj-activity-feed__avatar-initials">{initials}</span>'

            icon_html = ""
            if icon:
                icon_html = f'<span class="dj-activity-feed__icon">{icon}</span>'

            time_html = ""
            if time:
                time_html = f'<span class="dj-activity-feed__time">{time}</span>'

            target_html = ""
            if target:
                target_html = f' <span class="dj-activity-feed__target">{target}</span>'

            items.append(
                f'<div class="dj-activity-feed__item" role="article">'
                f'<span class="dj-activity-feed__avatar">{avatar_html}</span>'
                f'<div class="dj-activity-feed__body">'
                f"{icon_html}"
                f'<span class="dj-activity-feed__text">'
                f'<strong class="dj-activity-feed__user">{user}</strong> '
                f"{action}{target_html}</span>"
                f"{time_html}"
                f"</div></div>"
            )

        attrs_str = " ".join(attrs)
        return f"<div {attrs_str}>{''.join(items)}</div>"
