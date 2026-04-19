"""
Advanced template tags — Tour, Calendar, Gantt, DiffViewer, PivotTable,
OrgChart, ComparisonTable, MasonryGrid, Collaboration, and remaining
v2.0 components.

Extracted from the monolithic djust_components.py for maintainability.
All tags register on the shared ``register`` from ``_registry``.
"""

import calendar as _calendar

from django import template

from ._registry import register, _resolve, _parse_kv_args, conditional_escape, mark_safe

# ---------------------------------------------------------------------------
# Tour / Onboarding Guide
# ---------------------------------------------------------------------------


class TourNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        steps = kw.get("steps", [])
        active = kw.get("active", 0)
        event = kw.get("event", "tour")
        show_progress = kw.get("show_progress", True)
        show_skip = kw.get("show_skip", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))

        classes = ["dj-tour"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(steps, list) or not steps:
            return ""

        try:
            idx = int(active)
        except (ValueError, TypeError):
            idx = 0
        total = len(steps)
        idx = max(0, min(idx, total - 1))

        step = steps[idx]
        if not isinstance(step, dict):
            return ""

        e_event = conditional_escape(str(event))
        e_target = conditional_escape(str(step.get("target", "")))
        e_title = conditional_escape(str(step.get("title", "")))
        e_content = conditional_escape(str(step.get("content", "")))

        progress_html = ""
        if show_progress:
            dots = []
            for i in range(total):
                dot_cls = "dj-tour__dot"
                if i == idx:
                    dot_cls += " dj-tour__dot--active"
                elif i < idx:
                    dot_cls += " dj-tour__dot--completed"
                dots.append(f'<span class="{dot_cls}"></span>')
            progress_html = f'<div class="dj-tour__progress">{"".join(dots)}</div>'

        prev_btn = ""
        if idx > 0:
            prev_btn = (
                f'<button class="dj-tour__prev" type="button" '
                f'dj-click="{e_event}" data-value="prev">Back</button>'
            )

        next_label = "Finish" if idx == total - 1 else "Next"
        next_action = "finish" if idx == total - 1 else "next"
        next_btn = (
            f'<button class="dj-tour__next" type="button" '
            f'dj-click="{e_event}" data-value="{next_action}">{next_label}</button>'
        )

        skip_btn = ""
        if show_skip and idx < total - 1:
            skip_btn = (
                f'<button class="dj-tour__skip" type="button" '
                f'dj-click="{e_event}" data-value="skip">Skip tour</button>'
            )

        step_label = f'<span class="dj-tour__step-label">Step {idx + 1} of {total}</span>'

        return mark_safe(
            f'<div class="{class_str}" dj-hook="Tour" '
            f'data-target="{e_target}" data-step="{idx}" '
            f'data-total="{total}" data-event="{e_event}" role="dialog" aria-modal="true">'
            f'<div class="dj-tour__overlay"></div>'
            f'<div class="dj-tour__popover">'
            f'<div class="dj-tour__header">'
            f'<h4 class="dj-tour__title">{e_title}</h4>'
            f"{step_label}</div>"
            f'<div class="dj-tour__body">'
            f'<p class="dj-tour__content">{e_content}</p></div>'
            f"{progress_html}"
            f'<div class="dj-tour__footer">'
            f"{skip_btn}{prev_btn}{next_btn}</div></div></div>"
        )


@register.tag("tour")
def do_tour(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return TourNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Batch 4 — Enterprise + Specialized Components
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Calendar View
# ---------------------------------------------------------------------------


class CalendarViewNode(template.Node):
    COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6"]

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        events = kw.get("events", [])
        month = kw.get("month", 1)
        year = kw.get("year", 2026)
        view = kw.get("view", "month")
        start_day = kw.get("start_day", 0)
        event = kw.get("event", "")
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-calendar"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(events, list):
            events = []
        try:
            month = int(month)
        except (ValueError, TypeError):
            month = 1
        try:
            year = int(year)
        except (ValueError, TypeError):
            year = 2026
        try:
            start_day = int(start_day) % 7
        except (ValueError, TypeError):
            start_day = 0

        # Build event map
        emap = {}
        for ev in events:
            if not isinstance(ev, dict):
                continue
            d = str(ev.get("date", ""))
            if d:
                emap.setdefault(d, []).append(ev)

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        day_names = day_names[start_day:] + day_names[:start_day]

        try:
            month_name = _calendar.month_name[month]
        except (IndexError, KeyError):
            month_name = ""
        e_month = conditional_escape(month_name)
        e_year = conditional_escape(str(year))

        header = (
            f'<div class="dj-calendar__header">'
            f'<span class="dj-calendar__title">{e_month} {e_year}</span></div>'
        )

        dn_cells = "".join(
            f'<div class="dj-calendar__dayname">{conditional_escape(d)}</div>' for d in day_names
        )
        day_names_row = f'<div class="dj-calendar__daynames">{dn_cells}</div>'

        try:
            cal = _calendar.Calendar(firstweekday=start_day)
            weeks = cal.monthdayscalendar(year, month)
        except (ValueError, OverflowError):
            weeks = []

        e_event = conditional_escape(str(event)) if event else ""

        weeks_html = []
        for week in weeks:
            cells = []
            for day in week:
                if day == 0:
                    cells.append('<div class="dj-calendar__day dj-calendar__day--empty"></div>')
                    continue
                date_str = f"{year}-{month:02d}-{day:02d}"
                day_events = emap.get(date_str, [])

                ev_html = ""
                for i, ev in enumerate(day_events[:3]):
                    title = conditional_escape(str(ev.get("title", "")))
                    color = conditional_escape(
                        str(ev.get("color", self.COLORS[i % len(self.COLORS)]))
                    )
                    ev_html += (
                        f'<div class="dj-calendar__event" '
                        f'style="--dj-calendar-event-color: {color}">{title}</div>'
                    )
                if len(day_events) > 3:
                    ev_html += f'<div class="dj-calendar__more">+{len(day_events) - 3} more</div>'

                click_attr = ""
                if e_event:
                    click_attr = f' dj-click="{e_event}" data-value="{date_str}"'

                cells.append(
                    f'<div class="dj-calendar__day" data-date="{date_str}"{click_attr}>'
                    f'<span class="dj-calendar__daynum">{day}</span>{ev_html}</div>'
                )
            weeks_html.append(f'<div class="dj-calendar__week">{"".join(cells)}</div>')

        grid = f'<div class="dj-calendar__grid">{"".join(weeks_html)}</div>'

        return mark_safe(
            f'<div class="{class_str}" role="grid" '
            f'aria-label="{e_month} {e_year}">{header}{day_names_row}{grid}</div>'
        )


@register.tag("calendar")
def do_calendar(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return CalendarViewNode(kwargs)


# ---------------------------------------------------------------------------
# Gantt Chart
# ---------------------------------------------------------------------------


class GanttChartNode(template.Node):
    COLORS = [
        "#3b82f6",
        "#ef4444",
        "#22c55e",
        "#f59e0b",
        "#8b5cf6",
        "#06b6d4",
        "#ec4899",
        "#f97316",
    ]

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        tasks = kw.get("tasks", [])
        title = kw.get("title", "")
        unit_label = kw.get("unit_label", "Day")
        units = kw.get("units", None)
        row_height = kw.get("row_height", 32)
        width = kw.get("width", 600)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-gantt"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(tasks, list):
            tasks = []
        try:
            width = int(width)
        except (ValueError, TypeError):
            width = 600
        try:
            row_height = int(row_height)
        except (ValueError, TypeError):
            row_height = 32

        if not tasks:
            return mark_safe(f'<div class="{class_str}"><svg></svg></div>')

        parsed = []
        for i, t in enumerate(tasks):
            if not isinstance(t, dict):
                continue
            name = str(t.get("name", f"Task {i + 1}"))
            try:
                start = float(t.get("start", 0))
            except (ValueError, TypeError):
                start = 0
            try:
                dur = float(t.get("duration", 1))
            except (ValueError, TypeError):
                dur = 1
            color = str(t.get("color", self.COLORS[i % len(self.COLORS)]))
            try:
                progress = max(0, min(1, float(t.get("progress", 0))))
            except (ValueError, TypeError):
                progress = 0
            parsed.append((name, start, dur, color, progress))

        if not parsed:
            return mark_safe(f'<div class="{class_str}"><svg></svg></div>')

        max_end = max(s + d for _, s, d, _, _ in parsed)
        try:
            total_units = int(units) if units is not None else int(max_end) + 1
        except (ValueError, TypeError):
            total_units = int(max_end) + 1
        if total_units <= 0:
            total_units = 1

        label_width = 120
        title_h = 24 if title else 0
        header_h = 24
        rh = row_height
        w = width
        h = title_h + header_h + len(parsed) * rh + 4
        chart_w = w - label_width
        unit_w = chart_w / total_units

        e_title = conditional_escape(str(title)) if title else "Gantt chart"
        parts = [
            f'<svg class="dj-gantt__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{e_title}">'
        ]

        if title:
            parts.append(
                f'<text class="dj-gantt__title" x="{w / 2}" y="16" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{conditional_escape(str(title))}</text>"
            )

        for u in range(total_units):
            x = label_width + u * unit_w + unit_w / 2
            y = title_h + 16
            parts.append(
                f'<text class="dj-gantt__header" x="{x:.1f}" y="{y:.1f}" '
                f'text-anchor="middle" font-size="9" fill="#6b7280">{u + 1}</text>'
            )

        for u in range(total_units + 1):
            x = label_width + u * unit_w
            y1 = title_h + header_h
            y2 = h
            parts.append(
                f'<line x1="{x:.1f}" y1="{y1}" x2="{x:.1f}" y2="{y2}" '
                f'stroke="#e5e7eb" stroke-width="0.5"/>'
            )

        for idx, (name, start, dur, color, progress) in enumerate(parsed):
            y = title_h + header_h + idx * rh
            bar_x = label_width + start * unit_w
            bar_w = dur * unit_w
            bar_y = y + 6
            bar_h = rh - 12

            e_name = conditional_escape(name)
            e_color = conditional_escape(color)
            e_unit = conditional_escape(str(unit_label))

            parts.append(
                f'<text class="dj-gantt__label" x="{label_width - 8}" '
                f'y="{y + rh / 2:.1f}" text-anchor="end" '
                f'dominant-baseline="central" font-size="11">{e_name}</text>'
            )
            parts.append(
                f'<rect class="dj-gantt__bar" x="{bar_x:.1f}" y="{bar_y:.1f}" '
                f'width="{bar_w:.1f}" height="{bar_h:.1f}" rx="3" '
                f'fill="{e_color}" opacity="0.25">'
                f"<title>{e_name}: {e_unit} {start:.0f}-{start + dur:.0f}</title></rect>"
            )
            if progress > 0:
                pw = bar_w * progress
                parts.append(
                    f'<rect class="dj-gantt__progress" x="{bar_x:.1f}" y="{bar_y:.1f}" '
                    f'width="{pw:.1f}" height="{bar_h:.1f}" rx="3" '
                    f'fill="{e_color}"/>'
                )

        parts.append("</svg>")
        return mark_safe(f'<div class="{class_str}">{"".join(parts)}</div>')


@register.tag("gantt_chart")
def do_gantt_chart(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return GanttChartNode(kwargs)


# ---------------------------------------------------------------------------
# Diff Viewer
# ---------------------------------------------------------------------------


class DiffViewerNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    @staticmethod
    def _compute_diff(old_lines, new_lines):
        m, n = len(old_lines), len(new_lines)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if old_lines[i - 1] == new_lines[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
        result = []
        i, j = m, n
        while i > 0 or j > 0:
            if i > 0 and j > 0 and old_lines[i - 1] == new_lines[j - 1]:
                result.append(("equal", old_lines[i - 1], new_lines[j - 1]))
                i -= 1
                j -= 1
            elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
                result.append(("insert", None, new_lines[j - 1]))
                j -= 1
            else:
                result.append(("delete", old_lines[i - 1], None))
                i -= 1
        result.reverse()
        return result

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        old = str(kw.get("old", ""))
        new = str(kw.get("new", ""))
        mode = kw.get("mode", "split")
        title_old = kw.get("title_old", "Original")
        title_new = kw.get("title_new", "Modified")
        show_line_numbers = kw.get("show_line_numbers", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-diff"]
        if mode == "unified":
            classes.append("dj-diff--unified")
        else:
            classes.append("dj-diff--split")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        old_lines = old.split("\n") if old else []
        new_lines = new.split("\n") if new else []
        ops = self._compute_diff(old_lines, new_lines)

        if mode == "unified":
            return mark_safe(self._render_unified(class_str, ops, show_line_numbers))
        return mark_safe(
            self._render_split(class_str, ops, title_old, title_new, show_line_numbers)
        )

    def _render_split(self, class_str, ops, title_old, title_new, show_ln):
        e_title_old = conditional_escape(str(title_old))
        e_title_new = conditional_escape(str(title_new))

        old_rows = []
        new_rows = []
        old_num = 0
        new_num = 0

        for tag, old_line, new_line in ops:
            if tag == "equal":
                old_num += 1
                new_num += 1
                num_o = f'<span class="dj-diff__num">{old_num}</span>' if show_ln else ""
                num_n = f'<span class="dj-diff__num">{new_num}</span>' if show_ln else ""
                old_rows.append(
                    f'<div class="dj-diff__line">{num_o}'
                    f'<span class="dj-diff__text">{conditional_escape(old_line)}</span></div>'
                )
                new_rows.append(
                    f'<div class="dj-diff__line">{num_n}'
                    f'<span class="dj-diff__text">{conditional_escape(new_line)}</span></div>'
                )
            elif tag == "delete":
                old_num += 1
                num_html = f'<span class="dj-diff__num">{old_num}</span>' if show_ln else ""
                old_rows.append(
                    f'<div class="dj-diff__line dj-diff__line--del">{num_html}'
                    f'<span class="dj-diff__text">{conditional_escape(old_line)}</span></div>'
                )
                new_rows.append('<div class="dj-diff__line dj-diff__line--empty"></div>')
            elif tag == "insert":
                new_num += 1
                num_html = f'<span class="dj-diff__num">{new_num}</span>' if show_ln else ""
                old_rows.append('<div class="dj-diff__line dj-diff__line--empty"></div>')
                new_rows.append(
                    f'<div class="dj-diff__line dj-diff__line--add">{num_html}'
                    f'<span class="dj-diff__text">{conditional_escape(new_line)}</span></div>'
                )

        return (
            f'<div class="{class_str}">'
            f'<div class="dj-diff__pane dj-diff__pane--old">'
            f'<div class="dj-diff__pane-header">{e_title_old}</div>'
            f"{''.join(old_rows)}</div>"
            f'<div class="dj-diff__pane dj-diff__pane--new">'
            f'<div class="dj-diff__pane-header">{e_title_new}</div>'
            f"{''.join(new_rows)}</div></div>"
        )

    def _render_unified(self, class_str, ops, show_ln):
        rows = []
        old_num = 0
        new_num = 0

        for tag, old_line, new_line in ops:
            if tag == "equal":
                old_num += 1
                new_num += 1
                num_html = ""
                if show_ln:
                    num_html = (
                        f'<span class="dj-diff__num">{old_num}</span>'
                        f'<span class="dj-diff__num">{new_num}</span>'
                    )
                rows.append(
                    f'<div class="dj-diff__line">{num_html}'
                    f'<span class="dj-diff__marker"> </span>'
                    f'<span class="dj-diff__text">{conditional_escape(old_line)}</span></div>'
                )
            elif tag == "delete":
                old_num += 1
                num_html = ""
                if show_ln:
                    num_html = (
                        f'<span class="dj-diff__num">{old_num}</span>'
                        f'<span class="dj-diff__num"></span>'
                    )
                rows.append(
                    f'<div class="dj-diff__line dj-diff__line--del">{num_html}'
                    f'<span class="dj-diff__marker">-</span>'
                    f'<span class="dj-diff__text">{conditional_escape(old_line)}</span></div>'
                )
            elif tag == "insert":
                new_num += 1
                num_html = ""
                if show_ln:
                    num_html = (
                        f'<span class="dj-diff__num"></span>'
                        f'<span class="dj-diff__num">{new_num}</span>'
                    )
                rows.append(
                    f'<div class="dj-diff__line dj-diff__line--add">{num_html}'
                    f'<span class="dj-diff__marker">+</span>'
                    f'<span class="dj-diff__text">{conditional_escape(new_line)}</span></div>'
                )

        return f'<div class="{class_str}"><div class="dj-diff__unified">{"".join(rows)}</div></div>'


@register.tag("diff_viewer")
def do_diff_viewer(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return DiffViewerNode(kwargs)


# ---------------------------------------------------------------------------
# Pivot Table
# ---------------------------------------------------------------------------


class PivotTableNode(template.Node):
    AGG_FUNCS = {
        "sum": lambda vals: sum(vals),
        "avg": lambda vals: sum(vals) / len(vals) if vals else 0,
        "count": lambda vals: len(vals),
        "min": lambda vals: min(vals) if vals else 0,
        "max": lambda vals: max(vals) if vals else 0,
    }

    def __init__(self, kwargs):
        self.kwargs = kwargs

    @staticmethod
    def _format_val(v):
        if v == int(v):
            return str(int(v))
        return f"{v:.2f}"

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        data = kw.get("data", [])
        rows_field = str(kw.get("rows", ""))
        cols_field = str(kw.get("cols", ""))
        values_field = str(kw.get("values", ""))
        agg = kw.get("agg", "sum")
        title = kw.get("title", "")
        show_totals = kw.get("show_totals", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-pivot"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(data, list):
            data = []
        if agg not in self.AGG_FUNCS:
            agg = "sum"
        agg_fn = self.AGG_FUNCS[agg]

        if not data or not rows_field or not cols_field or not values_field:
            return mark_safe(
                f'<div class="{class_str}"><table class="dj-pivot__table"></table></div>'
            )

        # Build pivot
        row_keys = []
        col_keys = []
        cells = {}
        for record in data:
            if not isinstance(record, dict):
                continue
            rk = str(record.get(rows_field, ""))
            ck = str(record.get(cols_field, ""))
            try:
                val = float(record.get(values_field, 0))
            except (ValueError, TypeError):
                val = 0
            if rk not in row_keys:
                row_keys.append(rk)
            if ck not in col_keys:
                col_keys.append(ck)
            cells.setdefault((rk, ck), []).append(val)

        agg_cells = {k: agg_fn(v) for k, v in cells.items()}

        parts = []
        if title:
            parts.append(
                f'<caption class="dj-pivot__title">{conditional_escape(str(title))}</caption>'
            )

        header_cells = [f'<th class="dj-pivot__corner">{conditional_escape(rows_field)}</th>']
        for ck in col_keys:
            header_cells.append(f'<th class="dj-pivot__colheader">{conditional_escape(ck)}</th>')
        if show_totals:
            header_cells.append('<th class="dj-pivot__colheader dj-pivot__total-header">Total</th>')
        parts.append(f"<thead><tr>{''.join(header_cells)}</tr></thead>")

        body_rows = []
        col_totals = {ck: 0 for ck in col_keys}
        grand_total = 0

        for rk in row_keys:
            row_cells = [f'<th class="dj-pivot__rowheader">{conditional_escape(rk)}</th>']
            row_total = 0
            for ck in col_keys:
                val = agg_cells.get((rk, ck), 0)
                row_total += val
                col_totals[ck] += val
                row_cells.append(f'<td class="dj-pivot__cell">{self._format_val(val)}</td>')
            if show_totals:
                grand_total += row_total
                row_cells.append(
                    f'<td class="dj-pivot__cell dj-pivot__row-total">{self._format_val(row_total)}</td>'
                )
            body_rows.append(f"<tr>{''.join(row_cells)}</tr>")

        parts.append(f"<tbody>{''.join(body_rows)}</tbody>")

        if show_totals:
            foot_cells = ['<th class="dj-pivot__rowheader">Total</th>']
            for ck in col_keys:
                foot_cells.append(
                    f'<td class="dj-pivot__cell dj-pivot__col-total">{self._format_val(col_totals[ck])}</td>'
                )
            foot_cells.append(
                f'<td class="dj-pivot__cell dj-pivot__grand-total">{self._format_val(grand_total)}</td>'
            )
            parts.append(f"<tfoot><tr>{''.join(foot_cells)}</tr></tfoot>")

        return mark_safe(
            f'<div class="{class_str}">'
            f'<table class="dj-pivot__table" role="grid">{"".join(parts)}</table></div>'
        )


@register.tag("pivot_table")
def do_pivot_table(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return PivotTableNode(kwargs)


# ---------------------------------------------------------------------------
# Org Chart
# ---------------------------------------------------------------------------


class OrgChartNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def _render_node(self, nid, node_map, children, e_event):
        node = node_map.get(nid)
        if not node:
            return ""
        name = conditional_escape(str(node.get("name", "")))
        title = conditional_escape(str(node.get("title", "")))
        avatar = node.get("avatar", "")

        click_attr = ""
        if e_event:
            click_attr = f' dj-click="{e_event}" data-value="{conditional_escape(nid)}"'

        if avatar:
            avatar_html = (
                f'<img class="dj-org__avatar" src="{conditional_escape(str(avatar))}" '
                f'alt="{name}" />'
            )
        else:
            initials = "".join(w[0] for w in str(node.get("name", "")).split()[:2]).upper() or "?"
            avatar_html = f'<span class="dj-org__initials">{conditional_escape(initials)}</span>'

        node_html = (
            f'<div class="dj-org__card" data-id="{conditional_escape(nid)}"{click_attr}>'
            f"{avatar_html}"
            f'<div class="dj-org__info">'
            f'<span class="dj-org__name">{name}</span>'
            f'<span class="dj-org__title">{title}</span>'
            f"</div></div>"
        )

        child_ids = children.get(nid, [])
        if not child_ids:
            return f'<li class="dj-org__node">{node_html}</li>'

        child_items = "".join(
            self._render_node(cid, node_map, children, e_event) for cid in child_ids
        )
        return (
            f'<li class="dj-org__node">{node_html}'
            f'<ul class="dj-org__children">{child_items}</ul></li>'
        )

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        nodes = kw.get("nodes", [])
        root = kw.get("root", "")
        event = kw.get("event", "")
        direction = kw.get("direction", "vertical")
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-org"]
        if direction == "horizontal":
            classes.append("dj-org--horizontal")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(nodes, list):
            nodes = []

        node_map = {}
        children = {}
        child_ids = set()

        for n in nodes:
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id", ""))
            if not nid:
                continue
            node_map[nid] = n
            parent = n.get("parent", "")
            if parent:
                parent = str(parent)
                children.setdefault(parent, []).append(nid)
                child_ids.add(nid)

        root_id = str(root) if root else ""
        if not root_id:
            roots = [nid for nid in node_map if nid not in child_ids]
            root_id = roots[0] if roots else (list(node_map.keys())[0] if node_map else "")

        if not node_map or not root_id:
            return mark_safe(f'<div class="{class_str}" role="tree"></div>')

        e_event = conditional_escape(str(event)) if event else ""
        tree_html = self._render_node(root_id, node_map, children, e_event)

        return mark_safe(
            f'<div class="{class_str}" role="tree"><ul class="dj-org__root">{tree_html}</ul></div>'
        )


@register.tag("org_chart")
def do_org_chart(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return OrgChartNode(kwargs)


# ---------------------------------------------------------------------------
# Comparison Table
# ---------------------------------------------------------------------------


class ComparisonTableNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    @staticmethod
    def _render_value(val):
        if val is True:
            return '<span class="dj-compare__check" aria-label="Yes">&#10003;</span>'
        if val is False:
            return '<span class="dj-compare__cross" aria-label="No">&#10007;</span>'
        return conditional_escape(str(val))

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        plans = kw.get("plans", [])
        features = kw.get("features", [])
        event = kw.get("event", "")
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-compare"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(plans, list):
            plans = []
        if not isinstance(features, list):
            features = []

        if not plans:
            return mark_safe(
                f'<div class="{class_str}"><table class="dj-compare__table"></table></div>'
            )

        e_event = conditional_escape(str(event)) if event else ""
        num_plans = len(plans)

        header_cells = ['<th class="dj-compare__corner"></th>']
        for i, plan in enumerate(plans):
            if not isinstance(plan, dict):
                continue
            name = conditional_escape(str(plan.get("name", "")))
            price = conditional_escape(str(plan.get("price", "")))
            highlighted = plan.get("highlighted", False)
            hl_class = " dj-compare__plan--highlighted" if highlighted else ""

            click_attr = ""
            if e_event:
                click_attr = f' dj-click="{e_event}" data-value="{name}"'

            price_html = f'<div class="dj-compare__price">{price}</div>' if price else ""
            header_cells.append(
                f'<th class="dj-compare__plan{hl_class}"{click_attr}>'
                f'<div class="dj-compare__plan-name">{name}</div>'
                f"{price_html}</th>"
            )
        header = f"<thead><tr>{''.join(header_cells)}</tr></thead>"

        body_rows = []
        for feat in features:
            if not isinstance(feat, dict):
                continue
            fname = conditional_escape(str(feat.get("name", "")))
            values = feat.get("values", [])
            if not isinstance(values, list):
                values = []
            cells = [f'<th class="dj-compare__feature">{fname}</th>']
            for i in range(num_plans):
                val = values[i] if i < len(values) else ""
                highlighted = False
                if i < len(plans) and isinstance(plans[i], dict):
                    highlighted = plans[i].get("highlighted", False)
                hl_class = " dj-compare__cell--highlighted" if highlighted else ""
                cells.append(
                    f'<td class="dj-compare__cell{hl_class}">{self._render_value(val)}</td>'
                )
            body_rows.append(f"<tr>{''.join(cells)}</tr>")

        body = f"<tbody>{''.join(body_rows)}</tbody>"

        return mark_safe(
            f'<div class="{class_str}">'
            f'<table class="dj-compare__table" role="grid">{header}{body}</table></div>'
        )


@register.tag("comparison_table")
def do_comparison_table(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ComparisonTableNode(kwargs)


# ---------------------------------------------------------------------------
# Masonry Grid
# ---------------------------------------------------------------------------


class MasonryGridNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        items = kw.get("items", [])
        columns = kw.get("columns", 3)
        gap = kw.get("gap", 16)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-masonry"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(items, list):
            items = []
        try:
            columns = max(1, int(columns))
        except (ValueError, TypeError):
            columns = 3
        try:
            gap = int(gap)
        except (ValueError, TypeError):
            gap = 16

        if not items:
            return mark_safe(f'<div class="{class_str}"></div>')

        col_heights = [0] * columns
        col_items = [[] for _ in range(columns)]

        for item in items:
            if not isinstance(item, dict):
                continue
            min_col = col_heights.index(min(col_heights))
            col_items[min_col].append(item)
            try:
                h = int(item.get("height", 100))
            except (ValueError, TypeError):
                h = 100
            col_heights[min_col] += h + gap

        col_html = []
        for col_idx, items_in_col in enumerate(col_items):
            item_cards = []
            for item in items_in_col:
                content = str(item.get("content", ""))
                item_class = conditional_escape(str(item.get("class", "")))
                extra = f" {item_class}" if item_class else ""
                item_cards.append(f'<div class="dj-masonry__item{extra}">{content}</div>')
            col_html.append(f'<div class="dj-masonry__col">{"".join(item_cards)}</div>')

        style = f"--dj-masonry-columns: {columns}; --dj-masonry-gap: {gap}px"

        return mark_safe(
            f'<div class="{class_str}" style="{style}" role="list">{"".join(col_html)}</div>'
        )


@register.tag("masonry_grid")
def do_masonry_grid(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return MasonryGridNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Batch 5: Collaboration Suite — Cursors Overlay (#77)
# ---------------------------------------------------------------------------


class CursorsOverlayNode(template.Node):
    DEFAULT_COLORS = [
        "#3b82f6",
        "#ef4444",
        "#22c55e",
        "#f59e0b",
        "#8b5cf6",
        "#ec4899",
        "#06b6d4",
        "#f97316",
    ]

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        users = kw.get("users", [])
        custom_class = kw.get("class", "")

        if not isinstance(users, list):
            users = []

        e_class = conditional_escape(str(custom_class))

        parts = []
        for i, user in enumerate(users):
            if isinstance(user, dict):
                name = user.get("name", "")
                color = user.get("color", self.DEFAULT_COLORS[i % len(self.DEFAULT_COLORS)])
                x = user.get("x", 0)
                y = user.get("y", 0)
            else:
                name = str(user)
                color = self.DEFAULT_COLORS[i % len(self.DEFAULT_COLORS)]
                x = 0
                y = 0

            e_name = conditional_escape(str(name))
            e_color = conditional_escape(str(color))

            try:
                px = int(x)
            except (ValueError, TypeError):
                px = 0
            try:
                py = int(y)
            except (ValueError, TypeError):
                py = 0

            cursor_svg = (
                f'<svg class="dj-cursors__arrow" width="16" height="20" viewBox="0 0 16 20" '
                f'fill="{e_color}">'
                f'<path d="M0 0L16 12L8 12L12 20L8 18L4 12L0 16Z"/>'
                f"</svg>"
            )

            parts.append(
                f'<div class="dj-cursors__cursor" '
                f'style="left:{px}px;top:{py}px" '
                f'data-user="{e_name}">'
                f"{cursor_svg}"
                f'<span class="dj-cursors__label" '
                f'style="background:{e_color}">{e_name}</span>'
                f"</div>"
            )

        cls = "dj-cursors"
        if e_class:
            cls += f" {e_class}"

        total = len(users)
        label = f"{total} cursor{'s' if total != 1 else ''}"

        return mark_safe(
            f'<div class="{cls}" role="group" aria-label="{label}" '
            f'dj-hook="CursorsOverlay">'
            f"{''.join(parts)}"
            f"</div>"
        )


@register.tag("cursors")
def do_cursors(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return CursorsOverlayNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Batch 5: Collaboration Suite — Live Indicator (#78)
# ---------------------------------------------------------------------------


class LiveIndicatorNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        user = kw.get("user", None)
        field = kw.get("field", "")
        action = kw.get("action", "typing")
        active = kw.get("active", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))

        if not active or not user:
            cls = "dj-live-indicator dj-live-indicator--hidden"
            if e_class:
                cls += f" {e_class}"
            return mark_safe(f'<div class="{cls}"></div>')

        if isinstance(user, dict):
            name = user.get("name", "")
            avatar = user.get("avatar", "")
        else:
            name = str(user)
            avatar = ""

        e_name = conditional_escape(str(name))
        e_avatar = conditional_escape(str(avatar))
        e_field = conditional_escape(str(field))
        e_action = conditional_escape(str(action))

        cls = "dj-live-indicator"
        if e_class:
            cls += f" {e_class}"

        avatar_html = ""
        if e_avatar:
            avatar_html = f'<img src="{e_avatar}" alt="{e_name}" class="dj-live-indicator__avatar">'

        dots = (
            '<span class="dj-live-indicator__dots">'
            '<span class="dj-live-indicator__dot"></span>'
            '<span class="dj-live-indicator__dot"></span>'
            '<span class="dj-live-indicator__dot"></span>'
            "</span>"
        )

        field_attr = f' data-field="{e_field}"' if e_field else ""

        return mark_safe(
            f'<div class="{cls}"{field_attr} role="status" aria-live="polite">'
            f"{avatar_html}"
            f'<span class="dj-live-indicator__text">'
            f"{e_name} is {e_action}{dots}</span>"
            f"</div>"
        )


@register.tag("live_indicator")
def do_live_indicator(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return LiveIndicatorNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Batch 5: Collaboration Suite — Collaborative Selection (#79)
# ---------------------------------------------------------------------------


class CollabSelectionNode(template.Node):
    DEFAULT_COLORS = [
        "#3b82f6",
        "#ef4444",
        "#22c55e",
        "#f59e0b",
        "#8b5cf6",
        "#ec4899",
        "#06b6d4",
        "#f97316",
    ]

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        users = kw.get("users", [])
        custom_class = kw.get("class", "")

        if not isinstance(users, list):
            users = []

        e_class = conditional_escape(str(custom_class))

        parts = []
        for i, user in enumerate(users):
            if isinstance(user, dict):
                name = user.get("name", "")
                color = user.get("color", self.DEFAULT_COLORS[i % len(self.DEFAULT_COLORS)])
                text = user.get("text", "")
                start = user.get("start", 0)
                end = user.get("end", 0)
            else:
                name = str(user)
                color = self.DEFAULT_COLORS[i % len(self.DEFAULT_COLORS)]
                text = ""
                start = 0
                end = 0

            e_name = conditional_escape(str(name))
            e_color = conditional_escape(str(color))
            e_text = conditional_escape(str(text))

            try:
                s = int(start)
            except (ValueError, TypeError):
                s = 0
            try:
                e = int(end)
            except (ValueError, TypeError):
                e = 0

            parts.append(
                f'<span class="dj-collab-sel__range" '
                f'style="--dj-collab-sel-color:{e_color}" '
                f'data-user="{e_name}" data-start="{s}" data-end="{e}">'
                f'<span class="dj-collab-sel__highlight">{e_text}</span>'
                f'<span class="dj-collab-sel__label" '
                f'style="background:{e_color}">{e_name}</span>'
                f"</span>"
            )

        cls = "dj-collab-sel"
        if e_class:
            cls += f" {e_class}"

        total = len(users)
        label = f"{total} selection{'s' if total != 1 else ''}"

        return mark_safe(
            f'<div class="{cls}" role="group" aria-label="{label}" '
            f'dj-hook="CollabSelection">'
            f"{''.join(parts)}"
            f"</div>"
        )


@register.tag("collab_selection")
def do_collab_selection(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return CollabSelectionNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Batch 5: Collaboration Suite — Activity Feed (#80)
# ---------------------------------------------------------------------------


class ActivityFeedNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        events = kw.get("events", [])
        stream_event = kw.get("stream", "")
        max_items = int(kw.get("max", 50))
        custom_class = kw.get("class", "")

        if not isinstance(events, list):
            events = []

        e_class = conditional_escape(str(custom_class))
        e_stream = conditional_escape(str(stream_event))

        cls = "dj-activity-feed"
        if e_class:
            cls += f" {e_class}"

        attrs = [f'class="{cls}"', 'role="feed"', 'aria-label="Activity feed"']
        if e_stream:
            attrs.append(f'data-stream-event="{e_stream}"')
            attrs.append('dj-hook="ActivityFeed"')

        visible = events[:max_items]

        items = []
        for event in visible:
            if not isinstance(event, dict):
                continue

            user = conditional_escape(str(event.get("user", "")))
            action = conditional_escape(str(event.get("action", "")))
            target = conditional_escape(str(event.get("target", "")))
            time = conditional_escape(str(event.get("time", "")))
            avatar_src = conditional_escape(str(event.get("avatar", "")))
            icon = conditional_escape(str(event.get("icon", "")))

            initials = (
                conditional_escape(
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
        return mark_safe(f"<div {attrs_str}>{''.join(items)}</div>")


@register.tag("activity_feed")
def do_activity_feed(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ActivityFeedNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Batch 5: Collaboration Suite — Reactions (#81)
# ---------------------------------------------------------------------------


class ReactionsNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        options = kw.get("options", [])
        counts = kw.get("counts", {})
        event = kw.get("event", "react")
        active = kw.get("active", [])
        custom_class = kw.get("class", "")

        if not isinstance(options, list):
            options = []
        if not isinstance(counts, dict):
            counts = {}
        if not isinstance(active, list):
            active = []

        e_class = conditional_escape(str(custom_class))
        e_event = conditional_escape(str(event))

        cls = "dj-reactions"
        if e_class:
            cls += f" {e_class}"

        buttons = []
        for emoji in options:
            e_emoji = conditional_escape(str(emoji))

            count = 0
            try:
                count = int(counts.get(str(emoji), 0))
            except (ValueError, TypeError):
                count = 0

            is_active = str(emoji) in active
            btn_cls = "dj-reactions__btn"
            if is_active:
                btn_cls += " dj-reactions__btn--active"

            aria_pressed = "true" if is_active else "false"

            count_html = ""
            if count > 0:
                count_html = f'<span class="dj-reactions__count">{count}</span>'

            buttons.append(
                f'<button type="button" class="{btn_cls}" '
                f'dj-click="{e_event}" dj-value-emoji="{e_emoji}" '
                f'aria-pressed="{aria_pressed}" '
                f'aria-label="{e_emoji} {count}">'
                f'<span class="dj-reactions__emoji">{e_emoji}</span>'
                f"{count_html}"
                f"</button>"
            )

        return mark_safe(
            f'<div class="{cls}" role="group" aria-label="Reactions">{"".join(buttons)}</div>'
        )


@register.tag("reactions")
def do_reactions(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ReactionsNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Map Picker (#76b)
# ---------------------------------------------------------------------------


class MapPickerNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        lat = kw.get("lat", 0)
        lng = kw.get("lng", 0)
        pick_event = kw.get("pick_event", "set_location")
        zoom = kw.get("zoom", 13)
        height = kw.get("height", "400px")
        custom_class = kw.get("class", "")

        try:
            lat = float(lat)
        except (ValueError, TypeError):
            lat = 0.0
        try:
            lng = float(lng)
        except (ValueError, TypeError):
            lng = 0.0
        try:
            zoom = int(zoom)
        except (ValueError, TypeError):
            zoom = 13

        e_class = conditional_escape(str(custom_class))
        e_event = conditional_escape(str(pick_event))
        e_height = conditional_escape(str(height))

        cls = "dj-map-picker"
        if e_class:
            cls += f" {e_class}"

        return mark_safe(
            f'<div class="{cls}" dj-hook="MapPicker" '
            f'data-lat="{lat}" data-lng="{lng}" '
            f'data-zoom="{zoom}" data-pick-event="{e_event}" '
            f'style="height:{e_height}" '
            f'role="application" aria-label="Map picker">'
            f'<div class="dj-map-picker__map"></div>'
            f"</div>"
        )


@register.tag("map_picker")
def do_map_picker(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return MapPickerNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Prompt Template Editor (#158)
# ---------------------------------------------------------------------------


class PromptEditorNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        import re as _re

        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        tmpl = kw.get("template", "")
        variables = kw.get("variables", {})
        event = kw.get("event", "save_prompt")
        placeholder = kw.get("placeholder", "Enter your prompt template...")
        rows = kw.get("rows", 6)
        custom_class = kw.get("class", "")

        if not isinstance(variables, dict):
            variables = {}

        try:
            rows = int(rows)
        except (ValueError, TypeError):
            rows = 6

        e_class = conditional_escape(str(custom_class))
        e_event = conditional_escape(str(event))
        e_placeholder = conditional_escape(str(placeholder))
        e_template = conditional_escape(str(tmpl))

        cls = "dj-prompt-editor"
        if e_class:
            cls += f" {e_class}"

        var_names = _re.findall(r"\{\{(\w+)\}\}", str(tmpl))
        unique_vars = list(dict.fromkeys(var_names))

        var_chips = []
        for v in unique_vars:
            e_v = conditional_escape(v)
            val = variables.get(v, "")
            e_val = conditional_escape(str(val))
            var_chips.append(
                f'<span class="dj-prompt-editor__var" data-var="{e_v}">'
                f"<code>{{{{{e_v}}}}}</code>"
                f"{f' = {e_val}' if val else ''}"
                f"</span>"
            )

        vars_html = ""
        if var_chips:
            vars_html = f'<div class="dj-prompt-editor__vars">{"".join(var_chips)}</div>'

        # Escape the template text first, then substitute variables
        preview_text = conditional_escape(str(tmpl))
        for v in unique_vars:
            val = variables.get(v, "{{" + v + "}}")
            # The escaped form of {{var}} is {{var}} (no special chars)
            preview_text = str(preview_text).replace(
                "{{" + v + "}}",
                f'<mark class="dj-prompt-editor__highlight">{conditional_escape(str(val))}</mark>',
            )

        event_attr = ""
        if e_event:
            event_attr = f' dj-click="{e_event}"'

        return mark_safe(
            f'<div class="{cls}">'
            f'<textarea class="dj-prompt-editor__textarea" '
            f'name="template" rows="{rows}" '
            f'placeholder="{e_placeholder}">{e_template}</textarea>'
            f"{vars_html}"
            f'<div class="dj-prompt-editor__preview">{preview_text}</div>'
            f'<button type="button" class="dj-prompt-editor__save"'
            f"{event_attr}>Save</button>"
            f"</div>"
        )


@register.tag("prompt_editor")
def do_prompt_editor(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return PromptEditorNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Voice Input Button (#164)
# ---------------------------------------------------------------------------


class VoiceInputNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        event = kw.get("event", "transcribe")
        lang = kw.get("lang", "en-US")
        continuous = kw.get("continuous", False)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        e_event = conditional_escape(str(event))
        e_lang = conditional_escape(str(lang))

        cls = "dj-voice-input"
        if e_class:
            cls += f" {e_class}"

        mic_svg = (
            '<svg class="dj-voice-input__icon" viewBox="0 0 24 24" '
            'width="20" height="20" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>'
            '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/>'
            '<line x1="12" y1="19" x2="12" y2="23"/>'
            '<line x1="8" y1="23" x2="16" y2="23"/>'
            "</svg>"
        )

        cont = "true" if continuous else "false"

        return mark_safe(
            f'<button type="button" class="{cls}" '
            f'dj-hook="VoiceInput" '
            f'data-event="{e_event}" data-lang="{e_lang}" '
            f'data-continuous="{cont}" '
            f'aria-label="Voice input" aria-pressed="false">'
            f"{mic_svg}"
            f'<span class="dj-voice-input__pulse"></span>'
            f"</button>"
        )


@register.tag("voice_input")
def do_voice_input(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return VoiceInputNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Cron Expression Input (#145)
# ---------------------------------------------------------------------------


class CronInputNode(template.Node):
    FIELD_LABELS = ["Minute", "Hour", "Day", "Month", "Weekday"]

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        name = kw.get("name", "cron")
        value = kw.get("value", "* * * * *")
        event = kw.get("event", "")
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        e_name = conditional_escape(str(name))
        e_event = conditional_escape(str(event))
        e_value = conditional_escape(str(value))

        cls = "dj-cron-input"
        if e_class:
            cls += f" {e_class}"

        parts = str(value).split()
        while len(parts) < 5:
            parts.append("*")
        parts = parts[:5]

        fields = []
        for i, (label, val) in enumerate(zip(self.FIELD_LABELS, parts)):
            e_label = conditional_escape(label)
            e_val = conditional_escape(val)
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

        return mark_safe(
            f'<div class="{cls}"{event_attr}>'
            f'<input type="hidden" name="{e_name}" value="{e_value}">'
            f'<div class="dj-cron-input__fields">{"".join(fields)}</div>'
            f'<div class="dj-cron-input__preview">'
            f"<code>{e_value}</code></div>"
            f"</div>"
        )


@register.tag("cron_input")
def do_cron_input(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return CronInputNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Error Page (#136)
# ---------------------------------------------------------------------------


class ErrorPageNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        code = kw.get("code", 500)
        title = kw.get("title", "Something went wrong")
        message = kw.get("message", "")
        action_url = kw.get("action_url", "/")
        action_label = kw.get("action_label", "Go Home")
        custom_class = kw.get("class", "")

        try:
            code = int(code)
        except (ValueError, TypeError):
            code = 500

        e_class = conditional_escape(str(custom_class))
        e_title = conditional_escape(str(title))
        e_message = conditional_escape(str(message))
        e_url = conditional_escape(str(action_url))
        e_label = conditional_escape(str(action_label))

        cls = "dj-error-page"
        if e_class:
            cls += f" {e_class}"

        msg_html = ""
        if e_message:
            msg_html = f'<p class="dj-error-page__message">{e_message}</p>'

        action_html = ""
        if e_url:
            action_html = f'<a href="{e_url}" class="dj-error-page__action">{e_label}</a>'

        return mark_safe(
            f'<div class="{cls}" role="alert">'
            f'<div class="dj-error-page__code">{code}</div>'
            f'<h1 class="dj-error-page__title">{e_title}</h1>'
            f"{msg_html}"
            f"{action_html}"
            f"</div>"
        )


@register.tag("error_page")
def do_error_page(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ErrorPageNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Image Upload Preview (#137)
# ---------------------------------------------------------------------------


class ImageUploadPreviewNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        name = kw.get("name", "images")
        max_count = kw.get("max", 5)
        event = kw.get("event", "upload")
        accept = kw.get("accept", "image/*")
        previews = kw.get("previews", [])
        custom_class = kw.get("class", "")

        try:
            max_count = int(max_count)
        except (ValueError, TypeError):
            max_count = 5

        if not isinstance(previews, list):
            previews = []

        e_class = conditional_escape(str(custom_class))
        e_name = conditional_escape(str(name))
        e_event = conditional_escape(str(event))
        e_accept = conditional_escape(str(accept))

        cls = "dj-img-upload"
        if e_class:
            cls += f" {e_class}"

        thumbs = []
        for url in previews:
            e_url = conditional_escape(str(url))
            thumbs.append(
                f'<div class="dj-img-upload__thumb">'
                f'<img src="{e_url}" alt="Preview" '
                f'class="dj-img-upload__thumb-img">'
                f"</div>"
            )

        thumbs_html = ""
        if thumbs:
            thumbs_html = f'<div class="dj-img-upload__previews">{"".join(thumbs)}</div>'

        upload_svg = (
            '<svg class="dj-img-upload__icon" viewBox="0 0 24 24" width="24" '
            'height="24" fill="none" stroke="currentColor" stroke-width="2">'
            '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
            '<polyline points="17 8 12 3 7 8"/>'
            '<line x1="12" y1="3" x2="12" y2="15"/>'
            "</svg>"
        )

        return mark_safe(
            f'<div class="{cls}" dj-hook="ImageUploadPreview" '
            f'data-event="{e_event}" data-max="{max_count}">'
            f'<label class="dj-img-upload__dropzone">'
            f"{upload_svg}"
            f'<span class="dj-img-upload__text">Drop images here or click to upload</span>'
            f'<span class="dj-img-upload__hint">Max {max_count} images</span>'
            f'<input type="file" name="{e_name}" accept="{e_accept}" '
            f'multiple class="dj-img-upload__input" aria-label="Upload images">'
            f"</label>"
            f"{thumbs_html}"
            f"</div>"
        )


@register.tag("image_upload_preview")
def do_image_upload_preview(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ImageUploadPreviewNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Number Animation (#141)
# ---------------------------------------------------------------------------


class AnimatedNumberNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        value = kw.get("value", 0)
        prefix = kw.get("prefix", "")
        suffix = kw.get("suffix", "")
        duration = kw.get("duration", 800)
        decimals = kw.get("decimals", 0)
        separator = kw.get("separator", ",")
        custom_class = kw.get("class", "")

        try:
            val = float(value)
        except (ValueError, TypeError):
            val = 0
        try:
            duration = int(duration)
        except (ValueError, TypeError):
            duration = 800
        try:
            decimals = int(decimals)
        except (ValueError, TypeError):
            decimals = 0

        e_class = conditional_escape(str(custom_class))
        e_prefix = conditional_escape(str(prefix))
        e_suffix = conditional_escape(str(suffix))
        e_sep = conditional_escape(str(separator))

        cls = "dj-animated-number"
        if e_class:
            cls += f" {e_class}"

        # Format number
        if decimals > 0:
            formatted = f"{val:,.{decimals}f}"
        else:
            formatted = f"{int(val):,}"
        if separator != ",":
            formatted = formatted.replace(",", separator)
        e_formatted = conditional_escape(formatted)

        prefix_html = ""
        if e_prefix:
            prefix_html = f'<span class="dj-animated-number__prefix">{e_prefix}</span>'
        suffix_html = ""
        if e_suffix:
            suffix_html = f'<span class="dj-animated-number__suffix">{e_suffix}</span>'

        return mark_safe(
            f'<span class="{cls}" dj-hook="AnimatedNumber" '
            f'data-value="{val}" data-duration="{duration}" '
            f'data-decimals="{decimals}" data-separator="{e_sep}">'
            f"{prefix_html}"
            f'<span class="dj-animated-number__value">{e_formatted}</span>'
            f"{suffix_html}"
            f"</span>"
        )


@register.tag("animated_number")
def do_animated_number(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return AnimatedNumberNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Ribbon Badge (#151)
# ---------------------------------------------------------------------------


class RibbonNode(template.Node):
    VARIANT_MAP = {
        "primary": "dj-ribbon--primary",
        "success": "dj-ribbon--success",
        "warning": "dj-ribbon--warning",
        "danger": "dj-ribbon--danger",
    }

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        text = kw.get("text", "")
        variant = kw.get("variant", "primary")
        position = kw.get("position", "top-right")
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        e_text = conditional_escape(str(text))

        classes = ["dj-ribbon"]
        variant_cls = self.VARIANT_MAP.get(str(variant), "dj-ribbon--primary")
        classes.append(variant_cls)

        pos = (
            str(position)
            if str(position) in ("top-left", "top-right", "bottom-left", "bottom-right")
            else "top-right"
        )
        classes.append(f"dj-ribbon--{pos}")

        if e_class:
            classes.append(e_class)

        cls = " ".join(classes)

        return mark_safe(
            f'<div class="{cls}" aria-label="{e_text}">'
            f'<span class="dj-ribbon__text">{e_text}</span>'
            f"</div>"
        )


@register.tag("ribbon")
def do_ribbon(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return RibbonNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Breadcrumb Dropdown (#115)
# ---------------------------------------------------------------------------


class BreadcrumbDropdownNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        items = kw.get("items", [])
        max_visible = kw.get("max_visible", 4)
        separator = kw.get("separator", "/")
        custom_class = kw.get("class", "")

        if not isinstance(items, list):
            items = []

        try:
            max_vis = int(max_visible)
        except (ValueError, TypeError):
            max_vis = 4

        e_class = conditional_escape(str(custom_class))
        e_sep = conditional_escape(str(separator))

        cls = "dj-breadcrumb"
        if e_class:
            cls += f" {e_class}"

        need_collapse = len(items) > max_vis and max_vis >= 2

        def render_item(item, is_last):
            if not isinstance(item, dict):
                return ""
            label = conditional_escape(str(item.get("label", "")))
            url = item.get("url", "")
            aria = ' aria-current="page"' if is_last else ""
            if url and not is_last:
                e_url = conditional_escape(str(url))
                content = f'<a href="{e_url}" class="dj-breadcrumb__link">{label}</a>'
            else:
                content = f'<span class="dj-breadcrumb__current">{label}</span>'
            return f'<li class="dj-breadcrumb__item"{aria}>{content}</li>'

        parts = []
        if need_collapse:
            visible_start = [items[0]]
            collapsed = items[1 : -(max_vis - 1)]
            visible_end = items[-(max_vis - 1) :]

            parts.append(render_item(visible_start[0], False))

            dropdown_items = []
            for it in collapsed:
                if not isinstance(it, dict):
                    continue
                label = conditional_escape(str(it.get("label", "")))
                url = it.get("url", "")
                if url:
                    e_url = conditional_escape(str(url))
                    dropdown_items.append(
                        f'<li class="dj-breadcrumb__dropdown-item">'
                        f'<a href="{e_url}">{label}</a></li>'
                    )
                else:
                    dropdown_items.append(f'<li class="dj-breadcrumb__dropdown-item">{label}</li>')
            parts.append(
                f'<li class="dj-breadcrumb__item dj-breadcrumb__ellipsis">'
                f'<span class="dj-breadcrumb__separator" aria-hidden="true">{e_sep}</span>'
                f'<button type="button" class="dj-breadcrumb__toggle" '
                f'aria-expanded="false" aria-label="Show more">&hellip;</button>'
                f'<ul class="dj-breadcrumb__dropdown">{"".join(dropdown_items)}</ul>'
                f"</li>"
            )
            for i, it in enumerate(visible_end):
                is_last = i == len(visible_end) - 1
                parts.append(render_item(it, is_last))
        else:
            for i, it in enumerate(items):
                is_last = i == len(items) - 1
                parts.append(render_item(it, is_last))

        return mark_safe(
            f'<nav class="{cls}" aria-label="Breadcrumb">'
            f'<ol class="dj-breadcrumb__list">{"".join(parts)}</ol>'
            f"</nav>"
        )


@register.tag("breadcrumb_dropdown")
def do_breadcrumb_dropdown(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return BreadcrumbDropdownNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Data Card Grid (#92)
# ---------------------------------------------------------------------------


class DataCardGridNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        items = kw.get("items", [])
        columns = kw.get("columns", 3)
        filter_key = kw.get("filter_key", "category")
        event = kw.get("event", "")
        custom_class = kw.get("class", "")

        if not isinstance(items, list):
            items = []

        try:
            cols = int(columns)
        except (ValueError, TypeError):
            cols = 3

        e_class = conditional_escape(str(custom_class))
        e_event = conditional_escape(str(event))

        cls = "dj-data-card-grid"
        if e_class:
            cls += f" {e_class}"

        fk = str(filter_key)
        categories = []
        seen = set()
        for it in items:
            if isinstance(it, dict):
                cat = str(it.get(fk, ""))
                if cat and cat not in seen:
                    categories.append(cat)
                    seen.add(cat)

        filter_html = ""
        if categories:
            btns = [
                '<button type="button" class="dj-data-card-grid__filter dj-data-card-grid__filter--active" data-filter="all">All</button>'
            ]
            for cat in categories:
                e_cat = conditional_escape(cat)
                btns.append(
                    f'<button type="button" class="dj-data-card-grid__filter" '
                    f'data-filter="{e_cat}">{e_cat}</button>'
                )
            filter_html = f'<div class="dj-data-card-grid__filters">{"".join(btns)}</div>'

        cards = []
        for it in items:
            if not isinstance(it, dict):
                continue
            title = conditional_escape(str(it.get("title", "")))
            desc = conditional_escape(str(it.get("description", "")))
            cat = conditional_escape(str(it.get(fk, "")))
            image = it.get("image", "")

            img_html = ""
            if image:
                e_img = conditional_escape(str(image))
                img_html = f'<img src="{e_img}" alt="{title}" class="dj-data-card-grid__img">'

            click_attr = ""
            if e_event:
                click_attr = f' dj-click="{e_event}" dj-value-title="{title}"'

            cards.append(
                f'<div class="dj-data-card-grid__card" data-category="{cat}" '
                f'role="listitem"{click_attr}>'
                f"{img_html}"
                f'<div class="dj-data-card-grid__body">'
                f'<h3 class="dj-data-card-grid__title">{title}</h3>'
                f'<p class="dj-data-card-grid__desc">{desc}</p>'
                f"</div></div>"
            )

        style = f"--dj-data-card-grid-cols:{cols}"

        return mark_safe(
            f'<div class="{cls}" style="{style}">'
            f"{filter_html}"
            f'<div class="dj-data-card-grid__grid" role="list">'
            f"{''.join(cards)}</div></div>"
        )


@register.tag("data_card_grid")
def do_data_card_grid(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return DataCardGridNode(kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: Agent Step Card (#154)
# ---------------------------------------------------------------------------


class AgentStepNode(template.Node):
    STATUS_ICONS = {
        "pending": "&#9711;",
        "running": "&#8987;",
        "complete": "&#10003;",
        "error": "&#10007;",
    }

    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        tool = kw.get("tool", "")
        status = kw.get("status", "pending")
        duration = kw.get("duration", "")
        custom_class = kw.get("class", "")

        content = self.nodelist.render(context).strip()

        status = str(status) if str(status) in self.STATUS_ICONS else "pending"

        e_class = conditional_escape(str(custom_class))
        e_tool = conditional_escape(str(tool))
        e_duration = conditional_escape(str(duration))
        e_content = conditional_escape(content) if content else ""

        classes = ["dj-agent-step", f"dj-agent-step--{status}"]
        if e_class:
            classes.append(e_class)
        cls = " ".join(classes)

        icon = self.STATUS_ICONS.get(status, "&#9711;")

        duration_html = ""
        if e_duration:
            duration_html = f'<span class="dj-agent-step__duration">{e_duration}</span>'

        content_html = ""
        if e_content:
            content_html = f'<div class="dj-agent-step__content">{e_content}</div>'

        return mark_safe(
            f'<div class="{cls}" role="listitem">'
            f'<div class="dj-agent-step__header">'
            f'<span class="dj-agent-step__icon" aria-hidden="true">{icon}</span>'
            f'<span class="dj-agent-step__tool">{e_tool}</span>'
            f'<span class="dj-agent-step__status">{conditional_escape(status)}</span>'
            f"{duration_html}"
            f"</div>"
            f"{content_html}"
            f"</div>"
        )


@register.tag("agent_step")
def do_agent_step(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endagent_step",))
    parser.delete_first_token()
    return AgentStepNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# v2.0 Final Batch: QR Code (#157)
# ---------------------------------------------------------------------------


class QRCodeNode(template.Node):
    SIZE_MAP = {"sm": 128, "md": 200, "lg": 300}

    def __init__(self, kwargs):
        self.kwargs = kwargs

    @staticmethod
    def _generate_matrix(data_str):
        size = 21
        matrix = [[False] * size for _ in range(size)]

        def add_finder(row, col):
            for r in range(7):
                for c in range(7):
                    if row + r < size and col + c < size:
                        is_border = r in (0, 6) or c in (0, 6)
                        is_inner = 2 <= r <= 4 and 2 <= c <= 4
                        matrix[row + r][col + c] = is_border or is_inner

        add_finder(0, 0)
        add_finder(0, size - 7)
        add_finder(size - 7, 0)

        for i in range(8, size - 8):
            matrix[6][i] = i % 2 == 0
            matrix[i][6] = i % 2 == 0

        data_bytes = data_str.encode("utf-8") if data_str else b"\x00"
        byte_idx = 0
        bit_idx = 0
        for r in range(size):
            for c in range(size):
                if matrix[r][c]:
                    continue
                if (r < 9 and c < 9) or (r < 9 and c >= size - 8) or (r >= size - 8 and c < 9):
                    continue
                if r == 6 or c == 6:
                    continue
                b = data_bytes[byte_idx % len(data_bytes)]
                matrix[r][c] = bool((b >> (7 - bit_idx)) & 1)
                bit_idx += 1
                if bit_idx >= 8:
                    bit_idx = 0
                    byte_idx += 1

        return matrix

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        data = kw.get("data", "")
        size = kw.get("size", "md")
        fg_color = kw.get("fg_color", "#000")
        bg_color = kw.get("bg_color", "#fff")
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        e_data = conditional_escape(str(data))
        e_fg = conditional_escape(str(fg_color))
        e_bg = conditional_escape(str(bg_color))

        cls = "dj-qr-code"
        if e_class:
            cls += f" {e_class}"

        if isinstance(size, str) and size in self.SIZE_MAP:
            px = self.SIZE_MAP[size]
        else:
            try:
                px = int(size)
            except (ValueError, TypeError):
                px = 200

        matrix = self._generate_matrix(str(data))
        mod_count = len(matrix)
        cell = px / mod_count

        rects = []
        for r, row in enumerate(matrix):
            for c, val in enumerate(row):
                if val:
                    x = c * cell
                    y = r * cell
                    rects.append(
                        f'<rect x="{x:.2f}" y="{y:.2f}" '
                        f'width="{cell:.2f}" height="{cell:.2f}" '
                        f'fill="{e_fg}"/>'
                    )

        return mark_safe(
            f'<div class="{cls}">'
            f'<svg class="dj-qr-code__svg" viewBox="0 0 {px} {px}" '
            f'width="{px}" height="{px}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="QR code: {e_data}">'
            f'<rect width="{px}" height="{px}" fill="{e_bg}"/>'
            f"{''.join(rects)}"
            f"</svg></div>"
        )


@register.tag("qr_code")
def do_qr_code(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return QRCodeNode(kwargs)
