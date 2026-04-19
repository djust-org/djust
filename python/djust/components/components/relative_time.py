"""Relative Time component — "3 hours ago" with auto-update."""

import html
from datetime import datetime
from typing import Union

from djust import Component


class RelativeTime(Component):
    """Displays a datetime as relative text ("3 hours ago") with optional
    client-side auto-update via interval.

    Usage in a LiveView::

        self.created = RelativeTime(datetime=self.created_at, auto_update=True)

    In template::

        {{ created|safe }}

    Args:
        datetime: ISO-format datetime string or datetime object
        auto_update: Enable client-side interval updates (default True)
        interval: Update interval in seconds (default 60)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        datetime: Union[str, "datetime", None] = None,
        auto_update: bool = True,
        interval: int = 60,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            datetime=datetime,
            auto_update=auto_update,
            interval=interval,
            custom_class=custom_class,
            **kwargs,
        )
        self.datetime_val = datetime
        self.auto_update = auto_update
        self.interval = interval
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the relative time HTML."""
        classes = ["dj-relative-time"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        # Convert datetime to ISO string if needed
        iso_val = ""
        if self.datetime_val:
            if hasattr(self.datetime_val, "isoformat"):
                iso_val = self.datetime_val.isoformat()
            else:
                iso_val = str(self.datetime_val)

        e_iso = html.escape(iso_val)
        auto_str = "true" if self.auto_update else "false"

        return (
            f'<time class="{class_str}" '
            f'datetime="{e_iso}" '
            f'data-auto-update="{auto_str}" '
            f'data-interval="{int(self.interval)}">'
            f"{e_iso}"
            f"</time>"
        )
