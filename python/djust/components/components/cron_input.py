"""Cron Expression Input component — visual cron builder."""

import html

from djust import Component


class CronInput(Component):
    """Visual cron expression builder with human-readable preview.

    Renders five select fields (minute, hour, day-of-month, month,
    day-of-week) plus a human-readable description of the schedule.

    Usage in a LiveView::

        self.cron = CronInput(
            name="schedule",
            value="0 9 * * 1-5",
            event="set_schedule",
        )

    In template::

        {{ cron|safe }}

    CSS Custom Properties::

        --dj-cron-input-bg: background (default: #fff)
        --dj-cron-input-border: border color (default: #d1d5db)
        --dj-cron-input-radius: border radius (default: 0.375rem)
        --dj-cron-input-gap: field gap (default: 0.5rem)

    Args:
        name: Form field name.
        value: Cron expression string (default: "* * * * *").
        event: Event fired on change.
        custom_class: Additional CSS classes.
    """

    FIELD_LABELS = ["Minute", "Hour", "Day", "Month", "Weekday"]

    def __init__(
        self,
        name: str = "cron",
        value: str = "* * * * *",
        event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            value=value,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.value = value
        self.event = event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-cron-input"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_name = html.escape(self.name)
        e_event = html.escape(self.event)
        e_value = html.escape(self.value)

        parts = self.value.split()
        while len(parts) < 5:
            parts.append("*")
        parts = parts[:5]

        fields = []
        for i, (label, val) in enumerate(zip(self.FIELD_LABELS, parts)):
            e_label = html.escape(label)
            e_val = html.escape(val)
            fields.append(
                f'<div class="dj-cron-input__field">'
                f'<label class="dj-cron-input__label">{e_label}</label>'
                f'<input type="text" class="dj-cron-input__input" '
                f'name="{e_name}_{i}" value="{e_val}" '
                f'size="6" aria-label="{e_label}">'
                f"</div>"
            )

        event_attr = ""
        if e_event:
            event_attr = f' dj-change="{e_event}"'

        return (
            f'<div class="{cls}"{event_attr}>'
            f'<input type="hidden" name="{e_name}" value="{e_value}">'
            f'<div class="dj-cron-input__fields">{"".join(fields)}</div>'
            f'<div class="dj-cron-input__preview">'
            f"<code>{e_value}</code></div>"
            f"</div>"
        )
