"""DatePicker component."""

import html
from djust import Component


class DatePicker(Component):
    """Server-rendered calendar date picker component.

    Args:
        name: form field name
        label: label text
        selected: selected date (YYYY-MM-DD)
        year: display year
        month: display month
        prev_event, next_event, select_event: dj-click events"""

    def __init__(
        self,
        name: str = "date",
        label: str = "",
        selected: str = "",
        year: int = 0,
        month: int = 0,
        prev_event: str = "date_prev_month",
        next_event: str = "date_next_month",
        select_event: str = "date_select",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            label=label,
            selected=selected,
            year=year,
            month=month,
            prev_event=prev_event,
            next_event=next_event,
            select_event=select_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.label = label
        self.selected = selected
        self.year = year
        self.month = month
        self.prev_event = prev_event
        self.next_event = next_event
        self.select_event = select_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the datepicker HTML."""
        import datetime
        import calendar

        today = datetime.date.today()
        year = self.year or today.year
        month = self.month or today.month
        cls = "date-picker"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_prev = html.escape(self.prev_event)
        e_next = html.escape(self.next_event)
        e_select = html.escape(self.select_event)
        e_label = html.escape(self.label)
        label_html = f'<label class="form-label">{e_label}</label>' if self.label else ""
        month_name = calendar.month_name[month]
        weekdays = "".join(
            f'<div class="dp-weekday">{d}</div>' for d in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        )
        cal = calendar.monthcalendar(year, month)
        day_cells = ""
        today_str = today.strftime("%Y-%m-%d")
        for week in cal:
            for day in week:
                if day == 0:
                    day_cells += '<div class="dp-day dp-day-empty"></div>'
                else:
                    date_str = f"{year}-{month:02d}-{day:02d}"
                    day_cls = "dp-day"
                    if date_str == today_str:
                        day_cls += " dp-day-today"
                    if date_str == self.selected:
                        day_cls += " dp-day-selected"
                    day_cells += f'<button class="{day_cls}" dj-click="{e_select}" data-value="{date_str}">{day}</button>'
        return (
            f'<div class="form-group">{label_html}'
            f'<div class="{cls}">'
            f'<div class="dp-header">'
            f'<button class="dp-nav-btn" dj-click="{e_prev}">&#8249;</button>'
            f'<span class="dp-month-label">{month_name} {year}</span>'
            f'<button class="dp-nav-btn" dj-click="{e_next}">&#8250;</button>'
            f"</div>"
            f'<div class="dp-grid">{weekdays}{day_cells}</div>'
            f"</div></div>"
        )
