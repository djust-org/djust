"""Calendar View component — month/week/day calendar with event slots."""

import html
import calendar
from typing import Optional

from djust import Component


class CalendarView(Component):
    """Month/week/day calendar view with event slots.

    Usage in a LiveView::

        self.cal = CalendarView(
            events=[
                {"date": "2026-03-25", "title": "Team standup", "color": "#3b82f6"},
                {"date": "2026-03-25", "title": "Lunch", "color": "#22c55e"},
                {"date": "2026-03-28", "title": "Deploy", "color": "#ef4444"},
            ],
            month=3,
            year=2026,
        )

    In template::

        {{ cal|safe }}

    Args:
        events: List of dicts with ``date`` (YYYY-MM-DD), ``title``, optional ``color``
        month: Month number (1-12)
        year: Four-digit year
        view: "month", "week", or "day" (default: "month")
        start_day: First day of week, 0=Mon 6=Sun (default: 0)
        event: djust click event for day cells
        custom_class: Additional CSS classes
    """

    COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6"]

    def __init__(
        self,
        events: Optional[list] = None,
        month: int = 1,
        year: int = 2026,
        view: str = "month",
        start_day: int = 0,
        event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            events=events,
            month=month,
            year=year,
            view=view,
            start_day=start_day,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.events = events or []
        try:
            self.month = int(month)
        except (ValueError, TypeError):
            self.month = 1
        try:
            self.year = int(year)
        except (ValueError, TypeError):
            self.year = 2026
        self.view = view if view in ("month", "week", "day") else "month"
        self.start_day = start_day
        self.event = event
        self.custom_class = custom_class

    def _build_event_map(self):
        """Group events by date string."""
        emap = {}
        for ev in self.events:
            if not isinstance(ev, dict):
                continue
            d = str(ev.get("date", ""))
            if d:
                emap.setdefault(d, []).append(ev)
        return emap

    def _render_custom(self) -> str:
        classes = ["dj-calendar"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        emap = self._build_event_map()

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        sd = self.start_day % 7
        day_names = day_names[sd:] + day_names[:sd]

        try:
            month_name = calendar.month_name[self.month]
        except (IndexError, KeyError):
            month_name = ""
        header = (
            f'<div class="dj-calendar__header">'
            f'<span class="dj-calendar__title">'
            f"{html.escape(month_name)} {html.escape(str(self.year))}</span></div>"
        )

        dn_cells = "".join(
            f'<div class="dj-calendar__dayname">{html.escape(d)}</div>' for d in day_names
        )
        day_names_row = f'<div class="dj-calendar__daynames">{dn_cells}</div>'

        try:
            cal = calendar.Calendar(firstweekday=sd)
            weeks = cal.monthdayscalendar(self.year, self.month)
        except (ValueError, OverflowError):
            weeks = []

        e_event = html.escape(self.event) if self.event else ""

        weeks_html = []
        for week in weeks:
            cells = []
            for day in week:
                if day == 0:
                    cells.append('<div class="dj-calendar__day dj-calendar__day--empty"></div>')
                    continue
                date_str = f"{self.year}-{self.month:02d}-{day:02d}"
                day_events = emap.get(date_str, [])

                ev_html = ""
                for i, ev in enumerate(day_events[:3]):
                    title = html.escape(str(ev.get("title", "")))
                    color = html.escape(str(ev.get("color", self.COLORS[i % len(self.COLORS)])))
                    ev_html += (
                        f'<div class="dj-calendar__event" '
                        f'style="--dj-calendar-event-color: {color}">'
                        f"{title}</div>"
                    )
                if len(day_events) > 3:
                    ev_html += f'<div class="dj-calendar__more">+{len(day_events) - 3} more</div>'

                click_attr = ""
                if e_event:
                    click_attr = f' dj-click="{e_event}" data-value="{date_str}"'

                cells.append(
                    f'<div class="dj-calendar__day" data-date="{date_str}"{click_attr}>'
                    f'<span class="dj-calendar__daynum">{day}</span>'
                    f"{ev_html}</div>"
                )
            weeks_html.append(f'<div class="dj-calendar__week">{"".join(cells)}</div>')

        grid = f'<div class="dj-calendar__grid">{"".join(weeks_html)}</div>'

        return (
            f'<div class="{class_str}" role="grid" '
            f'aria-label="{html.escape(month_name)} {html.escape(str(self.year))}">'
            f"{header}{day_names_row}{grid}</div>"
        )
