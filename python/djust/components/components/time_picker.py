"""Time picker component for hour/minute selection."""

import html

from djust import Component


class TimePicker(Component):
    """Style-agnostic time picker component using CSS custom properties.

    Renders hour/minute selector with AM/PM toggle or 24h format.

    Usage in a LiveView::

        self.start = TimePicker(
            name="start_time",
            value="14:30",
            event="set_time",
        )

    In template::

        {{ start|safe }}

    CSS Custom Properties::

        --dj-time-picker-bg: background color
        --dj-time-picker-border: border color
        --dj-time-picker-radius: border radius (default: 0.25rem)
        --dj-time-picker-padding: padding (default: 0.5rem)

    Args:
        name: Form field name
        value: Initial time value in HH:MM format
        event: djust event handler name (for dj-change)
        format_24h: Use 24-hour format (default: False)
        min_time: Minimum selectable time (HH:MM)
        max_time: Maximum selectable time (HH:MM)
        step: Minute step interval (default: 1)
        disabled: Whether the picker is disabled
        label: Optional label text
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        name: str = "time",
        value: str = "",
        event: str = "",
        format_24h: bool = False,
        min_time: str = "",
        max_time: str = "",
        step: int = 1,
        disabled: bool = False,
        label: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            value=value,
            event=event,
            format_24h=format_24h,
            min_time=min_time,
            max_time=max_time,
            step=step,
            disabled=disabled,
            label=label,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.value = value
        self.event = event
        self.format_24h = format_24h
        self.min_time = min_time
        self.max_time = max_time
        self.step = step
        self.disabled = disabled
        self.label = label
        self.custom_class = custom_class

    def _parse_time(self, time_str):
        """Parse HH:MM string into (hour, minute)."""
        if not time_str:
            return 0, 0
        parts = time_str.split(":")
        try:
            return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return 0, 0

    def _render_custom(self) -> str:
        classes = ["dj-time-picker"]
        if self.disabled:
            classes.append("dj-time-picker--disabled")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        hour, minute = self._parse_time(self.value)

        e_name = html.escape(self.name)
        e_event = html.escape(self.event) if self.event else ""

        parts = []
        if self.label:
            parts.append(
                f'<label class="dj-time-picker__label" for="{e_name}">'
                f"{html.escape(self.label)}</label>"
            )

        event_attr = f' dj-change="{e_event}"' if e_event else ""
        disabled_attr = " disabled" if self.disabled else ""

        # Hidden input for form value
        parts.append(
            f'<input type="hidden" name="{e_name}" value="{html.escape(self.value)}"{event_attr}>'
        )

        # Hour select
        if self.format_24h:
            hour_max = 24
        else:
            hour_max = 13
            hour_start = 1
        parts.append('<div class="dj-time-picker__controls">')

        hour_options = []
        if self.format_24h:
            for h in range(24):
                sel = " selected" if h == hour else ""
                hour_options.append(f'<option value="{h}"{sel}>{h:02d}</option>')
        else:
            display_hour = hour % 12 or 12
            for h in range(1, 13):
                sel = " selected" if h == display_hour else ""
                hour_options.append(f'<option value="{h}"{sel}>{h}</option>')

        parts.append(
            f'<select class="dj-time-picker__hour" '
            f'aria-label="Hour"{disabled_attr}>'
            f"{''.join(hour_options)}</select>"
        )

        parts.append('<span class="dj-time-picker__separator">:</span>')

        # Minute select
        minute_options = []
        for m in range(0, 60, max(1, self.step)):
            sel = " selected" if m == minute else ""
            minute_options.append(f'<option value="{m}"{sel}>{m:02d}</option>')

        parts.append(
            f'<select class="dj-time-picker__minute" '
            f'aria-label="Minute"{disabled_attr}>'
            f"{''.join(minute_options)}</select>"
        )

        # AM/PM toggle
        if not self.format_24h:
            is_pm = hour >= 12
            parts.append(
                f'<select class="dj-time-picker__period" '
                f'aria-label="AM/PM"{disabled_attr}>'
                f'<option value="AM"{"" if is_pm else " selected"}>AM</option>'
                f'<option value="PM"{" selected" if is_pm else ""}>PM</option>'
                f"</select>"
            )

        parts.append("</div>")  # close controls

        return f'<div class="{class_str}">{"".join(parts)}</div>'
