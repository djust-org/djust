"""
Chart template tags — BarChart, LineChart, PieChart, Sparkline, Heatmap,
Treemap, CalendarHeatmap.

Extracted from the monolithic djust_components.py for maintainability.
All tags register on the shared ``register`` from ``_registry``.
"""

import math as _math

from django import template

from ._registry import (
    register,
    _resolve,
    _parse_kv_args,
    conditional_escape,
    mark_safe,
    interpolate_color,
)

# ---------------------------------------------------------------------------
# Bar Chart
# ---------------------------------------------------------------------------


class BarChartNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        data = kw.get("data", [])
        labels = kw.get("labels", [])
        title = kw.get("title", "")
        width = kw.get("width", 400)
        height = kw.get("height", 250)
        color = kw.get("color", "")
        show_values = kw.get("show_values", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-bar-chart"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(data, list):
            data = []
        if not isinstance(labels, list):
            labels = []

        try:
            width = int(width)
        except (ValueError, TypeError):
            width = 400
        try:
            height = int(height)
        except (ValueError, TypeError):
            height = 250

        if not data:
            return mark_safe(f'<div class="{class_str}"><svg></svg></div>')

        w, h = width, height
        pad_top = 30 if title else 10
        pad_bottom = 30
        pad_left = 40
        pad_right = 10
        chart_w = w - pad_left - pad_right
        chart_h = h - pad_top - pad_bottom

        vals = []
        for v in data:
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                vals.append(0)

        max_val = max(vals) if vals else 1
        if max_val <= 0:
            max_val = 1

        n = len(vals)
        bar_gap = 4
        bar_w = max(1, (chart_w - (n - 1) * bar_gap) / n)

        e_title = conditional_escape(str(title)) if title else "Bar chart"
        parts = [
            f'<svg class="dj-bar-chart__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{e_title}">'
        ]

        if title:
            parts.append(
                f'<text class="dj-bar-chart__title" x="{w / 2}" y="18" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{conditional_escape(str(title))}</text>"
            )

        e_color = conditional_escape(str(color)) if color else ""
        color_attr = f' fill="{e_color}"' if e_color else ""

        for i, val in enumerate(vals):
            bar_h = (val / max_val) * chart_h if max_val > 0 else 0
            x = pad_left + i * (bar_w + bar_gap)
            y = pad_top + chart_h - bar_h

            lbl = conditional_escape(str(labels[i])) if i < len(labels) else ""
            parts.append(
                f'<rect class="dj-bar-chart__bar" x="{x:.1f}" y="{y:.1f}" '
                f'width="{bar_w:.1f}" height="{bar_h:.1f}"{color_attr}>'
                f"<title>{lbl}: {val}</title></rect>"
            )

            if show_values:
                parts.append(
                    f'<text class="dj-bar-chart__value" x="{x + bar_w / 2:.1f}" '
                    f'y="{y - 4:.1f}" text-anchor="middle" font-size="10">'
                    f"{val:g}</text>"
                )

            if i < len(labels):
                parts.append(
                    f'<text class="dj-bar-chart__label" x="{x + bar_w / 2:.1f}" '
                    f'y="{pad_top + chart_h + 16:.1f}" text-anchor="middle" font-size="10">'
                    f"{lbl}</text>"
                )

        parts.append("</svg>")
        return mark_safe(f'<div class="{class_str}">{"".join(parts)}</div>')


@register.tag("bar_chart")
def do_bar_chart(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return BarChartNode(kwargs)


# ---------------------------------------------------------------------------
# Line Chart
# ---------------------------------------------------------------------------


class LineChartNode(template.Node):
    COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6", "#06b6d4"]

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        series = kw.get("series", [])
        labels = kw.get("labels", [])
        title = kw.get("title", "")
        width = kw.get("width", 400)
        height = kw.get("height", 250)
        area = kw.get("area", False)
        show_dots = kw.get("show_dots", True)
        show_legend = kw.get("show_legend", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-line-chart"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(series, list):
            series = []
        if not isinstance(labels, list):
            labels = []

        try:
            width = int(width)
        except (ValueError, TypeError):
            width = 400
        try:
            height = int(height)
        except (ValueError, TypeError):
            height = 250

        if not series:
            return mark_safe(f'<div class="{class_str}"><svg></svg></div>')

        w, h = width, height
        pad_top = 30 if title else 10
        pad_bottom = 40 if show_legend else 30
        pad_left = 40
        pad_right = 10
        chart_w = w - pad_left - pad_right
        chart_h = h - pad_top - pad_bottom

        all_vals = []
        for s in series:
            if isinstance(s, dict):
                for v in s.get("data", []):
                    try:
                        all_vals.append(float(v))
                    except (ValueError, TypeError):
                        pass
        max_val = max(all_vals) if all_vals else 1
        min_val = min(all_vals) if all_vals else 0
        val_range = max_val - min_val if max_val != min_val else 1

        e_title = conditional_escape(str(title)) if title else "Line chart"
        parts = [
            f'<svg class="dj-line-chart__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{e_title}">'
        ]

        if title:
            parts.append(
                f'<text class="dj-line-chart__title" x="{w / 2}" y="18" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{conditional_escape(str(title))}</text>"
            )

        for si, s in enumerate(series):
            if not isinstance(s, dict):
                continue
            data = s.get("data", [])
            color = conditional_escape(str(s.get("color", self.COLORS[si % len(self.COLORS)])))
            name = conditional_escape(str(s.get("name", f"Series {si + 1}")))

            if not data:
                continue

            n = len(data)
            points = []
            for i, v in enumerate(data):
                try:
                    v = float(v)
                except (ValueError, TypeError):
                    v = 0
                x = pad_left + (i / max(n - 1, 1)) * chart_w
                y = pad_top + chart_h - ((v - min_val) / val_range) * chart_h
                points.append((x, y, v))

            path = " ".join(
                f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _) in enumerate(points)
            )

            if area and points:
                area_path = (
                    path
                    + f" L{points[-1][0]:.1f},{pad_top + chart_h:.1f}"
                    + f" L{points[0][0]:.1f},{pad_top + chart_h:.1f} Z"
                )
                parts.append(
                    f'<path class="dj-line-chart__area" d="{area_path}" '
                    f'fill="{color}" opacity="0.15"/>'
                )

            parts.append(
                f'<path class="dj-line-chart__line" d="{path}" '
                f'fill="none" stroke="{color}" stroke-width="2"/>'
            )

            if show_dots:
                for x, y, v in points:
                    parts.append(
                        f'<circle class="dj-line-chart__dot" cx="{x:.1f}" cy="{y:.1f}" '
                        f'r="3" fill="{color}">'
                        f"<title>{name}: {v:g}</title></circle>"
                    )

        if labels:
            n = len(labels)
            for i, lbl in enumerate(labels):
                x = pad_left + (i / max(n - 1, 1)) * chart_w
                parts.append(
                    f'<text class="dj-line-chart__label" x="{x:.1f}" '
                    f'y="{pad_top + chart_h + 16:.1f}" text-anchor="middle" font-size="10">'
                    f"{conditional_escape(str(lbl))}</text>"
                )

        if show_legend and series:
            lx = pad_left
            ly = h - 8
            for si, s in enumerate(series):
                if not isinstance(s, dict):
                    continue
                color = conditional_escape(str(s.get("color", self.COLORS[si % len(self.COLORS)])))
                name = conditional_escape(str(s.get("name", f"Series {si + 1}")))
                parts.append(
                    f'<rect x="{lx}" y="{ly - 6}" width="10" height="10" rx="2" fill="{color}"/>'
                )
                parts.append(f'<text x="{lx + 14}" y="{ly + 3}" font-size="10">{name}</text>')
                lx += len(name) * 7 + 24

        parts.append("</svg>")
        return mark_safe(f'<div class="{class_str}">{"".join(parts)}</div>')


@register.tag("line_chart")
def do_line_chart(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return LineChartNode(kwargs)


# ---------------------------------------------------------------------------
# Pie / Donut Chart
# ---------------------------------------------------------------------------


class PieChartNode(template.Node):
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
        segments = kw.get("segments", [])
        title = kw.get("title", "")
        width = kw.get("width", 300)
        height = kw.get("height", 300)
        donut = kw.get("donut", False)
        inner_radius = kw.get("inner_radius", 0.6)
        show_labels = kw.get("show_labels", True)
        show_legend = kw.get("show_legend", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-pie-chart"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(segments, list):
            segments = []

        try:
            width = int(width)
        except (ValueError, TypeError):
            width = 300
        try:
            height = int(height)
        except (ValueError, TypeError):
            height = 300
        try:
            inner_radius = float(inner_radius)
        except (ValueError, TypeError):
            inner_radius = 0.6

        if not segments:
            return mark_safe(f'<div class="{class_str}"><svg></svg></div>')

        w, h = width, height
        title_offset = 24 if title else 0
        legend_offset = 24 if show_legend else 0
        cx = w / 2
        cy = title_offset + (h - title_offset - legend_offset) / 2
        r = min(cx, (h - title_offset - legend_offset) / 2) - 10
        ir = r * inner_radius if donut else 0

        total = 0
        for seg in segments:
            if isinstance(seg, dict):
                try:
                    total += float(seg.get("value", 0))
                except (ValueError, TypeError):
                    pass

        if total <= 0:
            return mark_safe(f'<div class="{class_str}"><svg></svg></div>')

        e_title = conditional_escape(str(title)) if title else "Pie chart"
        parts = [
            f'<svg class="dj-pie-chart__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{e_title}">'
        ]

        if title:
            parts.append(
                f'<text class="dj-pie-chart__title" x="{cx}" y="18" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{conditional_escape(str(title))}</text>"
            )

        angle = -_math.pi / 2

        for si, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            try:
                val = float(seg.get("value", 0))
            except (ValueError, TypeError):
                val = 0
            if val <= 0:
                continue
            color = conditional_escape(str(seg.get("color", self.COLORS[si % len(self.COLORS)])))
            label = conditional_escape(str(seg.get("label", "")))
            pct = val / total

            sweep = pct * 2 * _math.pi
            x1 = cx + r * _math.cos(angle)
            y1 = cy + r * _math.sin(angle)
            x2 = cx + r * _math.cos(angle + sweep)
            y2 = cy + r * _math.sin(angle + sweep)
            large = 1 if sweep > _math.pi else 0

            if donut:
                ix1 = cx + ir * _math.cos(angle)
                iy1 = cy + ir * _math.sin(angle)
                ix2 = cx + ir * _math.cos(angle + sweep)
                iy2 = cy + ir * _math.sin(angle + sweep)
                d = (
                    f"M{x1:.2f},{y1:.2f} A{r},{r} 0 {large},1 {x2:.2f},{y2:.2f} "
                    f"L{ix2:.2f},{iy2:.2f} A{ir},{ir} 0 {large},0 {ix1:.2f},{iy1:.2f} Z"
                )
            else:
                d = f"M{cx},{cy} L{x1:.2f},{y1:.2f} A{r},{r} 0 {large},1 {x2:.2f},{y2:.2f} Z"

            parts.append(
                f'<path class="dj-pie-chart__segment" d="{d}" fill="{color}">'
                f"<title>{label}: {val:g} ({pct * 100:.1f}%)</title></path>"
            )

            if show_labels and pct >= 0.05:
                mid_angle = angle + sweep / 2
                lr = r * 0.7 if not donut else (r + ir) / 2
                lx = cx + lr * _math.cos(mid_angle)
                ly = cy + lr * _math.sin(mid_angle)
                parts.append(
                    f'<text class="dj-pie-chart__pct" x="{lx:.1f}" y="{ly:.1f}" '
                    f'text-anchor="middle" dominant-baseline="central" '
                    f'font-size="10" fill="#fff" font-weight="600">'
                    f"{pct * 100:.0f}%</text>"
                )

            angle += sweep

        if show_legend:
            lx = 10
            ly = h - 8
            for si, seg in enumerate(segments):
                if not isinstance(seg, dict):
                    continue
                color = conditional_escape(
                    str(seg.get("color", self.COLORS[si % len(self.COLORS)]))
                )
                label = conditional_escape(str(seg.get("label", "")))
                parts.append(
                    f'<rect x="{lx}" y="{ly - 6}" width="10" height="10" rx="2" fill="{color}"/>'
                )
                parts.append(f'<text x="{lx + 14}" y="{ly + 3}" font-size="10">{label}</text>')
                lx += len(label) * 7 + 24

        parts.append("</svg>")
        return mark_safe(f'<div class="{class_str}">{"".join(parts)}</div>')


@register.tag("pie_chart")
def do_pie_chart(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return PieChartNode(kwargs)


# ---------------------------------------------------------------------------
# Sparkline
# ---------------------------------------------------------------------------


class SparklineNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        data = kw.get("data", [])
        variant = kw.get("variant", "line")
        width = kw.get("width", 100)
        height = kw.get("height", 24)
        color = kw.get("color", "")
        stroke_width = kw.get("stroke_width", 1.5)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-sparkline"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(data, list):
            data = []

        try:
            width = int(width)
        except (ValueError, TypeError):
            width = 100
        try:
            height = int(height)
        except (ValueError, TypeError):
            height = 24
        try:
            stroke_width = float(stroke_width)
        except (ValueError, TypeError):
            stroke_width = 1.5

        if not data:
            return mark_safe(f'<span class="{class_str}"><svg></svg></span>')

        w, h = width, height
        pad = 2
        chart_w = w - pad * 2
        chart_h = h - pad * 2

        vals = []
        for v in data:
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                vals.append(0)

        max_val = max(vals) if vals else 1
        min_val = min(vals) if vals else 0
        val_range = max_val - min_val if max_val != min_val else 1

        e_color = conditional_escape(str(color)) if color else ""

        parts = [
            f'<svg class="dj-sparkline__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="Sparkline">'
        ]

        if variant == "bar":
            n = len(vals)
            bar_gap = 1
            bar_w = max(1, (chart_w - (n - 1) * bar_gap) / n)
            for i, v in enumerate(vals):
                bar_h = max(1, ((v - min_val) / val_range) * chart_h)
                x = pad + i * (bar_w + bar_gap)
                y = pad + chart_h - bar_h
                fill = f' fill="{e_color}"' if e_color else ""
                parts.append(
                    f'<rect class="dj-sparkline__bar" x="{x:.1f}" y="{y:.1f}" '
                    f'width="{bar_w:.1f}" height="{bar_h:.1f}"{fill}/>'
                )
        else:
            n = len(vals)
            points = []
            for i, v in enumerate(vals):
                x = pad + (i / max(n - 1, 1)) * chart_w
                y = pad + chart_h - ((v - min_val) / val_range) * chart_h
                points.append((x, y))

            path = " ".join(
                f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points)
            )

            if variant == "area" and points:
                area_path = (
                    path
                    + f" L{points[-1][0]:.1f},{pad + chart_h:.1f}"
                    + f" L{points[0][0]:.1f},{pad + chart_h:.1f} Z"
                )
                fill = f' fill="{e_color}"' if e_color else ""
                parts.append(
                    f'<path class="dj-sparkline__area" d="{area_path}"{fill} opacity="0.2"/>'
                )

            stroke = f' stroke="{e_color}"' if e_color else ""
            parts.append(
                f'<path class="dj-sparkline__line" d="{path}" '
                f'fill="none"{stroke} stroke-width="{stroke_width}"/>'
            )

        parts.append("</svg>")
        return mark_safe(f'<span class="{class_str}">{"".join(parts)}</span>')


@register.tag("sparkline")
def do_sparkline(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return SparklineNode(kwargs)


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------


class HeatmapNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    _interpolate_color = staticmethod(interpolate_color)

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        data = kw.get("data", [])
        x_labels = kw.get("x_labels", [])
        y_labels = kw.get("y_labels", [])
        title = kw.get("title", "")
        color_min = kw.get("color_min", "#f0f9ff")
        color_max = kw.get("color_max", "#1e40af")
        cell_size = kw.get("cell_size", 36)
        show_values = kw.get("show_values", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-heatmap"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(data, list):
            data = []
        if not isinstance(x_labels, list):
            x_labels = []
        if not isinstance(y_labels, list):
            y_labels = []

        try:
            cell_size = int(cell_size)
        except (ValueError, TypeError):
            cell_size = 36

        if not data:
            return mark_safe(f'<div class="{class_str}"><svg></svg></div>')

        cs = cell_size
        rows = len(data)
        cols = max((len(row) for row in data if isinstance(row, list)), default=0)
        if cols == 0:
            return mark_safe(f'<div class="{class_str}"><svg></svg></div>')

        label_left = 60 if y_labels else 0
        label_top = 20 if x_labels else 0
        title_h = 24 if title else 0
        w = label_left + cols * cs + 4
        h = title_h + label_top + rows * cs + 4

        all_vals = []
        for row in data:
            if not isinstance(row, list):
                continue
            for v in row:
                try:
                    all_vals.append(float(v))
                except (ValueError, TypeError):
                    pass
        min_v = min(all_vals) if all_vals else 0
        max_v = max(all_vals) if all_vals else 1
        val_range = max_v - min_v if max_v != min_v else 1

        e_title = conditional_escape(str(title)) if title else "Heatmap"
        parts = [
            f'<svg class="dj-heatmap__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{e_title}">'
        ]

        if title:
            parts.append(
                f'<text class="dj-heatmap__title" x="{w / 2}" y="16" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{conditional_escape(str(title))}</text>"
            )

        for ci, lbl in enumerate(x_labels[:cols]):
            x = label_left + ci * cs + cs / 2
            y = title_h + label_top - 4
            parts.append(
                f'<text class="dj-heatmap__xlabel" x="{x:.1f}" y="{y:.1f}" '
                f'text-anchor="middle" font-size="10">'
                f"{conditional_escape(str(lbl))}</text>"
            )

        for ri, lbl in enumerate(y_labels[:rows]):
            x = label_left - 4
            y = title_h + label_top + ri * cs + cs / 2
            parts.append(
                f'<text class="dj-heatmap__ylabel" x="{x:.1f}" y="{y:.1f}" '
                f'text-anchor="end" dominant-baseline="central" font-size="10">'
                f"{conditional_escape(str(lbl))}</text>"
            )

        for ri, row in enumerate(data):
            if not isinstance(row, list):
                continue
            for ci, v in enumerate(row):
                try:
                    val = float(v)
                except (ValueError, TypeError):
                    val = 0
                t = (val - min_v) / val_range
                color = self._interpolate_color(color_min, color_max, t)
                x = label_left + ci * cs
                y_pos = title_h + label_top + ri * cs

                parts.append(
                    f'<rect class="dj-heatmap__cell" x="{x}" y="{y_pos}" '
                    f'width="{cs}" height="{cs}" fill="{color}" stroke="#fff" stroke-width="1">'
                    f"<title>{val:g}</title></rect>"
                )

                if show_values:
                    text_color = "#fff" if t > 0.5 else "#1e293b"
                    parts.append(
                        f'<text class="dj-heatmap__value" x="{x + cs / 2:.1f}" '
                        f'y="{y_pos + cs / 2:.1f}" text-anchor="middle" '
                        f'dominant-baseline="central" font-size="10" fill="{text_color}">'
                        f"{val:g}</text>"
                    )

        parts.append("</svg>")
        return mark_safe(f'<div class="{class_str}">{"".join(parts)}</div>')


@register.tag("heatmap")
def do_heatmap(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return HeatmapNode(kwargs)


# ---------------------------------------------------------------------------
# Treemap
# ---------------------------------------------------------------------------


class TreemapNode(template.Node):
    COLORS = [
        "#3b82f6",
        "#ef4444",
        "#22c55e",
        "#f59e0b",
        "#8b5cf6",
        "#06b6d4",
        "#ec4899",
        "#f97316",
        "#14b8a6",
        "#a855f7",
    ]

    def __init__(self, kwargs):
        self.kwargs = kwargs

    @staticmethod
    def _squarify(items, x, y, w, h):
        rects = []
        if not items or w <= 0 or h <= 0:
            return rects
        total = sum(v for _, v, _ in items)
        if total <= 0:
            return rects
        if w >= h:
            cx = x
            for label, val, idx in items:
                frac = val / total
                rw = w * frac
                rects.append((cx, y, rw, h, label, val, idx))
                cx += rw
        else:
            cy = y
            for label, val, idx in items:
                frac = val / total
                rh = h * frac
                rects.append((x, cy, w, rh, label, val, idx))
                cy += rh
        return rects

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        data = kw.get("data", [])
        value_key = kw.get("value_key", "size")
        label_key = kw.get("label_key", "name")
        title = kw.get("title", "")
        width = kw.get("width", 400)
        height = kw.get("height", 250)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-treemap"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(data, list):
            data = []

        try:
            width = int(width)
        except (ValueError, TypeError):
            width = 400
        try:
            height = int(height)
        except (ValueError, TypeError):
            height = 250

        if not data:
            return mark_safe(f'<div class="{class_str}"><svg></svg></div>')

        w, h = width, height
        title_h = 24 if title else 0
        chart_h = h - title_h

        items = []
        for i, d in enumerate(data):
            if not isinstance(d, dict):
                continue
            try:
                val = float(d.get(value_key, 0))
            except (ValueError, TypeError):
                val = 0
            if val > 0:
                label = str(d.get(label_key, ""))
                items.append((label, val, i))

        items.sort(key=lambda x: x[1], reverse=True)
        rects = self._squarify(items, 0, title_h, w, chart_h)

        e_title = conditional_escape(str(title)) if title else "Treemap"
        parts = [
            f'<svg class="dj-treemap__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{e_title}">'
        ]

        if title:
            parts.append(
                f'<text class="dj-treemap__title" x="{w / 2}" y="16" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{conditional_escape(str(title))}</text>"
            )

        for rx, ry, rw, rh, label, val, idx in rects:
            color = conditional_escape(str(self.COLORS[idx % len(self.COLORS)]))
            e_label = conditional_escape(label)
            parts.append(
                f'<rect class="dj-treemap__cell" x="{rx:.1f}" y="{ry:.1f}" '
                f'width="{rw:.1f}" height="{rh:.1f}" fill="{color}" '
                f'stroke="#fff" stroke-width="2">'
                f"<title>{e_label}: {val:g}</title></rect>"
            )
            if rw > 30 and rh > 20:
                parts.append(
                    f'<text class="dj-treemap__label" '
                    f'x="{rx + rw / 2:.1f}" y="{ry + rh / 2:.1f}" '
                    f'text-anchor="middle" dominant-baseline="central" '
                    f'font-size="{min(11, rw / max(len(label), 1) * 1.2):.0f}" '
                    f'fill="#fff" font-weight="600">{e_label}</text>'
                )

        parts.append("</svg>")
        return mark_safe(f'<div class="{class_str}">{"".join(parts)}</div>')


@register.tag("treemap")
def do_treemap(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return TreemapNode(kwargs)


# ---------------------------------------------------------------------------
# Calendar Heatmap
# ---------------------------------------------------------------------------

from datetime import date as _date, timedelta as _timedelta


class CalendarHeatmapNode(template.Node):
    LEVELS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def _get_color(self, value, max_val):
        if value <= 0:
            return self.LEVELS[0]
        if max_val <= 0:
            return self.LEVELS[0]
        ratio = value / max_val
        if ratio <= 0.25:
            return self.LEVELS[1]
        elif ratio <= 0.5:
            return self.LEVELS[2]
        elif ratio <= 0.75:
            return self.LEVELS[3]
        else:
            return self.LEVELS[4]

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        data = kw.get("data", {})
        year = kw.get("year", _date.today().year)
        title = kw.get("title", "")
        cell_size = kw.get("cell_size", 12)
        cell_gap = kw.get("cell_gap", 2)
        show_month_labels = kw.get("show_month_labels", True)
        show_day_labels = kw.get("show_day_labels", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-calendar-heatmap"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(data, dict):
            data = {}

        try:
            year = int(year)
        except (ValueError, TypeError):
            year = _date.today().year
        try:
            cell_size = int(cell_size)
        except (ValueError, TypeError):
            cell_size = 12
        try:
            cell_gap = int(cell_gap)
        except (ValueError, TypeError):
            cell_gap = 2

        cs = cell_size
        cg = cell_gap
        step = cs + cg

        start = _date(year, 1, 1)
        end = _date(year, 12, 31)

        vals = []
        for v in data.values():
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                pass
        max_val = max(vals) if vals else 1

        label_left = 30 if show_day_labels else 0
        label_top = 16 if show_month_labels else 0
        title_h = 22 if title else 0

        first_dow = start.weekday()
        num_days = (end - start).days + 1
        num_weeks = ((first_dow + num_days - 1) // 7) + 1

        w = label_left + num_weeks * step + 4
        h = title_h + label_top + 7 * step + 4

        e_title = conditional_escape(str(title)) if title else f"{year} activity"
        parts = [
            f'<svg class="dj-calendar-heatmap__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{e_title}">'
        ]

        if title:
            parts.append(
                f'<text class="dj-calendar-heatmap__title" x="{w / 2}" y="16" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{conditional_escape(str(title))}</text>"
            )

        if show_day_labels:
            day_names = ["Mon", "", "Wed", "", "Fri", "", ""]
            for di, name in enumerate(day_names):
                if name:
                    y = title_h + label_top + di * step + cs / 2
                    parts.append(
                        f'<text class="dj-calendar-heatmap__day-label" x="{label_left - 4}" '
                        f'y="{y:.1f}" text-anchor="end" dominant-baseline="central" '
                        f'font-size="9">{name}</text>'
                    )

        month_names = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        month_positions = {}

        current = start
        while current <= end:
            day_of_year = (current - start).days
            dow = current.weekday()
            week = (first_dow + day_of_year) // 7

            x = label_left + week * step
            y = title_h + label_top + dow * step

            date_str = current.isoformat()
            try:
                val = float(data.get(date_str, 0))
            except (ValueError, TypeError):
                val = 0
            color = self._get_color(val, max_val)

            parts.append(
                f'<rect class="dj-calendar-heatmap__cell" x="{x}" y="{y}" '
                f'width="{cs}" height="{cs}" rx="2" fill="{color}">'
                f"<title>{date_str}: {val:g}</title></rect>"
            )

            if current.day == 1:
                month_positions[current.month] = x

            current += _timedelta(days=1)

        if show_month_labels:
            for month, mx in month_positions.items():
                parts.append(
                    f'<text class="dj-calendar-heatmap__month-label" '
                    f'x="{mx}" y="{title_h + label_top - 4}" '
                    f'font-size="9">{month_names[month - 1]}</text>'
                )

        parts.append("</svg>")
        return mark_safe(f'<div class="{class_str}">{"".join(parts)}</div>')


@register.tag("calendar_heatmap")
def do_calendar_heatmap(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return CalendarHeatmapNode(kwargs)
