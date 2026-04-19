"""Countdown / timer component."""

import html

from djust import Component


class Countdown(Component):
    """Style-agnostic countdown timer component.

    Displays days/hours/minutes/seconds countdown to a target datetime.

    Usage in a LiveView::

        self.timer = Countdown(
            target="2026-04-01T00:00:00",
            event="timer_done",
        )

    In template::

        {{ timer|safe }}

    Args:
        target: ISO 8601 datetime string for countdown target
        event: djust event to fire when countdown reaches zero
        show_days: Show days segment (default: True)
        show_seconds: Show seconds segment (default: True)
        labels: Dict of custom labels for segments
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        target: str = "",
        event: str = "",
        show_days: bool = True,
        show_seconds: bool = True,
        labels: dict = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            target=target,
            event=event,
            show_days=show_days,
            show_seconds=show_seconds,
            labels=labels,
            custom_class=custom_class,
            **kwargs,
        )
        self.target = target
        self.event = event
        self.show_days = show_days
        self.show_seconds = show_seconds
        self.labels = labels or {}
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-countdown"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_target = html.escape(self.target)
        e_event = html.escape(self.event) if self.event else ""

        event_attr = f' data-event="{e_event}"' if e_event else ""

        # Build segments (actual countdown done client-side)
        segments = []
        default_labels = {
            "days": "Days",
            "hours": "Hours",
            "minutes": "Minutes",
            "seconds": "Seconds",
        }
        merged_labels = {**default_labels, **self.labels}

        if self.show_days:
            segments.append(
                f'<div class="dj-countdown__segment">'
                f'<span class="dj-countdown__value" data-unit="days">00</span>'
                f'<span class="dj-countdown__label">{html.escape(merged_labels["days"])}</span>'
                f"</div>"
            )
        segments.append(
            f'<div class="dj-countdown__segment">'
            f'<span class="dj-countdown__value" data-unit="hours">00</span>'
            f'<span class="dj-countdown__label">{html.escape(merged_labels["hours"])}</span>'
            f"</div>"
        )
        segments.append(
            f'<div class="dj-countdown__segment">'
            f'<span class="dj-countdown__value" data-unit="minutes">00</span>'
            f'<span class="dj-countdown__label">{html.escape(merged_labels["minutes"])}</span>'
            f"</div>"
        )
        if self.show_seconds:
            segments.append(
                f'<div class="dj-countdown__segment">'
                f'<span class="dj-countdown__value" data-unit="seconds">00</span>'
                f'<span class="dj-countdown__label">{html.escape(merged_labels["seconds"])}</span>'
                f"</div>"
            )

        separators = []
        for i, seg in enumerate(segments):
            separators.append(seg)
            if i < len(segments) - 1:
                separators.append('<span class="dj-countdown__separator">:</span>')

        return (
            f'<div class="{class_str}" dj-hook="Countdown" '
            f'data-target="{e_target}"{event_attr} '
            f'role="timer">{"".join(separators)}</div>'
        )
