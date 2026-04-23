"""
Rust template engine handlers for djust-components.

Registers all component tags with the Rust tag handler registry so that
{% modal %}, {% alert %}, {% dj_button %}, etc. work in djust-templating
Rust-rendered templates — no {% load djust_components %} needed.

Inline handlers implement: render(self, args, context) -> str
Block handlers implement:  render(self, args, content, context) -> str
"""

import json as _json
import uuid as _uuid

from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

from djust.components.utils import (
    CURRENCY_SYMBOLS,
    format_cell as _format_cell_util,
    interpolate_color,
    interpolate_color_gradient,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_args(args: list[str], context: dict[str, object]) -> dict[str, object]:
    """Parse handler arg list ["key='val'", "key2=var"] into a dict.

    Resolves variable references against the template context dict.
    Values that are JSON-encoded lists/objects (from the Rust engine's
    variable resolution) are deserialized automatically.
    """

    result = {}
    for arg in args:
        if "=" not in arg:
            continue
        key, val = arg.split("=", 1)
        key = key.strip()
        val = val.strip()
        # String literal — strip quotes
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            result[key] = val[1:-1]
        # JSON array or object (from Rust variable resolution)
        elif (val.startswith("[") and val.endswith("]")) or (
            val.startswith("{") and val.endswith("}")
        ):
            try:
                result[key] = _json.loads(val)
            except (ValueError, TypeError):
                result[key] = context.get(val, val)
        # Boolean
        elif val in ("True", "true"):
            result[key] = True
        elif val in ("False", "false"):
            result[key] = False
        elif val == "":
            result[key] = ""
        # None
        elif val in ("None", "null"):
            result[key] = None
        else:
            # Try numeric before falling back to variable reference
            try:
                result[key] = int(val)
            except ValueError:
                try:
                    result[key] = float(val)
                except ValueError:
                    # Variable reference — look up in context
                    result[key] = context.get(val, val)
    return result


# ---------------------------------------------------------------------------
# Block handlers (wrap content — e.g. {% modal %}...{% endmodal %})
# ---------------------------------------------------------------------------


class ModalHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        is_open = kw.get("open", False)
        if not is_open:
            return ""
        title = conditional_escape(kw.get("title", ""))
        size = kw.get("size", "md")
        close_event = conditional_escape(kw.get("close_event", "close_modal"))
        size_class = {
            "sm": "modal-sm",
            "md": "modal-md",
            "lg": "modal-lg",
            "xl": "modal-xl",
        }.get(str(size), "modal-md")
        return mark_safe(
            f'<div class="modal-overlay {size_class}" dj-click="{close_event}">'
            f'<div class="modal-content" onclick="event.stopPropagation()">'
            f'<div class="modal-header">'
            f'<h3 class="modal-title">{title}</h3>'
            f'<button class="modal-close" dj-click="{close_event}">&times;</button>'
            f"</div>"
            f'<div class="modal-body">{content}</div>'
            f"</div>"
            f"</div>"
        )


class CardHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        title = kw.get("title", "")
        subtitle = kw.get("subtitle", "")
        variant = conditional_escape(kw.get("variant", "default"))
        extra_class = conditional_escape(kw.get("class", ""))
        header = ""
        if title:
            sub = f'<p class="card-subtitle">{conditional_escape(subtitle)}</p>' if subtitle else ""
            header = (
                f'<div class="card-header">'
                f'<h3 class="card-title">{conditional_escape(title)}</h3>{sub}'
                f"</div>"
            )
        return mark_safe(
            f'<div class="card card-{variant} {extra_class}">'
            f"{header}"
            f'<div class="card-body">{content}</div>'
            f"</div>"
        )


class TabsHandler:
    def render(self, args, content, context):
        # Tabs need active state from args to render nav — content already rendered
        kw = _parse_args(args, context)
        tabs_id = conditional_escape(kw.get("id", "tabs"))
        # For block-handler mode, the nav is built from child content rendered as panes.
        # We wrap in the tabs container; view logic controls active tab.
        return mark_safe(f'<div class="tabs-container" id="{tabs_id}">{content}</div>')


class AccordionHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        accordion_id = conditional_escape(kw.get("id", "accordion"))
        return mark_safe(f'<div class="accordion" id="{accordion_id}">{content}</div>')


class AccordionItemHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        title = conditional_escape(kw.get("title", ""))
        item_id = conditional_escape(kw.get("id", ""))
        event = conditional_escape(kw.get("event", "accordion_toggle"))
        is_open = kw.get("open", False)
        open_cls = "accordion-item--open" if is_open else ""
        expanded = "true" if is_open else "false"
        panel_hidden = "" if is_open else " hidden"
        return mark_safe(
            f'<div class="accordion-item {open_cls}">'
            f'<button class="accordion-trigger" aria-expanded="{expanded}" '
            f'dj-click="{event}" data-value="{item_id}">'
            f'<span class="accordion-title">{title}</span>'
            f'<svg class="accordion-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
            f'<polyline points="6 9 12 15 18 9"></polyline></svg>'
            f"</button>"
            f'<div class="accordion-panel"{panel_hidden}>{content}</div>'
            f"</div>"
        )


class DropdownHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        dropdown_id = conditional_escape(kw.get("id", "dropdown"))
        label = conditional_escape(kw.get("label", "Menu"))
        is_open = kw.get("open", False)
        toggle_event = conditional_escape(kw.get("toggle_event", "toggle_dropdown"))
        open_data = "true" if is_open else "false"
        return mark_safe(
            f'<div class="dropdown" id="{dropdown_id}">'
            f'<button class="dropdown-trigger" dj-click="{toggle_event}">{label}</button>'
            f'<div class="dropdown-menu" data-open="{open_data}">{content}</div>'
            f"</div>"
        )


class AlertHandler:
    _icons = {
        "info": "ℹ",
        "success": "✓",
        "warning": "⚠",
        "error": "✕",
        "danger": "✕",
    }

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        alert_type = kw.get("variant", kw.get("type", "info"))
        if alert_type == "danger":
            alert_type = "error"
        title = conditional_escape(kw.get("title", ""))
        dismissible = kw.get("dismissible", False)
        event = conditional_escape(kw.get("event", "dismiss_alert"))
        icon_char = self._icons.get(str(alert_type), "ℹ")
        title_html = f'<div class="alert-title">{title}</div>' if title else ""
        close_html = (
            f'<button class="alert-close" dj-click="{event}">&times;</button>'
            if dismissible
            else ""
        )
        return mark_safe(
            f'<div class="alert alert-{conditional_escape(alert_type)}'
            f'{"  alert-dismissible" if dismissible else ""}">'
            f'<span class="alert-icon">{icon_char}</span>'
            f'<div class="alert-body">{title_html}'
            f'<div class="alert-message">{content}</div>'
            f"</div>"
            f"{close_html}"
            f"</div>"
        )


class FormGroupHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        error = conditional_escape(kw.get("error", ""))
        helper = conditional_escape(kw.get("helper", ""))
        required = kw.get("required", False)
        for_input = conditional_escape(kw.get("for_input", ""))
        required_span = '<span class="form-label-required">*</span>' if required else ""
        label_html = (
            f'<label class="form-label" for="{for_input}">{label}{required_span}</label>'
            if label
            else ""
        )
        error_html = f'<div class="form-error-message">{error}</div>' if error else ""
        helper_html = f'<div class="form-helper">{helper}</div>' if helper else ""
        return mark_safe(
            f'<div class="form-group">{label_html}{content}{error_html}{helper_html}</div>'
        )


class TimelineHandler:
    def render(self, args, content, context):
        return mark_safe(f'<div class="timeline">{content}</div>')


class TimelineItemHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        title = conditional_escape(kw.get("title", ""))
        time = conditional_escape(kw.get("time", ""))
        time_html = f'<span class="timeline-time">{time}</span>' if time else ""
        return mark_safe(
            f'<div class="timeline-item">'
            f'<div class="timeline-marker"></div>'
            f'<div class="timeline-content">'
            f'<div class="timeline-title">{title}{time_html}</div>'
            f'<div class="timeline-body">{content}</div>'
            f"</div>"
            f"</div>"
        )


# ---------------------------------------------------------------------------
# Inline handlers (no children — e.g. {% spinner %}, {% dj_button %})
# ---------------------------------------------------------------------------


class ToastContainerHandler:
    ALLOWED_TYPES = {"info", "success", "warning", "error"}

    def render(self, args, context):
        toasts = context.get("toasts", [])
        dismiss_event = "dismiss_toast"
        if not toasts:
            return '<div class="toast-container"></div>'
        items = []
        for t in toasts:
            if not isinstance(t, dict):
                continue
            t_type = t.get("type", "info")
            if t_type not in self.ALLOWED_TYPES:
                t_type = "info"
            t_id = conditional_escape(str(t.get("id", "")))
            t_msg = conditional_escape(t.get("message", ""))
            items.append(
                f'<div class="toast toast-{t_type}">'
                f'<span class="toast-message">{t_msg}</span>'
                f'<button class="toast-close" dj-click="{conditional_escape(dismiss_event)}" '
                f'data-value="{t_id}">&times;</button>'
                f"</div>"
            )
        return mark_safe(f'<div class="toast-container">{"".join(items)}</div>')


class TooltipHandler:
    def render(self, args, content, context):
        # Tooltip is actually a block tag - but here used inline fallback
        kw = _parse_args(args, context)
        text = conditional_escape(kw.get("text", ""))
        position = conditional_escape(kw.get("position", "top"))
        return mark_safe(
            f'<span class="tooltip-wrapper">'
            f"{content}"
            f'<span class="tooltip tooltip-{position}">{text}</span>'
            f"</span>"
        )


class ProgressHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        try:
            value = max(0, min(100, int(kw.get("value", 0))))
        except (ValueError, TypeError):
            value = 0
        label = conditional_escape(kw.get("label", ""))
        size = conditional_escape(kw.get("size", "md"))
        color = conditional_escape(kw.get("color", "primary"))
        show_label = kw.get("show_label", True)
        label_row = ""
        if label or show_label:
            label_part = f'<span class="progress-label">{label}</span>' if label else ""
            pct_part = f'<span class="progress-value">{value}%</span>' if show_label else ""
            label_row = f'<div class="progress-label-row">{label_part}{pct_part}</div>'
        track_size = {"sm": "progress-track-sm", "lg": "progress-track-lg"}.get(str(size), "")
        color_class = "" if color == "primary" else color
        return mark_safe(
            f'<div class="progress-wrapper">'
            f"{label_row}"
            f'<div class="progress-track {track_size}">'
            f'<div class="progress-bar {color_class}" style="width:{value}%"></div>'
            f"</div>"
            f"</div>"
        )


class BadgeHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        status = conditional_escape(kw.get("status", "default"))
        pulse = kw.get("pulse", False)
        pulse_cls = " badge-pulse" if pulse else ""
        return mark_safe(
            f'<span class="badge badge-{status}{pulse_cls}">'
            f'<span class="badge-dot"></span>{label}'
            f"</span>"
        )


class PaginationHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        try:
            page = int(kw.get("page", 1))
            total_pages = int(kw.get("total_pages", 1))
        except (ValueError, TypeError):
            page, total_pages = 1, 1
        prev_event = conditional_escape(kw.get("prev_event", "page_prev"))
        next_event = conditional_escape(kw.get("next_event", "page_next"))
        prev_disabled = " disabled" if page <= 1 else ""
        next_disabled = " disabled" if page >= total_pages else ""
        return mark_safe(
            f'<div class="pagination">'
            f'<button class="pagination-btn"{prev_disabled} dj-click="{prev_event}">&#8592;</button>'
            f'<span class="pagination-info">Page {page} of {total_pages}</span>'
            f'<button class="pagination-btn"{next_disabled} dj-click="{next_event}">&#8594;</button>'
            f"</div>"
        )


class AvatarHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        src = kw.get("src", "")
        alt = conditional_escape(kw.get("alt", ""))
        initials = conditional_escape(kw.get("initials", "") or (alt[:2].upper() if alt else ""))
        size = conditional_escape(kw.get("size", "md"))
        status = conditional_escape(kw.get("status", ""))
        img_html = (
            f'<img class="avatar-image" src="{conditional_escape(src)}" alt="{alt}">'
            if src
            else f'<span class="avatar-initials">{initials}</span>'
        )
        status_html = (
            f'<span class="avatar-status avatar-status-{status}"></span>' if status else ""
        )
        return mark_safe(f'<div class="avatar avatar-{size}">{img_html}{status_html}</div>')


class SpinnerHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        size = conditional_escape(kw.get("size", "md"))
        color = conditional_escape(kw.get("color", "primary"))
        return mark_safe(
            f'<div class="spinner spinner-{size} spinner-{color}" role="status">'
            f'<span class="sr-only">Loading...</span>'
            f"</div>"
        )


class SkeletonHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        skel_type = kw.get("type", "text")
        try:
            lines = int(kw.get("lines", 3))
        except (ValueError, TypeError):
            lines = 3
        if skel_type == "avatar":
            return mark_safe('<div class="skeleton skeleton-avatar"></div>')
        if skel_type == "card":
            inner = "".join(
                f'<div class="skeleton skeleton-line" style="width:{w}%"></div>'
                for w in [80, 60, 90, 70][:lines]
            )
            return mark_safe(
                f'<div class="skeleton skeleton-card">'
                f'<div class="skeleton skeleton-text" style="width:50%;margin-bottom:1rem"></div>'
                f"{inner}"
                f"</div>"
            )
        if skel_type == "table":
            rows = "".join(
                '<div class="skeleton skeleton-line" style="width:100%"></div>'
                for _ in range(lines)
            )
            return mark_safe(f'<div class="skeleton-table">{rows}</div>')
        # default: text lines
        widths = [90, 75, 85, 60, 80, 70, 95]
        line_html = "".join(
            f'<div class="skeleton skeleton-line" style="width:{widths[i % len(widths)]}%"></div>'
            for i in range(lines)
        )
        return mark_safe(f'<div class="skeleton-text">{line_html}</div>')


class BreadcrumbHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        items = kw.get("items") or context.get("breadcrumb_items", [])
        if not isinstance(items, (list, tuple)):
            items = []
        parts = []
        for i, item in enumerate(items):
            if isinstance(item, dict):
                label = conditional_escape(item.get("label", ""))
                url = conditional_escape(item.get("url", ""))
                active = item.get("active", i == len(items) - 1)
            else:
                label = conditional_escape(str(item))
                url = ""
                active = i == len(items) - 1
            if active:
                parts.append(f'<span class="breadcrumb-item breadcrumb-active">{label}</span>')
            else:
                link = f'<a class="breadcrumb-link" href="{url}">{label}</a>' if url else label
                parts.append(
                    f'<span class="breadcrumb-item">{link}</span>'
                    f'<span class="breadcrumb-separator">›</span>'
                )
        return mark_safe(f'<nav class="breadcrumb">{"".join(parts)}</nav>')


class EmptyStateHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        title = conditional_escape(kw.get("title", "No items found"))
        description = conditional_escape(kw.get("description", ""))
        icon = conditional_escape(kw.get("icon", "○"))
        action_label = conditional_escape(kw.get("action_label", ""))
        action_event = conditional_escape(kw.get("action_event", ""))
        desc_html = f'<p class="empty-state-description">{description}</p>' if description else ""
        action_html = (
            f'<button class="empty-state-action btn btn-primary" dj-click="{action_event}">'
            f"{action_label}</button>"
            if action_label and action_event
            else ""
        )
        return mark_safe(
            f'<div class="empty-state">'
            f'<div class="empty-state-icon">{icon}</div>'
            f'<h3 class="empty-state-title">{title}</h3>'
            f"{desc_html}"
            f"{action_html}"
            f"</div>"
        )


class DividerHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        label = kw.get("label", "")
        vertical = kw.get("vertical", False)
        if vertical:
            return mark_safe('<div class="divider divider-vertical"></div>')
        if label:
            return mark_safe(
                f'<div class="divider-label"><span>{conditional_escape(label)}</span></div>'
            )
        return mark_safe('<hr class="divider divider-horizontal">')


class SwitchHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", "switch"))
        checked = kw.get("checked", False)
        label = conditional_escape(kw.get("label", ""))
        event = conditional_escape(kw.get("event", "toggle"))
        size = conditional_escape(kw.get("size", "md"))
        disabled = kw.get("disabled", False)
        checked_attr = " checked" if checked else ""
        disabled_attr = " disabled" if disabled else ""
        label_html = f'<span class="switch-label">{label}</span>' if label else ""
        size_cls = f" switch-{size}" if size != "md" else ""
        return mark_safe(
            f'<label class="switch-wrapper{size_cls}">'
            f'<span class="switch">'
            f'<input type="checkbox" class="switch-input" name="{name}"'
            f'{checked_attr}{disabled_attr} dj-change="{event}">'
            f'<span class="switch-track"></span>'
            f'<span class="switch-thumb"></span>'
            f"</span>"
            f"{label_html}"
            f"</label>"
        )


class StatCardHandler:
    _trend_icons = {"up": "↑", "down": "↓", "flat": "—", "": ""}

    def render(self, args, context):
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        value = conditional_escape(str(kw.get("value", "")))
        trend = conditional_escape(str(kw.get("trend", "")))
        description = conditional_escape(kw.get("description", ""))
        trend_direction = kw.get("trend_direction", "")
        icon = self._trend_icons.get(str(trend_direction), "")
        trend_html = ""
        if trend:
            td_cls = f" stat-trend-{conditional_escape(trend_direction)}" if trend_direction else ""
            trend_html = f'<span class="stat-card-trend{td_cls}">{icon} {trend}</span>'
        desc_html = f'<p class="stat-card-description">{description}</p>' if description else ""
        return mark_safe(
            f'<div class="stat-card">'
            f'<div class="stat-card-label">{label}</div>'
            f'<div class="stat-card-value stat-value-primary">{value}</div>'
            f"{trend_html}"
            f"{desc_html}"
            f"</div>"
        )


class TagChipHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        variant = conditional_escape(kw.get("variant", "default"))
        dismissible = kw.get("dismissible", False)
        event = conditional_escape(kw.get("event", "dismiss_tag"))
        size = kw.get("size", "")
        size_cls = f" tag-{conditional_escape(size)}" if size else ""
        close_html = (
            f'<button class="tag-close" dj-click="{event}">&times;</button>' if dismissible else ""
        )
        return mark_safe(f'<span class="tag tag-{variant}{size_cls}">{label}{close_html}</span>')


class StepperHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        steps = kw.get("steps") or context.get("steps", [])
        if not isinstance(steps, (list, tuple)):
            steps = []
        try:
            active = int(kw.get("active", 0))
        except (ValueError, TypeError):
            active = 0
        event = conditional_escape(kw.get("event", "set_step"))
        parts = []
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                label = conditional_escape(step.get("label", f"Step {i + 1}"))
                complete = step.get("complete", i < active)
            else:
                label = conditional_escape(str(step))
                complete = i < active
            cls = "stepper-step"
            if i == active:
                cls += " stepper-step-active"
            elif complete:
                cls += " stepper-step-complete"
            parts.append(
                f'<div class="{cls}" dj-click="{event}" data-value="{i}">'
                f'<div class="stepper-step-circle">{i + 1}</div>'
                f'<div class="stepper-step-label">{label}</div>'
                f"</div>"
            )
        return mark_safe(f'<div class="stepper">{"".join(parts)}</div>')


class DjButtonHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        variant = conditional_escape(kw.get("variant", "primary"))
        event = conditional_escape(kw.get("event", ""))
        icon = kw.get("icon", "")
        disabled = kw.get("disabled", False)
        loading = kw.get("loading", False)
        size = kw.get("size", "md")
        classes = ["btn", f"btn-{variant}"]
        if size and size != "md":
            classes.append(f"btn-{conditional_escape(size)}")
        if loading:
            classes.append("btn-loading")
        attrs = [f'class="{" ".join(classes)}"']
        if event and not loading and not disabled:
            attrs.append(f'dj-click="{event}"')
        if disabled or loading:
            attrs.append("disabled")
        spinner = '<span class="btn-spinner"></span>' if loading else ""
        icon_html = f'<span class="btn-icon">{conditional_escape(icon)}</span>' if icon else ""
        return mark_safe(
            f"<button {' '.join(attrs)}>"
            f"{spinner}{icon_html}"
            f'<span class="btn-label">{label}</span>'
            f"</button>"
        )


class DjInputHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", ""))
        value = conditional_escape(str(kw.get("value", "")))
        placeholder = conditional_escape(kw.get("placeholder", ""))
        input_type = conditional_escape(kw.get("type", "text"))
        error = conditional_escape(kw.get("error", ""))
        helper = conditional_escape(kw.get("helper", ""))
        required = kw.get("required", False)
        disabled = kw.get("disabled", False)
        event = conditional_escape(kw.get("event", name))
        input_cls = "form-input" + (" form-input-error" if error else "")
        required_attr = " required" if required else ""
        disabled_attr = " disabled" if disabled else ""
        required_span = '<span class="form-label-required">*</span>' if required else ""
        label_html = (
            f'<label class="form-label" for="{name}">{label}{required_span}</label>'
            if label
            else ""
        )
        error_html = f'<div class="form-error-message">{error}</div>' if error else ""
        helper_html = f'<div class="form-helper">{helper}</div>' if helper else ""
        return mark_safe(
            f'<div class="form-group">'
            f"{label_html}"
            f'<input type="{input_type}" id="{name}" name="{name}" class="{input_cls}" '
            f'value="{value}" placeholder="{placeholder}"{required_attr}{disabled_attr} '
            f'dj-input="{event}">'
            f"{error_html}{helper_html}"
            f"</div>"
        )


class DjSelectHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", ""))
        current = str(kw.get("value", ""))
        options = kw.get("options") or context.get(kw.get("options_var", ""), [])
        error = conditional_escape(kw.get("error", ""))
        required = kw.get("required", False)
        disabled = kw.get("disabled", False)
        event = conditional_escape(kw.get("event", name))
        if not isinstance(options, (list, tuple)):
            options = []
        option_html = []
        for opt in options:
            if isinstance(opt, dict):
                ov = conditional_escape(str(opt.get("value", "")))
                ol = conditional_escape(str(opt.get("label", ov)))
            elif isinstance(opt, (list, tuple)) and len(opt) >= 2:
                ov, ol = conditional_escape(str(opt[0])), conditional_escape(str(opt[1]))
            else:
                ov = ol = conditional_escape(str(opt))
            sel = " selected" if str(ov) == str(current) else ""
            option_html.append(f'<option value="{ov}"{sel}>{ol}</option>')
        select_cls = "form-select" + (" form-select-error" if error else "")
        required_attr = " required" if required else ""
        disabled_attr = " disabled" if disabled else ""
        required_span = '<span class="form-label-required">*</span>' if required else ""
        label_html = (
            f'<label class="form-label" for="{name}">{label}{required_span}</label>'
            if label
            else ""
        )
        error_html = f'<div class="form-error-message">{error}</div>' if error else ""
        return mark_safe(
            f'<div class="form-group">'
            f"{label_html}"
            f'<select id="{name}" name="{name}" class="{select_cls}"'
            f'{required_attr}{disabled_attr} dj-change="{event}">'
            f"{''.join(option_html)}"
            f"</select>"
            f"{error_html}"
            f"</div>"
        )


class DjTextareaHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", ""))
        value = conditional_escape(str(kw.get("value", "")))
        placeholder = conditional_escape(kw.get("placeholder", ""))
        error = conditional_escape(kw.get("error", ""))
        helper = conditional_escape(kw.get("helper", ""))
        required = kw.get("required", False)
        disabled = kw.get("disabled", False)
        event = conditional_escape(kw.get("event", name))
        try:
            rows = int(kw.get("rows", 4))
        except (ValueError, TypeError):
            rows = 4
        ta_cls = "form-textarea" + (" form-textarea-error" if error else "")
        required_attr = " required" if required else ""
        disabled_attr = " disabled" if disabled else ""
        required_span = '<span class="form-label-required">*</span>' if required else ""
        label_html = (
            f'<label class="form-label" for="{name}">{label}{required_span}</label>'
            if label
            else ""
        )
        error_html = f'<div class="form-error-message">{error}</div>' if error else ""
        helper_html = f'<div class="form-helper">{helper}</div>' if helper else ""
        return mark_safe(
            f'<div class="form-group">'
            f"{label_html}"
            f'<textarea id="{name}" name="{name}" class="{ta_cls}" rows="{rows}" '
            f'placeholder="{placeholder}"{required_attr}{disabled_attr} '
            f'dj-input="{event}">{value}</textarea>'
            f"{error_html}{helper_html}"
            f"</div>"
        )


class DjCheckboxHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", ""))
        checked = kw.get("checked", False)
        value = conditional_escape(str(kw.get("value", "on")))
        event = conditional_escape(kw.get("event", name))
        disabled = kw.get("disabled", False)
        checked_attr = " checked" if checked else ""
        disabled_attr = " disabled" if disabled else ""
        return mark_safe(
            f'<div class="form-checkbox-wrapper">'
            f'<label class="form-checkbox-label">'
            f'<input type="checkbox" class="form-checkbox" name="{name}" value="{value}"'
            f'{checked_attr}{disabled_attr} dj-change="{event}">'
            f"{label}"
            f"</label>"
            f"</div>"
        )


class DjRadioHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", ""))
        value = conditional_escape(str(kw.get("value", "")))
        current = str(
            kw.get("current_value", "") or context.get(str(kw.get("current_value_var", "")), "")
        )
        event = conditional_escape(kw.get("event", name))
        disabled = kw.get("disabled", False)
        checked_attr = " checked" if str(value) == str(current) else ""
        disabled_attr = " disabled" if disabled else ""
        return mark_safe(
            f'<div class="form-radio-wrapper">'
            f'<label class="form-radio-label">'
            f'<input type="radio" class="form-radio" name="{name}" value="{value}"'
            f'{checked_attr}{disabled_attr} dj-change="{event}">'
            f"{label}"
            f"</label>"
            f"</div>"
        )


# Backward-compatible aliases — delegated to djust_components.utils
_format_cell = _format_cell_util
_interpolate_color_simple = interpolate_color_gradient


class DataTableHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        rows = kw.get("rows") or context.get("rows", [])
        columns = kw.get("columns") or context.get("columns", [])
        sort_by = conditional_escape(str(kw.get("sort_by", "")))
        sort_desc = kw.get("sort_desc", False)
        sort_event = conditional_escape(kw.get("sort_event", "table_sort"))

        # Phase 1 parameters (all opt-in)
        selectable = kw.get("selectable", False)
        selected_rows = kw.get("selected_rows") or []
        select_event = conditional_escape(kw.get("select_event", "table_select"))
        row_key = str(kw.get("row_key", "id"))
        search = kw.get("search", False)
        search_query = conditional_escape(str(kw.get("search_query", "")))
        search_event = conditional_escape(kw.get("search_event", "table_search"))
        try:
            search_debounce = int(kw.get("search_debounce", 300))
        except (ValueError, TypeError):
            search_debounce = 300
        filters = kw.get("filters") or {}
        filter_event = conditional_escape(kw.get("filter_event", "table_filter"))
        loading = kw.get("loading", False)
        empty_title = conditional_escape(str(kw.get("empty_title", "No data")))
        empty_description = conditional_escape(str(kw.get("empty_description", "")))
        empty_icon = conditional_escape(str(kw.get("empty_icon", "")))
        paginate = kw.get("paginate", False)
        try:
            page = int(kw.get("page", 1))
            total_pages = int(kw.get("total_pages", 1))
        except (ValueError, TypeError):
            page, total_pages = 1, 1
        page_event = conditional_escape(kw.get("page_event", "table_page"))
        striped = kw.get("striped", False)
        compact = kw.get("compact", False)

        # Phase 2 parameters (all opt-in)
        editable_columns = kw.get("editable_columns") or []
        edit_event = conditional_escape(kw.get("edit_event", "table_cell_edit"))
        resizable = kw.get("resizable", False)
        reorderable = kw.get("reorderable", False)
        reorder_event = conditional_escape(kw.get("reorder_event", "table_reorder"))
        try:
            frozen_left = int(kw.get("frozen_left", 0))
        except (ValueError, TypeError):
            frozen_left = 0
        try:
            frozen_right = int(kw.get("frozen_right", 0))
        except (ValueError, TypeError):
            frozen_right = 0
        column_visibility = kw.get("column_visibility", False)
        visibility_event = conditional_escape(kw.get("visibility_event", "table_visibility"))
        density = conditional_escape(str(kw.get("density", "comfortable")))
        density_toggle = kw.get("density_toggle", False)
        density_event = conditional_escape(kw.get("density_event", "table_density"))
        responsive_cards = kw.get("responsive_cards", False)
        editable_rows = kw.get("editable_rows", False)
        edit_row_event = conditional_escape(kw.get("edit_row_event", "table_row_edit"))
        save_row_event = conditional_escape(kw.get("save_row_event", "table_row_save"))
        cancel_row_event = conditional_escape(kw.get("cancel_row_event", "table_row_cancel"))
        editing_rows = kw.get("editing_rows") or []

        # Phase 3 parameters (all opt-in)
        expandable = kw.get("expandable", False)
        expand_event = conditional_escape(kw.get("expand_event", "table_expand"))
        expanded_rows = kw.get("expanded_rows") or []
        bulk_actions = kw.get("bulk_actions") or []
        bulk_action_event = conditional_escape(kw.get("bulk_action_event", "table_bulk_action"))
        exportable = kw.get("exportable", False)
        export_event = conditional_escape(kw.get("export_event", "table_export"))
        export_formats = kw.get("export_formats") or ["csv", "json"]
        group_by = str(kw.get("group_by", ""))
        group_toggle_event = conditional_escape(kw.get("group_toggle_event", "table_group_toggle"))
        collapsible_groups = kw.get("collapsible_groups", True)
        collapsed_groups = kw.get("collapsed_groups") or []
        keyboard_nav = kw.get("keyboard_nav", False)
        virtual_scroll = kw.get("virtual_scroll", False)
        try:
            virtual_row_height = int(kw.get("virtual_row_height", 40))
        except (ValueError, TypeError):
            virtual_row_height = 40
        try:
            virtual_buffer = int(kw.get("virtual_buffer", 5))
        except (ValueError, TypeError):
            virtual_buffer = 5
        server_mode = kw.get("server_mode", False)
        facets = kw.get("facets", False)
        facet_counts = kw.get("facet_counts") or {}
        persist_key = conditional_escape(str(kw.get("persist_key", "")))
        printable = kw.get("printable", False)
        column_stats = kw.get("column_stats") or {}

        # Phase 4 parameters (all opt-in)
        footer_aggregations = kw.get("footer_aggregations") or {}
        row_class_map = kw.get("row_class_map") or {}
        column_groups = kw.get("column_groups") or []
        row_drag = kw.get("row_drag", False)
        row_drag_event = conditional_escape(kw.get("row_drag_event", "table_row_drag"))
        copyable = kw.get("copyable", False)
        copy_event = conditional_escape(kw.get("copy_event", "table_copy"))
        copy_format = conditional_escape(str(kw.get("copy_format", "csv")))

        # Phase 5 parameters (all opt-in)
        importable = kw.get("importable", False)
        import_event = conditional_escape(kw.get("import_event", "table_import"))
        import_formats = kw.get("import_formats") or ["csv", "json"]
        import_preview_data = kw.get("import_preview_data") or []
        import_errors = kw.get("import_errors") or []
        import_pending = kw.get("import_pending", False)
        computed_columns = kw.get("computed_columns") or []
        cell_merge_key = str(kw.get("cell_merge_key", "_merge"))
        column_expressions = kw.get("column_expressions") or {}
        expression_event = conditional_escape(kw.get("expression_event", "table_expression"))
        active_expressions = kw.get("active_expressions") or {}
        conditional_formatting = kw.get("conditional_formatting") or []

        if not isinstance(rows, (list, tuple)):
            rows = []
        if not isinstance(columns, (list, tuple)):
            columns = []
        if not isinstance(selected_rows, (list, tuple)):
            selected_rows = []
        if not isinstance(filters, dict):
            filters = {}
        if not isinstance(editable_columns, (list, tuple)):
            editable_columns = []
        if not isinstance(editing_rows, (list, tuple, set)):
            editing_rows = []
        if not isinstance(expanded_rows, (list, tuple, set)):
            expanded_rows = []
        if not isinstance(bulk_actions, (list, tuple)):
            bulk_actions = []
        if not isinstance(export_formats, (list, tuple)):
            export_formats = ["csv", "json"]
        if not isinstance(collapsed_groups, (list, tuple, set)):
            collapsed_groups = []
        if not isinstance(facet_counts, dict):
            facet_counts = {}
        if not isinstance(column_stats, dict):
            column_stats = {}
        if not isinstance(footer_aggregations, dict):
            footer_aggregations = {}
        if not isinstance(row_class_map, dict):
            row_class_map = {}
        if not isinstance(column_groups, (list, tuple)):
            column_groups = []
        if not isinstance(import_formats, (list, tuple)):
            import_formats = ["csv", "json"]
        if not isinstance(import_preview_data, (list, tuple)):
            import_preview_data = []
        if not isinstance(import_errors, (list, tuple)):
            import_errors = []
        if not isinstance(computed_columns, (list, tuple)):
            computed_columns = []
        if not isinstance(column_expressions, dict):
            column_expressions = {}
        if not isinstance(active_expressions, dict):
            active_expressions = {}
        if not isinstance(conditional_formatting, (list, tuple)):
            conditional_formatting = []

        # Convert to sets for fast lookup
        selected_set = {str(v) for v in selected_rows}
        editable_col_set = set(str(c) for c in editable_columns)
        editing_row_set = {str(v) for v in editing_rows}
        expanded_set = {str(v) for v in expanded_rows}
        collapsed_group_set = {str(v) for v in collapsed_groups}
        num_cols = len(columns)

        # --- Table classes ---
        table_classes = ["data-table"]
        if striped:
            table_classes.append("data-table-striped")
        if compact or density == "compact":
            table_classes.append("data-table-compact")
        if density == "spacious":
            table_classes.append("data-table-spacious")
        table_cls = " ".join(table_classes)

        # --- Wrapper attributes ---
        wrapper_classes = ["data-table-wrapper", "data-table-container"]
        if responsive_cards:
            wrapper_classes.append("data-table-responsive")
        if printable:
            wrapper_classes.append("data-table-printable")
        wrapper_attrs = []
        if resizable:
            wrapper_attrs.append('data-resizable="true"')
        if reorderable:
            wrapper_attrs.append('data-reorderable="true"')
            wrapper_attrs.append(f'data-reorder-event="{reorder_event}"')
        if editable_columns:
            wrapper_attrs.append(f'data-edit-event="{edit_event}"')
        if column_visibility:
            wrapper_attrs.append(f'data-visibility-event="{visibility_event}"')
        if keyboard_nav:
            wrapper_attrs.append('data-keyboard-nav="true" tabindex="0"')
        if virtual_scroll:
            wrapper_attrs.append('data-virtual-scroll="true"')
            wrapper_attrs.append(f'data-virtual-row-height="{virtual_row_height}"')
            wrapper_attrs.append(f'data-virtual-buffer="{virtual_buffer}"')
        if server_mode:
            wrapper_attrs.append('data-server-mode="true"')
        if persist_key:
            wrapper_attrs.append(f'data-persist-key="{persist_key}"')
        if row_drag:
            wrapper_attrs.append('data-row-drag="true"')
            wrapper_attrs.append(f'data-row-drag-event="{row_drag_event}"')
        if copyable:
            wrapper_attrs.append('data-copyable="true"')
            wrapper_attrs.append(f'data-copy-event="{copy_event}"')
            wrapper_attrs.append(f'data-copy-format="{copy_format}"')
        if importable:
            wrapper_attrs.append('data-importable="true"')
            wrapper_attrs.append(f'data-import-event="{import_event}"')
        if column_expressions:
            wrapper_attrs.append('data-column-expressions="true"')
            wrapper_attrs.append(f'data-expression-event="{expression_event}"')
        wrapper_attrs_str = (" " + " ".join(wrapper_attrs)) if wrapper_attrs else ""

        # --- Toolbar (column visibility + density toggle) ---
        toolbar_html = ""
        toolbar_parts = []

        if column_visibility:
            vis_items = ""
            for col in columns:
                if isinstance(col, dict):
                    ckey = conditional_escape(str(col.get("key", "")))
                    clabel = conditional_escape(str(col.get("label", ckey)))
                else:
                    ckey = clabel = conditional_escape(str(col))
                vis_items += (
                    f'<label class="data-table-visibility-item">'
                    f'<input type="checkbox" checked data-col-key="{ckey}"> {clabel}'
                    f"</label>"
                )
            toolbar_parts.append(
                f'<div class="data-table-visibility-dropdown">'
                f'<button type="button" class="data-table-visibility-btn">'
                f"&#9776; Columns</button>"
                f'<div class="data-table-visibility-menu">{vis_items}</div>'
                f"</div>"
            )

        if density_toggle:

            def _dbtn(val, label):
                active = " active" if val == density else ""
                return (
                    f'<button type="button" class="data-table-density-btn{active}"'
                    f' data-density="{val}"'
                    f' dj-click="{density_event}" data-value="{val}">{label}</button>'
                )

            toolbar_parts.append(
                f'<div class="data-table-density-toggle">'
                f"{_dbtn('compact', 'Compact')}"
                f"{_dbtn('comfortable', 'Comfortable')}"
                f"{_dbtn('spacious', 'Spacious')}"
                f"</div>"
            )

        if exportable:
            export_btns = ""
            for fmt in export_formats:
                fmt_esc = conditional_escape(str(fmt))
                label = fmt_esc.upper()
                export_btns += (
                    f'<button type="button" class="data-table-export-btn"'
                    f' dj-click="{export_event}" data-value="{fmt_esc}">'
                    f"Export {label}</button>"
                )
            toolbar_parts.append(f'<div class="data-table-export">{export_btns}</div>')

        if copyable:
            toolbar_parts.append(
                f'<div class="data-table-copy">'
                f'<button type="button" class="data-table-copy-btn"'
                f' dj-click="{copy_event}" data-value="selected">'
                f"Copy</button>"
                f"</div>"
            )

        if importable:
            import_btns = ""
            for ifmt in import_formats:
                ifmt_esc = conditional_escape(str(ifmt))
                label = ifmt_esc.upper()
                import_btns += (
                    f'<button type="button" class="data-table-import-btn"'
                    f' data-import-format="{ifmt_esc}">'
                    f"Import {label}</button>"
                )
            toolbar_parts.append(
                f'<div class="data-table-import">'
                f"{import_btns}"
                f'<input type="file" class="data-table-import-file" style="display:none"'
                f' accept=".csv,.json">'
                f"</div>"
            )

        # Bulk actions bar (rendered separately, shown conditionally)
        bulk_actions_html = ""
        if bulk_actions and selected_rows:
            ba_btns = ""
            for ba in bulk_actions:
                if isinstance(ba, dict):
                    ba_key = conditional_escape(str(ba.get("key", "")))
                    ba_label = conditional_escape(str(ba.get("label", ba_key)))
                else:
                    ba_key = ba_label = conditional_escape(str(ba))
                ba_btns += (
                    f'<button type="button" class="data-table-bulk-btn"'
                    f' dj-click="{bulk_action_event}" data-value="{ba_key}">'
                    f"{ba_label}</button>"
                )
            count = len(selected_rows)
            bulk_actions_html = (
                f'<div class="data-table-bulk-bar">'
                f'<span class="data-table-bulk-count">{count} selected</span>'
                f"{ba_btns}"
                f"</div>"
            )

        if toolbar_parts:
            toolbar_html = f'<div class="data-table-toolbar">{"".join(toolbar_parts)}</div>'

        # --- Search bar ---
        search_html = ""
        if search:
            search_html = (
                f'<div class="data-table-search">'
                f'<input type="text" role="searchbox" aria-label="Search table"'
                f' class="table-search" placeholder="Search..."'
                f' value="{search_query}"'
                f' dj-input="{search_event}" dj-debounce="{search_debounce}">'
                f"</div>"
            )

        # --- Loading state ---
        if loading:
            skeleton_rows = "".join(
                '<div class="skeleton skeleton-line" style="width:100%"></div>' for _ in range(5)
            )
            return mark_safe(
                f'<div class="{" ".join(wrapper_classes)}" role="grid"'
                f' aria-label="Data table" aria-busy="true"{wrapper_attrs_str}>'
                f"{toolbar_html}"
                f"{search_html}"
                f'<div class="data-table-loading skeleton-table">'
                f"{skeleton_rows}"
                f"</div>"
                f"</div>"
            )

        # --- Header cells ---
        has_filters = any(isinstance(col, dict) and col.get("filterable", False) for col in columns)
        header_cells = []
        filter_cells = []
        for col_idx, col in enumerate(columns):
            if isinstance(col, dict):
                key = conditional_escape(str(col.get("key", "")))
                col_label = conditional_escape(str(col.get("label", key)))
                sortable = col.get("sortable", True)
                filterable = col.get("filterable", False)
                filter_type = col.get("filter_type", "text")
                filter_options = col.get("filter_options", [])
                width = col.get("width", "")
            else:
                key = col_label = conditional_escape(str(col))
                sortable = True
                filterable = False
                filter_type = "text"
                filter_options = []
                width = ""

            # Frozen / pinned column class
            frozen_cls = ""
            pinned = col.get("pinned", "") if isinstance(col, dict) else ""
            if pinned == "left":
                frozen_cls = " data-table-pinned-left"
            elif pinned == "right":
                frozen_cls = " data-table-pinned-right"
            elif frozen_left > 0 and col_idx < frozen_left:
                frozen_cls = " data-table-frozen-left"
            elif frozen_right > 0 and col_idx >= (num_cols - frozen_right):
                frozen_cls = " data-table-frozen-right"

            # Width style
            width_attr = f' style="width:{conditional_escape(width)}"' if width else ""

            # Resize / reorder attributes
            extra_attrs = ""
            if resizable:
                extra_attrs += ' data-resizable="true"'
            if reorderable:
                extra_attrs += f' draggable="true" data-col-key="{key}"'
            elif column_visibility:
                extra_attrs += f' data-col-key="{key}"'

            # Sort state
            if sortable:
                active = " active" if key == sort_by else ""
                if key == sort_by:
                    arrow = " &#8595;" if sort_desc else " &#8593;"
                    aria_sort = "descending" if sort_desc else "ascending"
                else:
                    arrow = ""
                    aria_sort = "none"
                header_cells.append(
                    f'<th class="sortable{active}{frozen_cls}" role="columnheader"'
                    f' aria-sort="{aria_sort}"'
                    f' dj-click="{sort_event}" data-value="{key}"{width_attr}{extra_attrs}>'
                    f"{col_label}{arrow}</th>"
                )
            else:
                header_cells.append(
                    f'<th class="{frozen_cls.strip()}" role="columnheader"{width_attr}{extra_attrs}>'
                    f"{col_label}</th>"
                )

            # Filter cell
            if has_filters:
                frozen_f = f' class="{frozen_cls.strip()}"' if frozen_cls else ""
                if filterable:
                    filter_val = conditional_escape(str(filters.get(key, "")))
                    if filter_type == "select":
                        opts_html = '<option value="">All</option>'
                        for opt in filter_options:
                            if isinstance(opt, dict):
                                opt_val = conditional_escape(str(opt.get("value", "")))
                                opt_label = conditional_escape(str(opt.get("label", opt_val)))
                            else:
                                opt_val = opt_label = conditional_escape(str(opt))
                            selected = " selected" if opt_val == filter_val else ""
                            opts_html += f'<option value="{opt_val}"{selected}>{opt_label}</option>'
                        filter_cells.append(
                            f'<th{frozen_f}><select class="data-table-filter"'
                            f' aria-label="Filter {col_label}"'
                            f' dj-input="{filter_event}" data-column="{key}">'
                            f"{opts_html}"
                            f"</select></th>"
                        )
                    else:
                        filter_cells.append(
                            f'<th{frozen_f}><input type="text" class="data-table-filter"'
                            f' aria-label="Filter {col_label}"'
                            f' placeholder="Filter..."'
                            f' value="{filter_val}"'
                            f' dj-input="{filter_event}" data-column="{key}">'
                            f"</th>"
                        )
                else:
                    filter_cells.append(f"<th{frozen_f}></th>")

        # Phase 5: Append computed column headers
        if computed_columns:
            for cc in computed_columns:
                if not isinstance(cc, dict):
                    continue
                cc_key = conditional_escape(str(cc.get("key", "")))
                cc_label = conditional_escape(str(cc.get("label", cc_key)))
                header_cells.append(
                    f'<th class="data-table-computed-header" role="columnheader"'
                    f' data-col-key="{cc_key}">{cc_label}</th>'
                )
                if has_filters:
                    filter_cells.append("<th></th>")

        # Phase 5: Expression filter row (separate from regular filters)
        expression_cells = []
        if column_expressions:
            for col in columns:
                if isinstance(col, dict):
                    key = col.get("key", "")
                else:
                    key = str(col)
                key_esc = conditional_escape(str(key))
                if key in column_expressions:
                    expr_val = conditional_escape(str(active_expressions.get(key, "")))
                    placeholder = conditional_escape(
                        str(column_expressions.get(key, "Expression..."))
                    )
                    expression_cells.append(
                        f'<th><input type="text" class="data-table-expression"'
                        f' aria-label="Expression filter {key_esc}"'
                        f' placeholder="{placeholder}"'
                        f' value="{expr_val}"'
                        f' data-column="{key_esc}"'
                        f' dj-input="{expression_event}"'
                        f' dj-debounce="500">'
                        f"</th>"
                    )
                else:
                    expression_cells.append("<th></th>")
            if computed_columns:
                for _ in computed_columns:
                    expression_cells.append("<th></th>")

        # Prepend expand column
        if expandable:
            header_cells.insert(0, '<th class="data-table-expand-col" role="columnheader"></th>')
            if has_filters:
                filter_cells.insert(0, "<th></th>")
            if expression_cells:
                expression_cells.insert(0, "<th></th>")

        # Prepend selection column
        if selectable:
            header_cells.insert(
                0,
                f'<th><input type="checkbox" class="data-table-select-all"'
                f' aria-label="Select all rows"'
                f' dj-click="{select_event}" data-value="__all__"></th>',
            )
            if has_filters:
                filter_cells.insert(0, "<th></th>")
            if expression_cells:
                expression_cells.insert(0, "<th></th>")

        # Prepend drag handle column
        if row_drag:
            header_cells.insert(0, '<th class="data-table-drag-col" role="columnheader"></th>')
            if has_filters:
                filter_cells.insert(0, "<th></th>")
            if expression_cells:
                expression_cells.insert(0, "<th></th>")

        # Append actions column header for editable rows
        if editable_rows:
            header_cells.append('<th role="columnheader">Actions</th>')
            if has_filters:
                filter_cells.append("<th></th>")
            if expression_cells:
                expression_cells.append("<th></th>")

        # --- Multi-level column group header row ---
        group_header_row = ""
        if column_groups:
            group_cells = []
            if expandable:
                group_cells.append('<th rowspan="2"></th>')
            if selectable:
                group_cells.append('<th rowspan="2"></th>')
            # Build a mapping of col_key -> group info
            grouped_keys = set()
            for grp in column_groups:
                if isinstance(grp, dict):
                    grp_cols = grp.get("columns", [])
                    grouped_keys.update(grp_cols)

            col_idx = 0
            while col_idx < len(columns):
                col = columns[col_idx]
                col_key = col.get("key", col) if isinstance(col, dict) else str(col)
                # Check if this col starts a group
                found_group = None
                for grp in column_groups:
                    if isinstance(grp, dict):
                        grp_cols = grp.get("columns", [])
                        if grp_cols and grp_cols[0] == col_key:
                            found_group = grp
                            break
                if found_group:
                    grp_label = conditional_escape(str(found_group.get("label", "")))
                    span = len(found_group.get("columns", []))
                    group_cells.append(
                        f'<th class="data-table-column-group" colspan="{span}">{grp_label}</th>'
                    )
                    col_idx += span
                elif col_key not in grouped_keys:
                    group_cells.append('<th rowspan="2"></th>')
                    col_idx += 1
                else:
                    col_idx += 1
            if editable_rows:
                group_cells.append('<th rowspan="2"></th>')
            if row_drag:
                group_cells.append('<th rowspan="2"></th>')
            group_header_row = (
                f'<tr class="data-table-group-header-row">{"".join(group_cells)}</tr>'
            )

        # --- Header rows ---
        thead_rows = ""
        if group_header_row:
            thead_rows += group_header_row
        thead_rows += f"<tr>{''.join(header_cells)}</tr>"
        if has_filters:
            thead_rows += f"<tr>{''.join(filter_cells)}</tr>"
        if expression_cells:
            thead_rows += f'<tr class="data-table-expression-row">{"".join(expression_cells)}</tr>'

        # --- Total columns (for colspan calculations) ---
        num_computed = (
            len([cc for cc in computed_columns if isinstance(cc, dict)]) if computed_columns else 0
        )
        total_cols = (
            num_cols
            + num_computed
            + (1 if selectable else 0)
            + (1 if editable_rows else 0)
            + (1 if expandable else 0)
            + (1 if row_drag else 0)
        )

        # --- Helper: render a single row ---
        def _render_row(row):
            if not isinstance(row, dict):
                return ""
            row_id = str(row.get(row_key, ""))
            is_selected = row_id in selected_set
            is_editing = row_id in editing_row_set
            is_expanded = row_id in expanded_set
            row_attrs = ""
            row_classes = []

            if is_editing:
                row_classes.append("data-table-row-editing")
            if is_expanded:
                row_classes.append("data-table-row-expanded")
            # Phase 4: conditional row styling via row_class_map
            if row_class_map:
                for rcm_col, rcm_map in row_class_map.items():
                    if isinstance(rcm_map, dict):
                        rcm_val = str(row.get(rcm_col, ""))
                        if rcm_val in rcm_map:
                            row_classes.append(conditional_escape(str(rcm_map[rcm_val])))
            row_attrs += f' data-row-key="{conditional_escape(row_id)}"'

            cells = ""
            # Drag handle cell
            if row_drag:
                cells += (
                    '<td class="data-table-drag-handle">'
                    '<span class="data-table-grip" aria-label="Drag to reorder"'
                    ' draggable="true">&#9776;</span>'
                    "</td>"
                )

            # Expand toggle cell
            if expandable:
                exp_icon = "&#9660;" if is_expanded else "&#9654;"
                cells += (
                    f'<td class="data-table-expand-toggle">'
                    f'<button type="button" class="data-table-expand-btn"'
                    f' aria-label="Expand row" aria-expanded="{"true" if is_expanded else "false"}"'
                    f' dj-click="{expand_event}"'
                    f' data-value="{conditional_escape(row_id)}">{exp_icon}</button>'
                    f"</td>"
                )

            if selectable:
                checked = " checked" if is_selected else ""
                cells += (
                    f'<td><input type="checkbox" class="data-table-checkbox"'
                    f' aria-label="Select row"'
                    f"{checked}"
                    f' dj-click="{select_event}"'
                    f' data-value="{conditional_escape(row_id)}"></td>'
                )

            # Phase 5: build list of all renderable columns (base + computed)
            all_columns = list(columns)
            if computed_columns:
                all_columns = list(columns) + [
                    cc for cc in computed_columns if isinstance(cc, dict)
                ]

            # Phase 5: cell merge data for this row
            merge_data = row.get(cell_merge_key, {}) if isinstance(row, dict) else {}
            if not isinstance(merge_data, dict):
                merge_data = {}
            skip_cols = set()  # column indices to skip (merged into previous cell)

            # Pre-compute which columns are merged away
            for col_idx, col in enumerate(all_columns):
                col_k = col.get("key", col) if isinstance(col, dict) else col
                col_k_str = str(col_k)
                colspan = merge_data.get(col_k_str, 1)
                if isinstance(colspan, int) and colspan > 1:
                    for offset in range(1, colspan):
                        if col_idx + offset < len(all_columns):
                            skip_cols.add(col_idx + offset)

            for col_idx, col in enumerate(all_columns):
                if col_idx in skip_cols:
                    continue  # This cell is merged into a previous colspan
                col_k = col.get("key", col) if isinstance(col, dict) else col
                col_k_str = str(col_k)
                raw_val = row.get(col_k_str, "")
                # Phase 4: column type formatting
                col_type = col.get("type", "") if isinstance(col, dict) else ""
                if col_type and raw_val is not None and raw_val != "":
                    cell_val = conditional_escape(_format_cell(raw_val, col))
                else:
                    cell_val = conditional_escape(str(raw_val))
                col_label_for_card = ""
                if responsive_cards and isinstance(col, dict):
                    col_label_for_card = conditional_escape(str(col.get("label", col_k_str)))

                # Frozen / pinned class for td
                td_classes = []
                is_computed = col in computed_columns if computed_columns else False
                if is_computed:
                    td_classes.append("data-table-computed")
                if isinstance(col, dict) and col.get("pinned") == "left":
                    td_classes.append("data-table-pinned-left")
                elif isinstance(col, dict) and col.get("pinned") == "right":
                    td_classes.append("data-table-pinned-right")
                elif frozen_left > 0 and col_idx < frozen_left:
                    td_classes.append("data-table-frozen-left")
                elif frozen_right > 0 and col_idx >= (num_cols - frozen_right):
                    td_classes.append("data-table-frozen-right")
                # Phase 4: column type class
                if col_type:
                    td_classes.append(f"data-table-type-{conditional_escape(col_type)}")
                # Phase 4: cell_class from column config
                cell_class = col.get("cell_class", "") if isinstance(col, dict) else ""
                if cell_class:
                    # cell_class can be a string or a dict {value: class}
                    if isinstance(cell_class, dict):
                        cc_val = str(raw_val)
                        if cc_val in cell_class:
                            td_classes.append(conditional_escape(str(cell_class[cc_val])))
                    elif isinstance(cell_class, str):
                        td_classes.append(conditional_escape(cell_class))

                # Phase 5: conditional formatting
                cf_html = ""
                if conditional_formatting and raw_val is not None and raw_val != "":
                    for cf_preset in conditional_formatting:
                        if not isinstance(cf_preset, dict):
                            continue
                        if cf_preset.get("column") != col_k_str:
                            continue
                        try:
                            cf_num = float(raw_val)
                        except (ValueError, TypeError):
                            break
                        cf_type = cf_preset.get("type", "")
                        cf_min = float(cf_preset.get("min", 0))
                        cf_max = float(cf_preset.get("max", 100))
                        cf_span = cf_max - cf_min if cf_max != cf_min else 1
                        if cf_type == "data_bar":
                            cf_pct = max(0, min(100, ((cf_num - cf_min) / cf_span) * 100))
                            td_classes.append("data-table-cf-data-bar")
                            cf_html = (
                                f'<div class="data-table-data-bar"'
                                f' style="width:{cf_pct:.1f}%"></div>'
                            )
                        elif cf_type == "color_scale":
                            cf_ratio = max(0.0, min(1.0, (cf_num - cf_min) / cf_span))
                            cf_colors = cf_preset.get("colors", ["#ff0000", "#00ff00"])
                            cf_color = _interpolate_color_simple(cf_colors, cf_ratio)
                            td_classes.append("data-table-cf-color-scale")
                            cf_html = f' style="background-color:{conditional_escape(cf_color)}"'
                        elif cf_type == "icon_set":
                            cf_icons = cf_preset.get("icons", ["\u25bc", "\u25b6", "\u25b2"])
                            cf_thresholds = cf_preset.get("thresholds", [])
                            cf_icon = cf_icons[-1] if cf_icons else ""
                            for ci, ct in enumerate(cf_thresholds):
                                if cf_num < float(ct):
                                    cf_icon = cf_icons[ci] if ci < len(cf_icons) else cf_icons[-1]
                                    break
                            td_classes.append("data-table-cf-icon-set")
                            cf_html = f'<span class="data-table-cf-icon">{conditional_escape(cf_icon)}</span> '
                        break  # only first matching preset per column

                # Phase 5: cell merge colspan
                colspan_attr = ""
                colspan_val = merge_data.get(col_k_str, 1)
                if isinstance(colspan_val, int) and colspan_val > 1:
                    colspan_attr = f' colspan="{colspan_val}"'
                    td_classes.append("data-table-merged")

                td_cls_str = f' class="{" ".join(td_classes)}"' if td_classes else ""

                # Responsive card data-label
                label_attr = (
                    f' data-label="{col_label_for_card}"'
                    if responsive_cards and col_label_for_card
                    else ""
                )

                # Cell renderer
                cell_template = col.get("cell_template", "") if isinstance(col, dict) else ""
                if cell_template:
                    cell_tpl_esc = conditional_escape(str(cell_template))
                    cell_val = (
                        f'<span class="cell-renderer cell-renderer-{cell_tpl_esc}"'
                        f' data-value="{cell_val}">{cell_val}</span>'
                    )

                # Apply conditional formatting to cell content
                if cf_html and "data-table-cf-data-bar" in " ".join(td_classes):
                    cell_val = f"{cell_val}{cf_html}"
                elif cf_html and "data-table-cf-icon-set" in " ".join(td_classes):
                    cell_val = f"{cf_html}{cell_val}"
                # color_scale cf_html is a style attr, handled below

                # color_scale extra style
                extra_style = ""
                if cf_html and "data-table-cf-color-scale" in " ".join(td_classes):
                    extra_style = cf_html  # this is the style="..." attr

                # Editable cell (inline editing)
                is_col_editable = col_k_str in editable_col_set

                # Editable row mode: all cells become inputs when row is editing
                if editable_rows and is_editing:
                    raw_val = conditional_escape(str(row.get(col_k_str, "")))
                    cells += (
                        f"<td{td_cls_str}{label_attr}{colspan_attr}>"
                        f'<input type="text" value="{raw_val}"'
                        f' name="{conditional_escape(col_k_str)}"'
                        f' aria-label="Edit {conditional_escape(col_k_str)}">'
                        f"</td>"
                    )
                elif is_col_editable:
                    cells += (
                        f'<td data-editable="true"'
                        f' data-col-key="{conditional_escape(col_k_str)}"'
                        f"{td_cls_str}{label_attr}{colspan_attr}>"
                        f"{cell_val}</td>"
                    )
                else:
                    cells += (
                        f"<td{td_cls_str}{label_attr}{colspan_attr}{extra_style}>{cell_val}</td>"
                    )

            # Actions column for editable rows
            if editable_rows:
                if is_editing:
                    cells += (
                        f'<td class="data-table-row-actions">'
                        f'<button class="save-btn"'
                        f' dj-click="{save_row_event}"'
                        f' data-value="{conditional_escape(row_id)}">Save</button>'
                        f' <button class="cancel-btn"'
                        f' dj-click="{cancel_row_event}"'
                        f' data-value="{conditional_escape(row_id)}">Cancel</button>'
                        f"</td>"
                    )
                else:
                    cells += (
                        f'<td class="data-table-row-actions">'
                        f'<button dj-click="{edit_row_event}"'
                        f' data-value="{conditional_escape(row_id)}">Edit</button>'
                        f"</td>"
                    )

            # Row element
            result = ""
            if selectable:
                sel_attr = "true" if is_selected else "false"
                row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ""
                result += f'<tr aria-selected="{sel_attr}"{row_cls}{row_attrs}>{cells}</tr>'
            else:
                row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ""
                result += f"<tr{row_cls}{row_attrs}>{cells}</tr>"

            # Expansion detail row
            if expandable and is_expanded:
                result += (
                    f'<tr class="data-table-detail-row">'
                    f'<td colspan="{total_cols}" class="data-table-detail-cell">'
                    f'<div class="data-table-detail-content"'
                    f' data-row-key="{conditional_escape(row_id)}"></div>'
                    f"</td></tr>"
                )

            return result

        # --- Body rows ---
        body_rows = []
        if rows:
            if group_by:
                # Group rows by column value
                groups = {}
                group_order = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    val = str(row.get(group_by, ""))
                    if val not in groups:
                        groups[val] = []
                        group_order.append(val)
                    groups[val].append(row)

                for gval in group_order:
                    g_esc = conditional_escape(gval)
                    is_collapsed = gval in collapsed_group_set
                    toggle_attr = ""
                    if collapsible_groups:
                        toggle_attr = f' dj-click="{group_toggle_event}" data-value="{g_esc}"'
                    collapse_icon = "&#9654;" if is_collapsed else "&#9660;"
                    group_cls = "data-table-group-header"
                    if is_collapsed:
                        group_cls += " data-table-group-collapsed"
                    body_rows.append(
                        f'<tr class="{group_cls}">'
                        f'<td colspan="{total_cols}" class="data-table-group-cell">'
                        f'<button type="button" class="data-table-group-toggle"'
                        f"{toggle_attr}>{collapse_icon}</button>"
                        f' <span class="data-table-group-label">{g_esc}</span>'
                        f' <span class="data-table-group-count">({len(groups[gval])})</span>'
                        f"</td></tr>"
                    )
                    if not is_collapsed:
                        for row in groups[gval]:
                            body_rows.append(_render_row(row))
            else:
                for row in rows:
                    body_rows.append(_render_row(row))
            tbody_html = "".join(body_rows)
        else:
            # Empty state
            col_span = total_cols
            icon_html = (
                f'<div class="data-table-empty-icon">{empty_icon}</div>' if empty_icon else ""
            )
            desc_html = (
                f'<p class="data-table-empty-description">{empty_description}</p>'
                if empty_description
                else ""
            )
            tbody_html = (
                f'<tr><td colspan="{col_span}">'
                f'<div class="data-table-empty" role="status">'
                f"{icon_html}"
                f'<h3 class="data-table-empty-title">{empty_title}</h3>'
                f"{desc_html}"
                f"</div>"
                f"</td></tr>"
            )

        # --- Stats footer ---
        tfoot_html = ""
        has_stats = any(isinstance(col, dict) and col.get("stats", False) for col in columns)
        if has_stats and column_stats:
            stat_cells = []
            if expandable:
                stat_cells.append("<td></td>")
            if selectable:
                stat_cells.append("<td></td>")
            for col in columns:
                if isinstance(col, dict) and col.get("stats", False):
                    key = col.get("key", "")
                    s = column_stats.get(key, {})
                    if s and s.get("count", 0) > 0:
                        stat_cells.append(
                            f'<td class="data-table-stats-cell">'
                            f'<span class="data-table-stat" title="Min">{s.get("min", "")}</span>'
                            f'<span class="data-table-stat" title="Max">{s.get("max", "")}</span>'
                            f'<span class="data-table-stat" title="Avg">{s.get("avg", "")}</span>'
                            f"</td>"
                        )
                    else:
                        stat_cells.append('<td class="data-table-stats-cell">-</td>')
                else:
                    stat_cells.append("<td></td>")
            if editable_rows:
                stat_cells.append("<td></td>")
            tfoot_html = (
                f'<tfoot><tr class="data-table-stats-row">{"".join(stat_cells)}</tr></tfoot>'
            )

        # --- Phase 4: Footer aggregation row ---
        if footer_aggregations and rows:
            agg_cells = []
            if row_drag:
                agg_cells.append("<td></td>")
            if expandable:
                agg_cells.append("<td></td>")
            if selectable:
                agg_cells.append("<td></td>")
            for col in columns:
                col_k = col.get("key", col) if isinstance(col, dict) else str(col)
                col_k_str = str(col_k)
                agg_type = footer_aggregations.get(col_k_str, "")
                if agg_type:
                    # Compute aggregation
                    vals = []
                    for r in rows:
                        if isinstance(r, dict):
                            v = r.get(col_k_str)
                            if v is not None:
                                try:
                                    vals.append(float(v))
                                except (ValueError, TypeError):
                                    pass
                    agg_val = ""
                    if vals:
                        if agg_type == "sum":
                            agg_val = sum(vals)
                        elif agg_type == "avg":
                            agg_val = round(sum(vals) / len(vals), 2)
                        elif agg_type == "count":
                            agg_val = len(vals)
                        elif agg_type == "min":
                            agg_val = min(vals)
                        elif agg_type == "max":
                            agg_val = max(vals)
                    agg_label = conditional_escape(str(agg_type).capitalize())
                    agg_cells.append(
                        f'<td class="data-table-footer-agg" data-agg-type="{conditional_escape(str(agg_type))}">'
                        f'<span class="data-table-agg-label">{agg_label}:</span> '
                        f'<span class="data-table-agg-value">{agg_val}</span>'
                        f"</td>"
                    )
                else:
                    agg_cells.append("<td></td>")
            if editable_rows:
                agg_cells.append("<td></td>")
            agg_row = f'<tr class="data-table-footer-row">{"".join(agg_cells)}</tr>'
            if tfoot_html:
                # Append inside existing tfoot
                tfoot_html = tfoot_html.replace("</tfoot>", f"{agg_row}</tfoot>")
            else:
                tfoot_html = f"<tfoot>{agg_row}</tfoot>"

        # --- Pagination ---
        pagination_html = ""
        if paginate and total_pages > 1:
            prev_disabled = " disabled" if page <= 1 else ""
            next_disabled = " disabled" if page >= total_pages else ""
            prev_page = max(1, page - 1)
            next_page = min(total_pages, page + 1)
            pagination_html = (
                f'<div class="data-table-pagination" role="navigation"'
                f' aria-label="Table pagination">'
                f'<button class="pagination-btn"{prev_disabled}'
                f' dj-click="{page_event}" data-value="{prev_page}">&#8592;</button>'
                f'<span class="pagination-info">Page {page} of {total_pages}</span>'
                f'<button class="pagination-btn"{next_disabled}'
                f' dj-click="{page_event}" data-value="{next_page}">&#8594;</button>'
                f"</div>"
            )

        # --- Hidden triggers for JS events ---
        triggers_html = ""
        if reorderable:
            triggers_html += (
                f'<button class="data-table-reorder-trigger" style="display:none"'
                f' dj-click="{reorder_event}"></button>'
            )
        if editable_columns:
            triggers_html += (
                f'<button class="data-table-edit-trigger" style="display:none"'
                f' dj-click="{edit_event}"></button>'
            )
        if column_visibility:
            triggers_html += (
                f'<button class="data-table-visibility-trigger" style="display:none"'
                f' dj-click="{visibility_event}"></button>'
            )
        if row_drag:
            triggers_html += (
                f'<button class="data-table-drag-trigger" style="display:none"'
                f' dj-click="{row_drag_event}"></button>'
            )
        if copyable:
            triggers_html += (
                f'<button class="data-table-copy-trigger" style="display:none"'
                f' dj-click="{copy_event}"></button>'
            )
        if importable:
            triggers_html += (
                f'<button class="data-table-import-trigger" style="display:none"'
                f' dj-click="{import_event}"></button>'
            )
        if column_expressions:
            triggers_html += (
                f'<button class="data-table-expression-trigger" style="display:none"'
                f' dj-click="{expression_event}"></button>'
            )

        # --- Phase 5: Import preview / errors ---
        import_html = ""
        if importable:
            if import_errors:
                err_items = "".join(f"<li>{conditional_escape(str(e))}</li>" for e in import_errors)
                import_html += (
                    f'<div class="data-table-import-errors" role="alert"><ul>{err_items}</ul></div>'
                )
            if import_pending and import_preview_data:
                preview_count = len(import_preview_data)
                import_html += (
                    f'<div class="data-table-import-preview">'
                    f'<span class="data-table-import-preview-count">'
                    f"{preview_count} rows ready to import</span>"
                    f'<button type="button" class="data-table-import-confirm"'
                    f' dj-click="{import_event}"'
                    f" data-value='{{\"confirm\":true}}'>Confirm Import</button>"
                    f'<button type="button" class="data-table-import-cancel"'
                    f' dj-click="{import_event}"'
                    f" data-value='{{\"cancel\":true}}'>Cancel</button>"
                    f"</div>"
                )

        # --- Scrollable wrapper for frozen columns ---
        scroll_open = ""
        scroll_close = ""
        if frozen_left > 0 or frozen_right > 0:
            scroll_open = '<div class="data-table-scroll">'
            scroll_close = "</div>"

        # --- Facet counts display (appended to filter cells) ---
        # Facets are shown as counts next to filter options — handled via facet_counts data attr
        facet_attr = ""
        if facets and facet_counts:
            facet_attr = f" data-facet-counts='{conditional_escape(_json.dumps(facet_counts))}'"

        return mark_safe(
            f'<div class="{" ".join(wrapper_classes)}" role="grid"'
            f' aria-label="Data table"{wrapper_attrs_str}{facet_attr}>'
            f"{toolbar_html}"
            f"{bulk_actions_html}"
            f"{search_html}"
            f"{import_html}"
            f"{scroll_open}"
            f'<table class="{table_cls}">'
            f"<thead>{thead_rows}</thead>"
            f"<tbody>{tbody_html}</tbody>"
            f"{tfoot_html}"
            f"</table>"
            f"{scroll_close}"
            f"{pagination_html}"
            f"{triggers_html}"
            f"</div>"
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

# Inline handlers: (tag_name, handler_instance)
INLINE_HANDLERS = [
    ("toast_container", ToastContainerHandler()),
    ("progress", ProgressHandler()),
    ("badge", BadgeHandler()),
    ("pagination", PaginationHandler()),
    ("avatar", AvatarHandler()),
    ("data_table", DataTableHandler()),
    ("spinner", SpinnerHandler()),
    ("skeleton", SkeletonHandler()),
    ("breadcrumb", BreadcrumbHandler()),
    ("empty_state", EmptyStateHandler()),
    ("dj_divider", DividerHandler()),
    ("switch", SwitchHandler()),
    ("stat_card", StatCardHandler()),
    ("dj_tag", TagChipHandler()),
    ("stepper", StepperHandler()),
    ("dj_button", DjButtonHandler()),
    ("dj_input", DjInputHandler()),
    ("dj_select", DjSelectHandler()),
    ("dj_textarea", DjTextareaHandler()),
    ("dj_checkbox", DjCheckboxHandler()),
    ("dj_radio", DjRadioHandler()),
]

# Block handlers: (tag_name, end_tag_name, handler_instance)
BLOCK_HANDLERS = [
    ("modal", "endmodal", ModalHandler()),
    ("card", "endcard", CardHandler()),
    ("tabs", "endtabs", TabsHandler()),
    ("accordion", "endaccordion", AccordionHandler()),
    ("accordion_item", "endaccordion_item", AccordionItemHandler()),
    ("dropdown", "enddropdown", DropdownHandler()),
    ("alert", "endalert", AlertHandler()),
    ("form_group", "endform_group", FormGroupHandler()),
    ("timeline", "endtimeline", TimelineHandler()),
    ("timeline_item", "endtimeline_item", TimelineItemHandler()),
    ("tooltip", "endtooltip", TooltipHandler()),
]


def register_with_rust_engine():
    """Register all component tag handlers with the Rust template engine.

    Called from DjustComponentsConfig.ready(). Safe to call multiple times
    (subsequent calls overwrite existing registrations).
    """
    try:
        from djust._rust import (  # type: ignore[import]
            register_block_tag_handler,
            register_tag_handler,
        )
    except ImportError:
        # djust not installed — skip silently (components still work via
        # Django template engine with {% load djust_components %})
        return

    for tag_name, handler in INLINE_HANDLERS:
        register_tag_handler(tag_name, handler)

    for tag_name, end_tag, handler in BLOCK_HANDLERS:
        register_block_tag_handler(tag_name, end_tag, handler)

    # Component system (v0.5.0): {% call %}, {% component %}, {% slot %},
    # {% render_slot %}. Registered here so the tags are available without
    # a separate {% load %} in user templates.
    from .function_component import (
        CallTagHandler,
        RenderSlotTagHandler,
        SlotTagHandler,
    )

    _call_handler = CallTagHandler()
    register_block_tag_handler("call", "endcall", _call_handler)
    register_block_tag_handler("component", "endcomponent", _call_handler)
    register_block_tag_handler("slot", "endslot", SlotTagHandler())
    register_tag_handler("render_slot", RenderSlotTagHandler())

    # Async rendering (v0.5.0): {% dj_suspense await="..." fallback="..." %}
    # wraps sections dependent on AsyncResult assigns. See
    # djust/components/suspense.py for semantics.
    from .suspense import SuspenseTagHandler

    register_block_tag_handler("dj_suspense", "enddj_suspense", SuspenseTagHandler())


# ===========================================================================
# TIER 2 REMAINING + TIER 3 HANDLERS
# ===========================================================================


class CodeBlockHandler:
    """Inline handler for {% code_block code=... language=... %}"""

    def render(self, args, context):
        from djust.components.templatetags.djust_components import code_block as _cb

        kwargs = _parse_args(args, context)
        code = kwargs.get("code", "")
        language = kwargs.get("language", "")
        filename = kwargs.get("filename", "")
        highlight = kwargs.get("highlight", True)
        theme = kwargs.get("theme", "github-dark")
        return str(
            _cb(code=code, language=language, filename=filename, highlight=highlight, theme=theme)
        )


class ComboboxHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import combobox as _cb

        kwargs = _parse_args(args, context)
        options_val = kwargs.get("options", "")
        if isinstance(options_val, list):
            options = options_val
        else:
            options = context.get(options_val, [])
        selected_val = kwargs.get("selected", None)
        if isinstance(selected_val, str) and selected_val:
            selected_val = context.get(selected_val, [])
        return str(
            _cb(
                name=kwargs.get("name", ""),
                label=kwargs.get("label", ""),
                value=kwargs.get("value", ""),
                placeholder=kwargs.get("placeholder", "Search…"),
                options=options,
                event=kwargs.get("event", ""),
                search_event=kwargs.get("search_event", ""),
                multiple=kwargs.get("multiple", False),
                selected=selected_val,
            )
        )


class RatingHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import rating as _r

        kwargs = _parse_args(args, context)
        return str(
            _r(
                value=kwargs.get("value", 0),
                max_stars=kwargs.get("max_stars", 5),
                readonly=kwargs.get("readonly", False),
                event=kwargs.get("event", "set_rating"),
                size=kwargs.get("size", "md"),
            )
        )


class CopyButtonHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import copy_button as _c

        kwargs = _parse_args(args, context)
        return str(
            _c(
                text=kwargs.get("text", ""),
                label=kwargs.get("label", "Copy"),
                variant=kwargs.get("variant", "outline"),
                size=kwargs.get("size", "sm"),
            )
        )


class KbdHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import kbd as _k

        # args is a list of strings; filter out empty ones
        keys = [a.strip("'\"") for a in args if a.strip("'\"")]
        return str(_k(*keys))


class GaugeHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import gauge as _g

        kwargs = _parse_args(args, context)
        return str(
            _g(
                value=kwargs.get("value", 0),
                max_value=kwargs.get("max_value", 100),
                label=kwargs.get("label", ""),
                color=kwargs.get("color", "primary"),
                size=kwargs.get("size", "md"),
            )
        )


class NotificationCenterHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import notification_center as _nc

        kwargs = _parse_args(args, context)
        notifs_key = kwargs.get("notifications", "notifications")
        notifs = context.get(notifs_key, []) if isinstance(notifs_key, str) else notifs_key
        unread = sum(1 for n in notifs if isinstance(n, dict) and n.get("unread"))
        return str(
            _nc(
                notifications=notifs,
                unread_count=unread,
                open_event=kwargs.get("open_event", "toggle_notifications"),
            )
        )


class TreeViewHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import tree_view as _tv

        kwargs = _parse_args(args, context)
        nodes_key = kwargs.get("nodes", "tree_nodes")
        nodes = context.get(nodes_key, []) if isinstance(nodes_key, str) else nodes_key
        return str(
            _tv(
                nodes=nodes,
                expand_event=kwargs.get("expand_event", "tree_expand"),
                select_event=kwargs.get("select_event", "tree_select"),
                selected=kwargs.get("selected", ""),
            )
        )


class ColorPickerHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import color_picker as _cp

        kwargs = _parse_args(args, context)
        return str(
            _cp(
                name=kwargs.get("name", ""),
                value=kwargs.get("value", "#3B82F6"),
                event=kwargs.get("event", ""),
                label=kwargs.get("label", ""),
            )
        )


class CarouselHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import carousel as _car

        kwargs = _parse_args(args, context)
        imgs_key = kwargs.get("images", "carousel_images")
        images = context.get(imgs_key, []) if isinstance(imgs_key, str) else imgs_key
        return str(
            _car(
                images=images,
                active=kwargs.get("active", 0),
                prev_event=kwargs.get("prev_event", "carousel_prev"),
                next_event=kwargs.get("next_event", "carousel_next"),
            )
        )


class PaletteItemHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import palette_item as _pi

        kwargs = _parse_args(args, context)
        return str(
            _pi(
                label=kwargs.get("label", ""),
                shortcut=kwargs.get("shortcut", ""),
                description=kwargs.get("description", ""),
                event=kwargs.get("event", ""),
                icon=kwargs.get("icon", ""),
            )
        )


class ContextMenuItemHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import context_menu_item as _ci

        kwargs = _parse_args(args, context)
        return str(
            _ci(
                label=kwargs.get("label", ""),
                event=kwargs.get("event", ""),
                icon=kwargs.get("icon", ""),
                danger=kwargs.get("danger", False),
                divider=kwargs.get("divider", False),
            )
        )


class PopoverHandler:
    """Block handler for {% popover trigger="..." %}...{% endpopover %}"""

    def render(self, args, content, context):
        kwargs = _parse_args(args, context)
        trigger = kwargs.get("trigger", "Click me")
        placement = kwargs.get("placement", "bottom")
        title = kwargs.get("title", "")
        from django.utils.html import conditional_escape

        e_trigger = conditional_escape(trigger)
        e_placement = conditional_escape(placement)
        title_html = (
            f'<div class="popover-title">{conditional_escape(title)}</div>' if title else ""
        )
        return mark_safe(
            f'<div class="popover-wrapper">'
            f'<button class="popover-trigger btn btn-outline btn-sm" '
            f"onclick=\"(function(el){{var p=el.parentElement;p.classList.toggle('popover-open');"
            f"document.addEventListener('click',function h(e){{if(!p.contains(e.target)){{p.classList.remove('popover-open');document.removeEventListener('click',h);}}}},true);"
            f'}})(this)" aria-expanded="false">'
            f"{e_trigger}</button>"
            f'<div class="popover popover-{e_placement}" role="tooltip">'
            f"{title_html}"
            f'<div class="popover-content">{content}</div>'
            f"</div>"
            f"</div>"
        )


class CollapsibleHandler:
    """Block handler for {% collapsible trigger="..." %}...{% endcollapsible %}"""

    def render(self, args, content, context):
        kwargs = _parse_args(args, context)
        trigger = kwargs.get("trigger", "Toggle")
        event = kwargs.get("event", "toggle_collapsible")
        open_ = kwargs.get("open", False)
        if isinstance(open_, str):
            open_ = open_.lower() not in ("false", "0", "")
        from django.utils.html import conditional_escape

        e_trigger = conditional_escape(trigger)
        e_event = conditional_escape(event)
        open_cls = " collapsible-open" if open_ else ""
        return mark_safe(
            f'<div class="collapsible{open_cls}">'
            f'<button class="collapsible-trigger" '
            f"onclick=\"(function(el){{el.closest('.collapsible').classList.toggle('collapsible-open');}})(this)\""
            f' dj-click="{e_event}">'
            f'<span class="collapsible-label">{e_trigger}</span>'
            f'<span class="collapsible-icon">▾</span>'
            f"</button>"
            f'<div class="collapsible-content">{content}</div>'
            f"</div>"
        )


class SheetHandler:
    """Block handler for {% sheet side="right" open=show_sheet %}...{% endsheet %}"""

    def render(self, args, content, context):
        kwargs = _parse_args(args, context)
        open_ = kwargs.get("is_open", kwargs.get("open", False))
        if isinstance(open_, str):
            open_ = open_.lower() not in ("false", "0", "")
        side = kwargs.get("side", "right")
        title = kwargs.get("title", "")
        close_event = kwargs.get("close_event", "close_sheet")
        from django.utils.html import conditional_escape

        e_side = conditional_escape(side)
        e_title = conditional_escape(title)
        e_close = conditional_escape(close_event)
        open_attr = ' data-open="true"' if open_ else ""
        title_html = (
            f'<div class="sheet-header">'
            f'<h3 class="sheet-title">{e_title}</h3>'
            f'<button class="sheet-close" dj-click="{e_close}">&times;</button>'
            f"</div>"
            if title
            else f'<div class="sheet-header-close">'
            f'<button class="sheet-close" dj-click="{e_close}">&times;</button>'
            f"</div>"
        )
        return mark_safe(
            f'<div class="sheet-overlay" dj-click="{e_close}"{open_attr}></div>'
            f'<div class="sheet sheet-{e_side}"{open_attr}>'
            f"{title_html}"
            f'<div class="sheet-body">{content}</div>'
            f"</div>"
        )


class CommandPaletteHandler:
    """Block handler for {% command_palette open=show_palette %}...{% endcommand_palette %}"""

    def render(self, args, content, context):
        kwargs = _parse_args(args, context)
        open_ = kwargs.get("is_open", kwargs.get("open", False))
        if isinstance(open_, str):
            open_ = open_.lower() not in ("false", "0", "")
        search_event = kwargs.get("search_event", "palette_search")
        close_event = kwargs.get("close_event", "close_palette")
        placeholder = kwargs.get("placeholder", "Search commands…")
        from django.utils.html import conditional_escape

        e_search = conditional_escape(search_event)
        e_close = conditional_escape(close_event)
        e_placeholder = conditional_escape(placeholder)
        open_attr = ' data-open="true"' if open_ else ""
        return mark_safe(
            f'<div class="palette-overlay" dj-click="{e_close}"{open_attr}></div>'
            f'<div class="palette"{open_attr}>'
            f'<div class="palette-search">'
            f'<span class="palette-search-icon">⌕</span>'
            f'<input class="palette-input" type="text" placeholder="{e_placeholder}" '
            f'dj-input="{e_search}">'
            f'<button class="palette-close" dj-click="{e_close}">Esc</button>'
            f"</div>"
            f'<div class="palette-results">{content}</div>'
            f"</div>"
        )


class ContextMenuHandler:
    """Block handler for {% context_menu label="..." %}...{% endcontext_menu %}"""

    def render(self, args, content, context):
        kwargs = _parse_args(args, context)
        label = kwargs.get("label", "Right-click here")
        from django.utils.html import conditional_escape

        e_label = conditional_escape(label)
        return mark_safe(
            f'<div class="ctx-wrapper" '
            f'oncontextmenu="(function(e,el){{e.preventDefault();'
            f"document.querySelectorAll('.ctx-menu[data-open]').forEach(function(m){{delete m.dataset.open;}});"
            f"var m=el.querySelector('.ctx-menu');"
            f"m.style.left=e.offsetX+'px';m.style.top=e.offsetY+'px';"
            f"m.dataset.open='1';"
            f"document.addEventListener('click',function h(){{delete m.dataset.open;document.removeEventListener('click',h);}},{{once:true}});"
            f'}})(event,this)">'
            f'<div class="ctx-trigger">{e_label}</div>'
            f'<div class="ctx-menu" role="menu">{content}</div>'
            f"</div>"
        )


# Extend lists with Tier 2/3 handlers (defined above, after the original lists)
INLINE_HANDLERS.extend(
    [
        ("code_block", CodeBlockHandler()),
        ("combobox", ComboboxHandler()),
        ("rating", RatingHandler()),
        ("copy_button", CopyButtonHandler()),
        ("kbd", KbdHandler()),
        ("gauge", GaugeHandler()),
        ("notification_center", NotificationCenterHandler()),
        ("tree_view", TreeViewHandler()),
        ("color_picker", ColorPickerHandler()),
        ("carousel", CarouselHandler()),
        ("palette_item", PaletteItemHandler()),
        ("context_menu_item", ContextMenuItemHandler()),
    ]
)

BLOCK_HANDLERS.extend(
    [
        ("popover", "endpopover", PopoverHandler()),
        ("collapsible", "endcollapsible", CollapsibleHandler()),
        ("sheet", "endsheet", SheetHandler()),
        ("command_palette", "endcommand_palette", CommandPaletteHandler()),
        ("context_menu", "endcontext_menu", ContextMenuHandler()),
    ]
)


# ===========================================================================
# v1.3 HANDLERS
# ===========================================================================


class DatePickerHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import date_picker as _dp

        kwargs = _parse_args(args, context)
        return str(
            _dp(
                year=kwargs.get("year"),
                month=kwargs.get("month"),
                selected=kwargs.get("selected", ""),
                prev_event=kwargs.get("prev_event", "date_prev_month"),
                next_event=kwargs.get("next_event", "date_next_month"),
                select_event=kwargs.get("select_event", "date_select"),
                name=kwargs.get("name", "date"),
                label=kwargs.get("label", ""),
                is_range=kwargs.get("is_range", kwargs.get("range", False)),
                range_start=kwargs.get("range_start", ""),
                range_end=kwargs.get("range_end", ""),
            )
        )


class FileDropzoneHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import file_dropzone as _fd

        kwargs = _parse_args(args, context)
        return str(
            _fd(
                name=kwargs.get("name", "file"),
                label=kwargs.get("label", ""),
                accept=kwargs.get("accept", ""),
                multiple=kwargs.get("multiple", False),
                max_size_mb=kwargs.get("max_size_mb", 10),
                event=kwargs.get("event", "file_selected"),
            )
        )


class VirtualListHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import virtual_list as _vl

        kwargs = _parse_args(args, context)
        items_val = kwargs.get("items", "vl_items")
        if isinstance(items_val, list):
            items = items_val
        elif isinstance(items_val, str):
            # Could be a context variable name or an already-resolved JSON string
            if items_val in context:
                items = context[items_val]
            else:
                items = items_val
            # If still a string, try JSON deserialization
            if isinstance(items, str):
                try:
                    items = _json.loads(items)
                except (ValueError, TypeError):
                    items = []
        else:
            items = []
        return str(
            _vl(
                items=items,
                total=kwargs.get("total", len(items) if items else 0),
                page=kwargs.get("page", 1),
                page_size=kwargs.get("page_size", 20),
                load_more_event=kwargs.get("load_more_event", "load_more"),
            )
        )


class KanbanBoardHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import kanban_board as _kb

        kwargs = _parse_args(args, context)
        cols_val = kwargs.get("columns", "kanban_columns")
        if isinstance(cols_val, list):
            columns = cols_val
        elif isinstance(cols_val, str):
            if cols_val in context:
                columns = context[cols_val]
            else:
                columns = cols_val
            if isinstance(columns, str):
                try:
                    columns = _json.loads(columns)
                except (ValueError, TypeError):
                    columns = []
        else:
            columns = []
        return str(
            _kb(
                columns=columns,
                move_event=kwargs.get("move_event", "kanban_move"),
                add_card_event=kwargs.get("add_card_event", "kanban_add_card"),
            )
        )


class TableOfContentsHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import table_of_contents as _toc

        kwargs = _parse_args(args, context)
        items_key = kwargs.get("items", "toc_items")
        items = context.get(items_key, []) if isinstance(items_key, str) else items_key
        return str(
            _toc(
                items=items,
                title=kwargs.get("title", "Contents"),
                active=kwargs.get("active", ""),
                event=kwargs.get("event", ""),
            )
        )


class RichTextEditorHandler:
    def render(self, args, context):
        from djust.components.templatetags.djust_components import rich_text_editor as _rte

        kwargs = _parse_args(args, context)
        return str(
            _rte(
                name=kwargs.get("name", "content"),
                value=kwargs.get("value", ""),
                event=kwargs.get("event", "update_content"),
                label=kwargs.get("label", ""),
                height=kwargs.get("height", "200px"),
            )
        )


# Register v1.3 inline handlers
INLINE_HANDLERS.extend(
    [
        ("date_picker", DatePickerHandler()),
        ("file_dropzone", FileDropzoneHandler()),
        ("virtual_list", VirtualListHandler()),
        ("kanban_board", KanbanBoardHandler()),
        ("table_of_contents", TableOfContentsHandler()),
        ("rich_text_editor", RichTextEditorHandler()),
    ]
)


class SplitPaneHandler:
    """Block handler for {% split_pane %}...{% pane %}...{% endsplit_pane %}"""

    def render(self, args, content, context):
        # For Rust engine, content is pre-rendered; we just wrap it
        kwargs = _parse_args(args, context)
        direction = kwargs.get("direction", "horizontal")
        from django.utils.html import conditional_escape as ce
        import uuid as _uuid

        uid = f"sp-{_uuid.uuid4().hex[:6]}"
        return mark_safe(
            f'<div class="split-pane split-pane-{ce(direction)}" id="{uid}">{content}</div>'
        )


BLOCK_HANDLERS.extend(
    [
        ("split_pane", "endsplit_pane", SplitPaneHandler()),
    ]
)


# ===========================================================================
# FORM INPUT COMPONENTS (v0.4)
# ===========================================================================


class MultiSelectHandler:
    """Inline handler for {% multi_select name="tags" options=opts %}"""

    def render(self, args, context):
        from djust.components.templatetags.djust_components import multi_select as _ms

        kwargs = _parse_args(args, context)
        return str(
            _ms(
                name=kwargs.get("name", ""),
                label=kwargs.get("label", ""),
                options=kwargs.get("options"),
                selected=kwargs.get("selected"),
                event=kwargs.get("event", ""),
                placeholder=kwargs.get("placeholder", "Search..."),
                disabled=kwargs.get("disabled", False),
            )
        )


class OtpInputHandler:
    """Inline handler for {% otp_input name="code" digits=6 %}"""

    def render(self, args, context):
        from djust.components.templatetags.djust_components import otp_input as _oi

        kwargs = _parse_args(args, context)
        return str(
            _oi(
                name=kwargs.get("name", ""),
                digits=kwargs.get("digits", 6),
                event=kwargs.get("event", ""),
                label=kwargs.get("label", ""),
                disabled=kwargs.get("disabled", False),
            )
        )


class NumberStepperHandler:
    """Inline handler for {% number_stepper name="qty" min=1 max=99 %}"""

    def render(self, args, context):
        from djust.components.templatetags.djust_components import number_stepper as _ns

        kwargs = _parse_args(args, context)
        return str(
            _ns(
                name=kwargs.get("name", ""),
                value=kwargs.get("value", 0),
                min_val=kwargs.get("min_val") or kwargs.get("min"),
                max_val=kwargs.get("max_val") or kwargs.get("max"),
                step=kwargs.get("step", 1),
                event=kwargs.get("event", ""),
                label=kwargs.get("label", ""),
                disabled=kwargs.get("disabled", False),
            )
        )


class TagInputHandler:
    """Inline handler for {% tag_input name="tags" suggestions=tags %}"""

    def render(self, args, context):
        from djust.components.templatetags.djust_components import tag_input as _ti

        kwargs = _parse_args(args, context)
        return str(
            _ti(
                name=kwargs.get("name", ""),
                tags=kwargs.get("tags"),
                suggestions=kwargs.get("suggestions"),
                event=kwargs.get("event", ""),
                placeholder=kwargs.get("placeholder", "Add tag..."),
                disabled=kwargs.get("disabled", False),
                label=kwargs.get("label", ""),
            )
        )


class InputGroupHandler:
    """Block handler for {% input_group %}...{% endinput_group %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        size = kw.get("size", "md")
        error = kw.get("error", "")
        size_cls = f" input-group-{conditional_escape(size)}" if size != "md" else ""
        error_cls = " input-group-error" if error else ""
        error_html = (
            f'<span class="form-error-message">{conditional_escape(error)}</span>' if error else ""
        )
        return mark_safe(
            f'<div class="input-group{size_cls}{error_cls}">{content}</div>{error_html}'
        )


class InputAddonHandler:
    """Block handler for {% input_addon %}...{% endinput_addon %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        position = kw.get("position", "prefix")
        return mark_safe(
            f'<span class="input-addon input-addon-{conditional_escape(position)}">{content}</span>'
        )


class DjLabelHandler:
    """Block handler for {% dj_label for="email" %}Email{% enddj_label %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        for_input = kw.get("for", "")
        required = kw.get("required", False)
        extra_class = kw.get("class", "")
        for_attr = f' for="{conditional_escape(for_input)}"' if for_input else ""
        required_span = ' <span class="form-required">*</span>' if required else ""
        cls = f"form-label {conditional_escape(extra_class)}".strip()
        return mark_safe(f'<label class="{cls}"{for_attr}>{content}{required_span}</label>')


class FieldsetHandler:
    """Block handler for {% fieldset legend="Account" %}...{% endfieldset %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        legend = kw.get("legend", "")
        disabled = kw.get("disabled", False)
        extra_class = kw.get("class", "")
        disabled_attr = " disabled" if disabled else ""
        legend_html = (
            f'<legend class="fieldset-legend">{conditional_escape(legend)}</legend>'
            if legend
            else ""
        )
        cls = f"fieldset {conditional_escape(extra_class)}".strip()
        return mark_safe(
            f'<fieldset class="{cls}"{disabled_attr}>'
            f"{legend_html}"
            f'<div class="fieldset-content">{content}</div>'
            f"</fieldset>"
        )


# Register form input inline handlers
INLINE_HANDLERS.extend(
    [
        ("multi_select", MultiSelectHandler()),
        ("otp_input", OtpInputHandler()),
        ("number_stepper", NumberStepperHandler()),
        ("tag_input", TagInputHandler()),
    ]
)

# Register form input block handlers
BLOCK_HANDLERS.extend(
    [
        ("input_group", "endinput_group", InputGroupHandler()),
        ("input_addon", "endinput_addon", InputAddonHandler()),
        ("dj_label", "enddj_label", DjLabelHandler()),
        ("fieldset", "endfieldset", FieldsetHandler()),
    ]
)


# ===========================================================================
# BUTTON & CONTROL VARIANT HANDLERS
# ===========================================================================


class ToggleGroupHandler:
    """Inline handler for {% toggle_group name=... options=... value=... %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        options = kw.get("options", [])
        if not isinstance(options, (list, tuple)):
            options = []
        value = kw.get("value", "")
        # In multi mode, value can be a list
        mode = kw.get("mode", "single")
        event = conditional_escape(kw.get("event", "toggle_select"))
        disabled = kw.get("disabled", False)
        size = kw.get("size", "md")

        size_cls = ""
        if size and size != "md":
            size_cls = f" toggle-group-{conditional_escape(size)}"
        disabled_cls = " toggle-group-disabled" if disabled else ""

        buttons = []
        for opt in options:
            if not isinstance(opt, dict):
                continue
            opt_value = conditional_escape(str(opt.get("value", "")))
            opt_label = conditional_escape(str(opt.get("label", "")))
            opt_icon = opt.get("icon", "")

            # Determine if this option is active
            if mode == "multi" and isinstance(value, (list, tuple)):
                is_active = opt.get("value", "") in value
            else:
                is_active = str(opt.get("value", "")) == str(value)

            active_cls = " toggle-group-btn--active" if is_active else ""
            aria_pressed = "true" if is_active else "false"
            disabled_attr = " disabled" if disabled else ""
            click_attr = "" if disabled else f' dj-click="{event}" data-value="{opt_value}"'

            icon_html = ""
            if opt_icon:
                icon_html = (
                    f'<span class="toggle-group-icon">{conditional_escape(str(opt_icon))}</span>'
                )

            buttons.append(
                f'<button class="toggle-group-btn{active_cls}" '
                f'aria-pressed="{aria_pressed}" '
                f'data-name="{name}"{click_attr}{disabled_attr}>'
                f"{icon_html}"
                f'<span class="toggle-group-label">{opt_label}</span>'
                f"</button>"
            )

        return mark_safe(
            f'<div class="toggle-group{size_cls}{disabled_cls}" '
            f'role="group" data-mode="{conditional_escape(mode)}">'
            f"{''.join(buttons)}"
            f"</div>"
        )


class FabHandler:
    """Inline handler for {% fab icon=... event=... position=... %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        icon = conditional_escape(kw.get("icon", "+"))
        event = conditional_escape(kw.get("event", ""))
        position = kw.get("position", "bottom-right")
        label = conditional_escape(kw.get("label", ""))
        size = kw.get("size", "md")
        variant = kw.get("variant", "primary")
        disabled = kw.get("disabled", False)
        actions = kw.get("actions", [])
        if not isinstance(actions, (list, tuple)):
            actions = []

        valid_positions = ("bottom-right", "bottom-left", "top-right", "top-left")
        pos_cls = position if position in valid_positions else "bottom-right"
        pos_cls = conditional_escape(pos_cls)

        size_cls = ""
        if size and size != "md":
            size_cls = f" fab-{conditional_escape(size)}"
        variant_cls = f" fab-{conditional_escape(variant)}"
        disabled_attr = " disabled" if disabled else ""
        click_attr = "" if disabled or not event else f' dj-click="{event}"'
        aria_label = f' aria-label="{label}"' if label else ""

        # Speed-dial sub-actions
        actions_html = ""
        if actions:
            action_items = []
            for act in actions:
                if not isinstance(act, dict):
                    continue
                act_icon = conditional_escape(str(act.get("icon", "")))
                act_event = conditional_escape(str(act.get("event", "")))
                act_label = conditional_escape(str(act.get("label", "")))
                act_click = f' dj-click="{act_event}"' if act_event and not disabled else ""
                act_aria = f' aria-label="{act_label}"' if act_label else ""
                action_items.append(
                    f'<button class="fab-action"{act_click}{act_aria}{disabled_attr}>'
                    f'<span class="fab-action-icon">{act_icon}</span>'
                    f"</button>"
                )
            if action_items:
                actions_html = f'<div class="fab-actions">{"".join(action_items)}</div>'

        return mark_safe(
            f'<div class="fab-container fab-{pos_cls}">'
            f"{actions_html}"
            f'<button class="fab{size_cls}{variant_cls}"{click_attr}{aria_label}{disabled_attr}>'
            f'<span class="fab-icon">{icon}</span>'
            f"</button>"
            f"</div>"
        )


class SplitButtonHandler:
    """Inline handler for {% split_button label=... event=... options=... %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        event = conditional_escape(kw.get("event", ""))
        options = kw.get("options", [])
        if not isinstance(options, (list, tuple)):
            options = []
        variant = kw.get("variant", "primary")
        size = kw.get("size", "md")
        disabled = kw.get("disabled", False)
        loading = kw.get("loading", False)
        is_open = kw.get("is_open", kw.get("open", False))
        toggle_event = conditional_escape(kw.get("toggle_event", "toggle_split_menu"))

        variant_cls = f" split-btn-{conditional_escape(variant)}"
        size_cls = ""
        if size and size != "md":
            size_cls = f" split-btn-{conditional_escape(size)}"
        loading_cls = " split-btn-loading" if loading else ""
        disabled_attr = " disabled" if disabled or loading else ""
        click_attr = "" if disabled or loading or not event else f' dj-click="{event}"'

        spinner_html = '<span class="split-btn-spinner"></span>' if loading else ""

        # Build option items
        option_items = []
        for opt in options:
            if not isinstance(opt, dict):
                continue
            opt_label = conditional_escape(str(opt.get("label", "")))
            opt_event = conditional_escape(str(opt.get("event", "")))
            opt_click = f' dj-click="{opt_event}"' if opt_event and not disabled else ""
            opt_disabled = " disabled" if disabled else ""
            option_items.append(
                f'<button class="split-btn-option" role="menuitem"{opt_click}{opt_disabled}>'
                f"{opt_label}</button>"
            )

        open_data = "true" if is_open else "false"
        toggle_disabled = " disabled" if disabled or loading else ""
        toggle_click = "" if disabled or loading else f' dj-click="{toggle_event}"'

        menu_html = ""
        if option_items:
            menu_html = (
                f'<div class="split-btn-menu" role="menu" data-open="{open_data}">'
                f"{''.join(option_items)}"
                f"</div>"
            )

        return mark_safe(
            f'<div class="split-btn{variant_cls}{size_cls}{loading_cls}">'
            f'<button class="split-btn-primary"{click_attr}{disabled_attr}>'
            f"{spinner_html}"
            f'<span class="split-btn-label">{label}</span>'
            f"</button>"
            f'<button class="split-btn-toggle"{toggle_click}{toggle_disabled} '
            f'aria-expanded="{open_data}" aria-haspopup="true">'
            f'<span class="split-btn-caret">&#9662;</span>'
            f"</button>"
            f"{menu_html}"
            f"</div>"
        )


# Register button & control variant inline handlers
INLINE_HANDLERS.extend(
    [
        ("toggle_group", ToggleGroupHandler()),
        ("fab", FabHandler()),
        ("split_button", SplitButtonHandler()),
    ]
)


# ===========================================================================
# STATUS / PROGRESS INDICATOR HANDLERS
# ===========================================================================


class NotificationBadgeHandler:
    """Inline handler for {% notification_badge count=5 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        try:
            count = int(kw.get("count", 0))
        except (ValueError, TypeError):
            count = 0
        try:
            max_count = int(kw.get("max", 99))
        except (ValueError, TypeError):
            max_count = 99
        dot = kw.get("dot", False)
        pulse = kw.get("pulse", False)
        size = conditional_escape(kw.get("size", "md"))

        cls = f"dj-notification-badge dj-notification-badge--{size}"
        if pulse:
            cls += " dj-notification-badge--pulse"

        if dot:
            return mark_safe(f'<span class="{cls} dj-notification-badge--dot"></span>')

        if count <= 0:
            return ""

        display = f"{max_count}+" if count > max_count else str(count)
        return mark_safe(f'<span class="{cls}">{display}</span>')


class SegmentedProgressHandler:
    """Inline handler for {% segmented_progress steps=steps current=2 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        steps = kw.get("steps", [])
        if not isinstance(steps, (list, tuple)):
            steps = []
        try:
            current = int(kw.get("current", 0))
        except (ValueError, TypeError):
            current = 0
        size = conditional_escape(kw.get("size", "md"))

        cls = f"dj-segmented-progress dj-segmented-progress--{size}"

        segments = []
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                label = conditional_escape(str(step.get("label", "")))
            else:
                label = conditional_escape(str(step))
            step_num = i + 1
            if step_num < current:
                state = "completed"
            elif step_num == current:
                state = "active"
            else:
                state = "pending"
            segments.append(
                f'<div class="dj-segmented-progress__step dj-segmented-progress__step--{state}">'
                f'<div class="dj-segmented-progress__indicator">{step_num}</div>'
                f'<div class="dj-segmented-progress__label">{label}</div>'
                f"</div>"
            )

        parts = []
        for i, seg in enumerate(segments):
            parts.append(seg)
            if i < len(segments) - 1:
                step_num = i + 1
                line_state = "completed" if step_num < current else "pending"
                parts.append(
                    f'<div class="dj-segmented-progress__connector '
                    f'dj-segmented-progress__connector--{line_state}"></div>'
                )

        return mark_safe(f'<div class="{cls}">{"".join(parts)}</div>')


class ProgressCircleHandler:
    """Inline handler for {% progress_circle value=65 size="md" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        try:
            value = max(0, min(100, int(kw.get("value", 0))))
        except (ValueError, TypeError):
            value = 0
        size = str(kw.get("size", "md"))
        color = conditional_escape(kw.get("color", "primary"))
        show_value = kw.get("show_value", True)

        sizes = {"sm": 48, "md": 80, "lg": 120}
        dim = sizes.get(size, 80)
        stroke_widths = {"sm": 4, "md": 6, "lg": 8}
        stroke_w = stroke_widths.get(size, 6)

        radius = (dim - stroke_w) / 2
        circumference = 2 * 3.14159265 * radius
        dash_offset = circumference * (1 - value / 100)

        e_size = conditional_escape(size)
        cls = f"dj-progress-circle dj-progress-circle--{e_size} dj-progress-circle--{color}"

        value_html = ""
        if show_value:
            font_sizes = {"sm": "0.625rem", "md": "1rem", "lg": "1.5rem"}
            fs = font_sizes.get(size, "1rem")
            value_html = (
                f'<text x="{dim / 2}" y="{dim / 2}" '
                f'class="dj-progress-circle__value" '
                f'text-anchor="middle" dominant-baseline="central" '
                f'style="font-size:{fs}">'
                f"{value}%</text>"
            )

        return mark_safe(
            f'<div class="{cls}" role="progressbar" '
            f'aria-valuenow="{value}" aria-valuemin="0" aria-valuemax="100">'
            f'<svg width="{dim}" height="{dim}" viewBox="0 0 {dim} {dim}">'
            f'<circle class="dj-progress-circle__track" '
            f'cx="{dim / 2}" cy="{dim / 2}" r="{radius}" '
            f'fill="none" stroke-width="{stroke_w}"/>'
            f'<circle class="dj-progress-circle__fill" '
            f'cx="{dim / 2}" cy="{dim / 2}" r="{radius}" '
            f'fill="none" stroke-width="{stroke_w}" '
            f'stroke-dasharray="{circumference:.2f}" '
            f'stroke-dashoffset="{dash_offset:.2f}" '
            f'stroke-linecap="round" '
            f'transform="rotate(-90 {dim / 2} {dim / 2})"/>'
            f"{value_html}"
            f"</svg></div>"
        )


class StatusIndicatorHandler:
    """Inline handler for {% status_indicator status="online" label="API" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        status = str(kw.get("status", "offline"))
        label = kw.get("label", "")
        pulse = kw.get("pulse", False)
        size = conditional_escape(kw.get("size", "md"))

        e_label = conditional_escape(str(label))

        status_colors = {
            "online": "green",
            "degraded": "yellow",
            "offline": "red",
            "maintenance": "blue",
        }
        color = status_colors.get(status, "gray")

        cls = f"dj-status-indicator dj-status-indicator--{size} dj-status-indicator--{color}"
        if pulse:
            cls += " dj-status-indicator--pulse"

        dot_html = '<span class="dj-status-indicator__dot"></span>'
        label_html = f'<span class="dj-status-indicator__label">{e_label}</span>' if label else ""

        return mark_safe(f'<span class="{cls}" role="status">{dot_html}{label_html}</span>')


# Register status/progress indicator inline handlers
INLINE_HANDLERS.extend(
    [
        ("notification_badge", NotificationBadgeHandler()),
        ("segmented_progress", SegmentedProgressHandler()),
        ("progress_circle", ProgressCircleHandler()),
        ("status_indicator", StatusIndicatorHandler()),
    ]
)


# ===========================================================================
# OVERLAY / FEEDBACK HANDLERS
# ===========================================================================


class LoadingOverlayHandler:
    """Block handler for {% loading_overlay active=... %}...{% endloading_overlay %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        active = kw.get("active", False)
        text = kw.get("text", "")
        spinner_size = kw.get("spinner_size", "md")
        custom_class = kw.get("custom_class", "")

        e_spinner_size = conditional_escape(str(spinner_size))
        e_custom_class = conditional_escape(str(custom_class))
        e_text = conditional_escape(str(text))

        cls = "dj-loading-overlay-wrap"
        if e_custom_class:
            cls += f" {e_custom_class}"

        overlay_html = ""
        if active:
            text_html = f'<span class="dj-loading-overlay__text">{e_text}</span>' if text else ""
            overlay_html = (
                f'<div class="dj-loading-overlay">'
                f'<div class="dj-loading-overlay__spinner dj-loading-overlay__spinner--{e_spinner_size}"></div>'
                f"{text_html}"
                f"</div>"
            )

        return mark_safe(f'<div class="{cls}">{content}{overlay_html}</div>')


class AnnouncementBarHandler:
    """Block handler for {% announcement_bar type=... %}...{% endannouncement_bar %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        bar_type = kw.get("variant", kw.get("type", "info"))
        dismissible = kw.get("dismissible", False)
        dismiss_event = kw.get("dismiss_event", "dismiss_announcement")
        custom_class = kw.get("custom_class", "")

        e_bar_type = conditional_escape(str(bar_type))
        e_dismiss_event = conditional_escape(str(dismiss_event))
        e_custom_class = conditional_escape(str(custom_class))

        cls = f"dj-announcement-bar dj-announcement-bar--{e_bar_type}"
        if e_custom_class:
            cls += f" {e_custom_class}"

        close_html = ""
        if dismissible:
            close_html = (
                f'<button class="dj-announcement-bar__close" '
                f'dj-click="{e_dismiss_event}">&times;</button>'
            )

        return mark_safe(
            f'<div class="{cls}" role="banner" aria-live="polite">'
            f'<div class="dj-announcement-bar__content">{content}</div>'
            f"{close_html}"
            f"</div>"
        )


# Register overlay/feedback handlers
BLOCK_HANDLERS.extend(
    [
        ("loading_overlay", "endloading_overlay", LoadingOverlayHandler()),
        ("announcement_bar", "endannouncement_bar", AnnouncementBarHandler()),
    ]
)


# ===========================================================================
# RICH SELECT & DATA GRID HANDLERS
# ===========================================================================


class RichSelectHandler:
    """Inline handler for {% rich_select name=... options=... %}"""

    def render(self, args, context):
        from djust.components.templatetags.djust_components import rich_select as _rs

        kwargs = _parse_args(args, context)
        return str(
            _rs(
                name=kwargs.get("name", ""),
                options=kwargs.get("options"),
                value=kwargs.get("value", ""),
                event=kwargs.get("event", ""),
                placeholder=kwargs.get("placeholder", "Select..."),
                disabled=kwargs.get("disabled", False),
                searchable=kwargs.get("searchable", False),
                label=kwargs.get("label", ""),
            )
        )


class DataGridHandler:
    """Inline handler for {% data_grid columns=cols rows=rows %}"""

    def render(self, args, context):
        from djust.components.templatetags.djust_components import data_grid as _dg

        kwargs = _parse_args(args, context)
        return str(
            _dg(
                columns=kwargs.get("columns"),
                rows=kwargs.get("rows"),
                row_key=kwargs.get("row_key", "id"),
                edit_event=kwargs.get("edit_event", "grid_cell_edit"),
                resizable=kwargs.get("resizable", True),
                frozen_left=kwargs.get("frozen_left", 0),
                frozen_right=kwargs.get("frozen_right", 0),
                striped=kwargs.get("striped", False),
                compact=kwargs.get("compact", False),
                keyboard_nav=kwargs.get("keyboard_nav", True),
                new_row_event=kwargs.get("new_row_event", ""),
                delete_row_event=kwargs.get("delete_row_event", ""),
                custom_class=kwargs.get("custom_class", ""),
            )
        )


INLINE_HANDLERS.extend(
    [
        ("rich_select", RichSelectHandler()),
        ("data_grid", DataGridHandler()),
    ]
)


# ===========================================================================
# WEBSOCKET-POWERED COMPONENT HANDLERS
# ===========================================================================


class StreamingTextHandler:
    """Inline handler for {% streaming_text stream_event="stream_chunk" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        stream_event = conditional_escape(str(kw.get("stream_event", "stream_chunk")))
        text = conditional_escape(str(kw.get("text", "")))
        markdown = kw.get("markdown", False)
        auto_scroll = kw.get("auto_scroll", True)
        cursor = kw.get("cursor", True)
        custom_class = conditional_escape(str(kw.get("custom_class", "")))

        cls = "dj-streaming-text"
        if cursor:
            cls += " dj-streaming-text--cursor"
        if custom_class:
            cls += f" {custom_class}"

        attrs = [
            f'class="{cls}"',
            f'data-stream-event="{stream_event}"',
        ]
        if auto_scroll:
            attrs.append('data-auto-scroll="true"')
        if markdown:
            attrs.append('data-markdown="true"')

        attrs_str = " ".join(attrs)
        return mark_safe(
            f'<div {attrs_str}><div class="dj-streaming-text__content">{text}</div></div>'
        )


class ConnectionStatusHandler:
    """Inline handler for {% connection_status %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        custom_class = conditional_escape(str(kw.get("custom_class", "")))
        reconnecting_text = conditional_escape(str(kw.get("reconnecting_text", "Reconnecting...")))
        connected_text = conditional_escape(str(kw.get("connected_text", "Reconnected")))

        cls = "dj-connection-status"
        if custom_class:
            cls += f" {custom_class}"

        return mark_safe(
            f'<div class="{cls}" '
            f'data-reconnecting-text="{reconnecting_text}" '
            f'data-connected-text="{connected_text}" '
            f'role="status" aria-live="polite" style="display:none">'
            f'<span class="dj-connection-status__text">{reconnecting_text}</span>'
            f"</div>"
        )


class LiveCounterHandler:
    """Inline handler for {% live_counter value=42 label="online" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        try:
            value = int(kw.get("value", 0))
        except (ValueError, TypeError):
            value = 0
        label = conditional_escape(str(kw.get("label", "")))
        stream_event = conditional_escape(str(kw.get("stream_event", "counter_update")))
        custom_class = conditional_escape(str(kw.get("custom_class", "")))
        size = conditional_escape(str(kw.get("size", "md")))

        cls = f"dj-live-counter dj-live-counter--{size}"
        if custom_class:
            cls += f" {custom_class}"

        label_html = ""
        if label:
            label_html = f'<span class="dj-live-counter__label">{label}</span>'

        return mark_safe(
            f'<div class="{cls}" data-stream-event="{stream_event}">'
            f'<span class="dj-live-counter__value" data-value="{value}">{value}</span>'
            f"{label_html}"
            f"</div>"
        )


class ServerToastContainerHandler:
    """Inline handler for {% server_toast_container position="top-right" %}"""

    ALLOWED_POSITIONS = {
        "top-left",
        "top-right",
        "top-center",
        "bottom-left",
        "bottom-right",
        "bottom-center",
    }

    def render(self, args, context):
        kw = _parse_args(args, context)
        position = str(kw.get("position", "top-right"))
        if position not in self.ALLOWED_POSITIONS:
            position = "top-right"
        custom_class = conditional_escape(str(kw.get("custom_class", "")))
        try:
            max_toasts = int(kw.get("max_toasts", 5))
        except (ValueError, TypeError):
            max_toasts = 5

        cls = f"dj-toast-container dj-toast-container--{position}"
        if custom_class:
            cls += f" {custom_class}"

        return mark_safe(
            f'<div class="{cls}" '
            f'data-max-toasts="{max_toasts}" '
            f'role="region" aria-live="polite" aria-label="Notifications">'
            f"</div>"
        )


class ScrollToTopHandler:
    """Inline handler for {% scroll_to_top threshold="300px" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        threshold = conditional_escape(str(kw.get("threshold", "300px")))
        label = conditional_escape(str(kw.get("label", "Back to top")))
        custom_class = conditional_escape(str(kw.get("custom_class", "")))

        cls = "dj-scroll-to-top"
        if custom_class:
            cls += f" {custom_class}"

        return mark_safe(
            f'<button class="{cls}" '
            f'data-threshold="{threshold}" '
            f'aria-label="{label}" '
            f'title="{label}" '
            f'style="display:none">'
            f'<svg width="20" height="20" viewBox="0 0 20 20" fill="none" '
            f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round">'
            f'<path d="M10 16V4M10 4l-6 6M10 4l6 6"/>'
            f"</svg>"
            f"</button>"
        )


class CodeSnippetHandler:
    """Inline handler for {% code_snippet language="bash" code="pip install djust" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        code = conditional_escape(str(kw.get("code", "")))
        language = conditional_escape(str(kw.get("language", "")))
        custom_class = conditional_escape(str(kw.get("custom_class", "")))

        cls = "dj-code-snippet"
        if custom_class:
            cls += f" {custom_class}"

        lang_badge = ""
        if language:
            lang_badge = f'<span class="dj-code-snippet__lang">{language}</span>'

        return mark_safe(
            f'<div class="{cls}">'
            f'<div class="dj-code-snippet__header">'
            f"{lang_badge}"
            f'<button class="dj-code-snippet__copy" aria-label="Copy code" '
            f'type="button">Copy</button>'
            f"</div>"
            f'<pre class="dj-code-snippet__pre">'
            f'<code class="dj-code-snippet__code">{code}</code>'
            f"</pre>"
            f"</div>"
        )


class ResponsiveImageHandler:
    """Inline handler for {% responsive_image src=url alt="..." %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        src = conditional_escape(str(kw.get("src", "")))
        alt = conditional_escape(str(kw.get("alt", "")))
        aspect_ratio = conditional_escape(str(kw.get("aspect_ratio", "")))
        lazy = kw.get("lazy", True)
        srcset = conditional_escape(str(kw.get("srcset", "")))
        sizes = conditional_escape(str(kw.get("sizes", "")))
        placeholder = conditional_escape(str(kw.get("placeholder", "")))
        custom_class = conditional_escape(str(kw.get("custom_class", "")))

        if isinstance(lazy, str):
            lazy = lazy.lower() not in ("false", "0", "")

        cls = "dj-responsive-image"
        if placeholder:
            cls += " dj-responsive-image--blur-up"
        if custom_class:
            cls += f" {custom_class}"

        style = ""
        if aspect_ratio:
            style = f' style="aspect-ratio:{aspect_ratio}"'

        img_attrs = [f'src="{src}"', f'alt="{alt}"']
        if lazy:
            img_attrs.append('loading="lazy"')
        if srcset:
            img_attrs.append(f'srcset="{srcset}"')
        if sizes:
            img_attrs.append(f'sizes="{sizes}"')

        img_tag = f'<img {" ".join(img_attrs)} class="dj-responsive-image__img">'

        placeholder_html = ""
        if placeholder:
            placeholder_html = (
                f'<img src="{placeholder}" alt="" '
                f'class="dj-responsive-image__placeholder" aria-hidden="true">'
            )

        return mark_safe(f'<div class="{cls}"{style}>{placeholder_html}{img_tag}</div>')


class RelativeTimeHandler:
    """Inline handler for {% relative_time datetime=created_at %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        dt = kw.get("datetime", "")
        auto_update = kw.get("auto_update", True)
        interval = kw.get("interval", 60)
        custom_class = conditional_escape(str(kw.get("custom_class", "")))

        if isinstance(auto_update, str):
            auto_update = auto_update.lower() not in ("false", "0", "")

        cls = "dj-relative-time"
        if custom_class:
            cls += f" {custom_class}"

        iso_val = ""
        if dt:
            if hasattr(dt, "isoformat"):
                iso_val = dt.isoformat()
            else:
                iso_val = str(dt)

        e_iso = conditional_escape(iso_val)
        auto_str = "true" if auto_update else "false"

        try:
            interval_val = int(interval)
        except (ValueError, TypeError):
            interval_val = 60

        return mark_safe(
            f'<time class="{cls}" '
            f'datetime="{e_iso}" '
            f'data-auto-update="{auto_str}" '
            f'data-interval="{interval_val}">'
            f"{e_iso}"
            f"</time>"
        )


class CopyableTextHandler:
    """Block handler for {% copyable_text %}...{% endcopyable_text %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        copied_label = conditional_escape(str(kw.get("copied_label", "Copied!")))
        custom_class = conditional_escape(str(kw.get("custom_class", "")))

        e_content = conditional_escape(content.strip())

        cls = "dj-copyable-text"
        if custom_class:
            cls += f" {custom_class}"

        return mark_safe(
            f'<span class="{cls}" '
            f'data-copy-text="{e_content}" '
            f'data-copied-label="{copied_label}" '
            f'role="button" tabindex="0" '
            f'aria-label="Click to copy">'
            f'<span class="dj-copyable-text__value">{e_content}</span>'
            f'<span class="dj-copyable-text__tooltip" aria-hidden="true">{copied_label}</span>'
            f"</span>"
        )


INLINE_HANDLERS.extend(
    [
        ("streaming_text", StreamingTextHandler()),
        ("connection_status", ConnectionStatusHandler()),
        ("live_counter", LiveCounterHandler()),
        ("server_toast_container", ServerToastContainerHandler()),
        ("scroll_to_top", ScrollToTopHandler()),
        ("code_snippet", CodeSnippetHandler()),
        ("responsive_image", ResponsiveImageHandler()),
        ("relative_time", RelativeTimeHandler()),
    ]
)

BLOCK_HANDLERS.extend(
    [
        ("copyable_text", "endcopyable_text", CopyableTextHandler()),
    ]
)


# ===========================================================================
# ICON SYSTEM (#178) + THEME TOGGLE (#138)
# ===========================================================================


class IconHandler:
    """Inline handler for {% icon name="check" size="md" set="heroicons" %}"""

    def render(self, args, context):
        from djust.components.icons import render_icon

        kw = _parse_args(args, context)
        name = kw.get("name", "")
        size = kw.get("size", "md")
        icon_set = kw.get("set", "heroicons")
        custom_class = kw.get("custom_class", "")
        # Pass remaining kwargs as extra attrs
        extra = {k: v for k, v in kw.items() if k not in ("name", "size", "set", "custom_class")}
        return str(
            render_icon(
                name=name,
                size=size,
                icon_set=icon_set,
                custom_class=custom_class,
                **extra,
            )
        )


class ThemeToggleHandler:
    """Inline handler for {% theme_toggle current="system" event="set_theme" %}"""

    def render(self, args, context):
        import uuid
        from djust.components.icons import render_icon

        kw = _parse_args(args, context)
        current = conditional_escape(kw.get("current", "system"))
        event = kw.get("event", "")
        custom_class = kw.get("custom_class", "")

        e_event = conditional_escape(event) if event else ""
        e_cls = conditional_escape(custom_class)

        cls = "dj-theme-toggle"
        if e_cls:
            cls += f" {e_cls}"

        click_attr = f' dj-click="{e_event}"' if e_event else ""
        sun_svg = render_icon("sun", size="sm")
        moon_svg = render_icon("moon", size="sm")
        monitor_svg = render_icon("computer-desktop", size="sm")
        toggle_id = f"dj-theme-toggle-{uuid.uuid4().hex[:8]}"

        return mark_safe(
            f'<div class="{cls}" id="{toggle_id}" '
            f'data-current="{current}"{click_attr} '
            f'role="radiogroup" aria-label="Color theme">'
            f'<button type="button" class="dj-theme-toggle__btn" '
            f'data-theme="light" aria-label="Light theme" '
            f'title="Light">{sun_svg}</button>'
            f'<button type="button" class="dj-theme-toggle__btn" '
            f'data-theme="dark" aria-label="Dark theme" '
            f'title="Dark">{moon_svg}</button>'
            f'<button type="button" class="dj-theme-toggle__btn" '
            f'data-theme="system" aria-label="System theme" '
            f'title="System">{monitor_svg}</button>'
            f"</div>"
        )


INLINE_HANDLERS.extend(
    [
        ("icon", IconHandler()),
        ("theme_toggle", ThemeToggleHandler()),
    ]
)


# ===========================================================================
# PAGE HEADER HANDLER (#179)
# ===========================================================================


class PageHeaderActionsHandler:
    """Block handler for {% page_header_actions %}...{% endpage_header_actions %}"""

    def render(self, args, content, context):
        # Just wrap actions content in its container div
        return mark_safe(f'<div class="dj-page-header__actions">{content}</div>')


class PageHeaderHandler:
    """Block handler for {% page_header title=... %}...{% endpage_header %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        title = kw.get("title", "")
        subtitle = kw.get("subtitle", "")
        description = kw.get("description", "")
        custom_class = kw.get("custom_class", "")

        e_title = conditional_escape(str(title))
        e_subtitle = conditional_escape(str(subtitle))
        e_description = conditional_escape(str(description))
        e_custom_class = conditional_escape(str(custom_class))

        cls = "dj-page-header"
        if e_custom_class:
            cls += f" {e_custom_class}"

        # In the Rust engine, nested block tags render inline.
        # page_header_actions renders its own wrapper div, so we need to
        # separate actions from breadcrumb content.
        actions_marker = '<div class="dj-page-header__actions">'
        actions_section = ""
        breadcrumb_content = content
        if actions_marker in content:
            idx = content.index(actions_marker)
            breadcrumb_content = content[:idx]
            actions_section = content[idx:]

        # Breadcrumb slot
        breadcrumb_html = ""
        if breadcrumb_content.strip():
            breadcrumb_html = f'<div class="dj-page-header__breadcrumb">{breadcrumb_content}</div>'

        # Title
        title_html = f'<h1 class="dj-page-header__title">{e_title}</h1>' if e_title else ""

        # Subtitle
        subtitle_html = ""
        if e_subtitle:
            subtitle_html = f'<p class="dj-page-header__subtitle">{e_subtitle}</p>'

        # Description
        description_html = ""
        if e_description:
            description_html = f'<p class="dj-page-header__description">{e_description}</p>'

        return mark_safe(
            f'<header class="{cls}">'
            f"{breadcrumb_html}"
            f'<div class="dj-page-header__row">'
            f'<div class="dj-page-header__text">'
            f"{title_html}"
            f"{subtitle_html}"
            f"{description_html}"
            f"</div>"
            f"{actions_section}"
            f"</div>"
            f"</header>"
        )


BLOCK_HANDLERS.extend(
    [
        ("page_header_actions", "endpage_header_actions", PageHeaderActionsHandler()),
        ("page_header", "endpage_header", PageHeaderHandler()),
    ]
)


# ===========================================================================
# FORM ESSENTIALS (v1.5)
# ===========================================================================


class SliderHandler:
    """Inline handler for {% slider name="price" min=0 max=100 value=50 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", ""))
        min_val = int(kw.get("min_val", kw.get("min", 0)))
        max_val = int(kw.get("max_val", kw.get("max", 100)))
        step = int(kw.get("step", 1))
        value = kw.get("value", min_val)
        value_end = kw.get("value_end", None)
        event = conditional_escape(kw.get("event", name))
        disabled = kw.get("disabled", False)
        show_ticks = kw.get("show_ticks", False)
        show_value = kw.get("show_value", True)
        custom_class = conditional_escape(kw.get("custom_class", ""))

        disabled_attr = " disabled" if disabled else ""
        range_mode = value_end is not None
        cls = "dj-slider"
        if range_mode:
            cls += " dj-slider--range"
        if custom_class:
            cls += f" {custom_class}"

        label_html = (
            f'<label class="dj-slider__label" for="{name}">{label}</label>' if label else ""
        )

        value_display = ""
        if show_value:
            if range_mode:
                value_display = (
                    f'<output class="dj-slider__value">'
                    f"{conditional_escape(str(value))} &ndash; "
                    f"{conditional_escape(str(value_end))}"
                    f"</output>"
                )
            else:
                value_display = (
                    f'<output class="dj-slider__value">{conditional_escape(str(value))}</output>'
                )

        ticks_html = ""
        if show_ticks:
            tick_count = max(1, (max_val - min_val) // step)
            tick_items = "".join(
                '<span class="dj-slider__tick"></span>' for _ in range(tick_count + 1)
            )
            ticks_html = f'<div class="dj-slider__ticks">{tick_items}</div>'

        input_html = (
            f'<input type="range" class="dj-slider__input" '
            f'name="{name}" id="{name}" '
            f'min="{min_val}" max="{max_val}" step="{step}" '
            f'value="{conditional_escape(str(value))}" '
            f'dj-input="{event}"{disabled_attr}>'
        )

        if range_mode:
            input_html += (
                f'<input type="range" class="dj-slider__input dj-slider__input--end" '
                f'name="{name}_end" id="{name}_end" '
                f'min="{min_val}" max="{max_val}" step="{step}" '
                f'value="{conditional_escape(str(value_end))}" '
                f'dj-input="{event}"{disabled_attr}>'
            )

        return mark_safe(
            f'<div class="{cls}">'
            f"{label_html}"
            f'<div class="dj-slider__track">{input_html}</div>'
            f"{ticks_html}"
            f"{value_display}"
            f"</div>"
        )


class SearchInputHandler:
    """Inline handler for {% search_input name="q" placeholder="Search..." %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", ""))
        value = conditional_escape(str(kw.get("value", "")))
        placeholder = conditional_escape(kw.get("placeholder", "Search..."))
        event = conditional_escape(kw.get("event", name))
        debounce = int(kw.get("debounce", 300))
        loading = kw.get("loading", False)
        disabled = kw.get("disabled", False)
        custom_class = conditional_escape(kw.get("custom_class", ""))

        disabled_attr = " disabled" if disabled else ""
        cls = "dj-search-input"
        if loading:
            cls += " dj-search-input--loading"
        if custom_class:
            cls += f" {custom_class}"

        label_html = (
            f'<label class="dj-search-input__label" for="{name}">{label}</label>' if label else ""
        )

        icon_html = (
            '<svg class="dj-search-input__icon" viewBox="0 0 20 20" fill="currentColor" '
            'width="16" height="16" aria-hidden="true">'
            '<path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11z'
            "M2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328"
            'A7 7 0 012 9z" clip-rule="evenodd"/></svg>'
        )

        spinner_html = (
            '<span class="dj-search-input__spinner" aria-hidden="true"></span>' if loading else ""
        )

        clear_html = (
            '<button type="button" class="dj-search-input__clear" '
            'aria-label="Clear search" tabindex="-1">&times;</button>'
        )

        return mark_safe(
            f"{label_html}"
            f'<div class="{cls}">'
            f"{icon_html}"
            f'<input type="search" class="dj-search-input__input" '
            f'name="{name}" id="{name}" value="{value}" '
            f'placeholder="{placeholder}" autocomplete="off" '
            f'dj-input="{event}" data-debounce="{debounce}"{disabled_attr}>'
            f"{clear_html}"
            f"{spinner_html}"
            f"</div>"
        )


class PasswordInputHandler:
    """Inline handler for {% password_input name="pwd" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", ""))
        value = conditional_escape(str(kw.get("value", "")))
        placeholder = conditional_escape(kw.get("placeholder", ""))
        event = conditional_escape(kw.get("event", name))
        error = conditional_escape(kw.get("error", ""))
        required = kw.get("required", False)
        disabled = kw.get("disabled", False)
        show_strength = kw.get("show_strength", False)
        strength = int(kw.get("strength", 0))
        custom_class = conditional_escape(kw.get("custom_class", ""))

        required_attr = " required" if required else ""
        disabled_attr = " disabled" if disabled else ""
        cls = "dj-password-input"
        if error:
            cls += " dj-password-input--error"
        if custom_class:
            cls += f" {custom_class}"

        label_html = ""
        if label:
            req_span = '<span class="form-required"> *</span>' if required else ""
            label_html = f'<label class="form-label" for="{name}">{label}{req_span}</label>'

        error_html = f'<span class="form-error-message">{error}</span>' if error else ""

        toggle_btn = (
            '<button type="button" class="dj-password-input__toggle" '
            'aria-label="Toggle password visibility" tabindex="-1">'
            '<svg class="dj-password-input__eye" viewBox="0 0 20 20" '
            'fill="currentColor" width="16" height="16">'
            '<path d="M10 3C5 3 1.73 7.11 1 10c.73 2.89 4 7 9 7s8.27-4.11 9-7'
            "c-.73-2.89-4-7-9-7zm0 12a5 5 0 110-10 5 5 0 010 10zm0-8a3 3 0 "
            '100 6 3 3 0 000-6z"/></svg>'
            "</button>"
        )

        strength_html = ""
        if show_strength:
            s_cls = f"dj-password-strength--{min(max(strength, 0), 4)}"
            strength_html = (
                f'<div class="dj-password-strength {s_cls}" '
                f'role="meter" aria-valuenow="{strength}" '
                f'aria-valuemin="0" aria-valuemax="4">'
                f'<div class="dj-password-strength__bar"></div>'
                f'<div class="dj-password-strength__bar"></div>'
                f'<div class="dj-password-strength__bar"></div>'
                f'<div class="dj-password-strength__bar"></div>'
                f"</div>"
            )

        return mark_safe(
            f'<div class="form-group">'
            f"{label_html}"
            f'<div class="{cls}">'
            f'<input type="password" class="dj-password-input__input form-input" '
            f'name="{name}" id="{name}" value="{value}" '
            f'placeholder="{placeholder}" '
            f'dj-input="{event}"{required_attr}{disabled_attr}>'
            f"{toggle_btn}"
            f"</div>"
            f"{strength_html}"
            f"{error_html}"
            f"</div>"
        )


class AutocompleteHandler:
    """Inline handler for {% autocomplete name="city" source_event="search_cities" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", ""))
        value = conditional_escape(str(kw.get("value", "")))
        display_value = conditional_escape(str(kw.get("display_value", value)))
        placeholder = conditional_escape(kw.get("placeholder", ""))
        source_event = conditional_escape(kw.get("source_event", ""))
        event = conditional_escape(kw.get("event", name))
        debounce = int(kw.get("debounce", 300))
        min_chars = int(kw.get("min_chars", 1))
        suggestions = kw.get("suggestions") or context.get("suggestions", [])
        loading = kw.get("loading", False)
        disabled = kw.get("disabled", False)
        error = conditional_escape(kw.get("error", ""))
        required = kw.get("required", False)
        custom_class = conditional_escape(kw.get("custom_class", ""))

        if not isinstance(suggestions, (list, tuple)):
            suggestions = []

        disabled_attr = " disabled" if disabled else ""
        required_attr = " required" if required else ""
        cls = "dj-autocomplete"
        if loading:
            cls += " dj-autocomplete--loading"
        if error:
            cls += " dj-autocomplete--error"
        if custom_class:
            cls += f" {custom_class}"

        label_html = ""
        if label:
            req_span = '<span class="form-required"> *</span>' if required else ""
            label_html = f'<label class="form-label" for="{name}">{label}{req_span}</label>'

        error_html = f'<span class="form-error-message">{error}</span>' if error else ""

        # Build suggestion items
        suggestion_items = []
        for sug in suggestions:
            if isinstance(sug, dict):
                sv = conditional_escape(str(sug.get("value", "")))
                sl = conditional_escape(str(sug.get("label", sv)))
            elif isinstance(sug, (list, tuple)) and len(sug) >= 2:
                sv = conditional_escape(str(sug[0]))
                sl = conditional_escape(str(sug[1]))
            else:
                sv = sl = conditional_escape(str(sug))
            suggestion_items.append(
                f'<li class="dj-autocomplete__item" role="option" data-value="{sv}">{sl}</li>'
            )

        dropdown_cls = "dj-autocomplete__dropdown"
        if not suggestion_items:
            dropdown_cls += " dj-autocomplete__dropdown--hidden"

        suggestions_html = (
            f'<ul class="{dropdown_cls}" role="listbox">{"".join(suggestion_items)}</ul>'
        )

        spinner_html = (
            '<span class="dj-autocomplete__spinner" aria-hidden="true"></span>' if loading else ""
        )

        return mark_safe(
            f'<div class="form-group">'
            f"{label_html}"
            f'<div class="{cls}" data-source-event="{source_event}" '
            f'data-debounce="{debounce}" data-min-chars="{min_chars}">'
            f'<input type="text" class="dj-autocomplete__input form-input" '
            f'name="{name}_display" id="{name}" value="{display_value}" '
            f'placeholder="{placeholder}" autocomplete="off" '
            f'role="combobox" aria-autocomplete="list" '
            f'aria-expanded="{"true" if suggestion_items else "false"}" '
            f'dj-input="{source_event or event}" '
            f'data-debounce="{debounce}"{required_attr}{disabled_attr}>'
            f'<input type="hidden" name="{name}" value="{value}">'
            f"{spinner_html}"
            f"{suggestions_html}"
            f"</div>"
            f"{error_html}"
            f"</div>"
        )


INLINE_HANDLERS.extend(
    [
        ("slider", SliderHandler()),
        ("search_input", SearchInputHandler()),
        ("password_input", PasswordInputHandler()),
        ("autocomplete", AutocompleteHandler()),
    ]
)


# ===========================================================================
# CONFIRMATION PATTERNS
# ===========================================================================


class ConfirmDialogHandler:
    """Inline handler for {% confirm_dialog message="Delete?" confirm_event="delete" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        is_open = kw.get("is_open", kw.get("open", False))
        if not is_open:
            return ""

        uid = _uuid.uuid4().hex[:8]
        title_id = f"dj-confirm-title-{uid}"
        msg_id = f"dj-confirm-msg-{uid}"

        message = conditional_escape(kw.get("message", "Are you sure?"))
        confirm_event = conditional_escape(kw.get("confirm_event", "confirm"))
        cancel_event = conditional_escape(kw.get("cancel_event", "cancel"))
        title = conditional_escape(kw.get("title", "Confirm"))
        variant = conditional_escape(kw.get("variant", "default"))
        confirm_label = conditional_escape(kw.get("confirm_label", "Confirm"))
        cancel_label = conditional_escape(kw.get("cancel_label", "Cancel"))
        custom_class = conditional_escape(kw.get("custom_class", ""))

        variant_cls = f" dj-confirm-dialog--{variant}" if variant != "default" else ""
        extra_cls = f" {custom_class}" if custom_class else ""

        return mark_safe(
            f'<div class="dj-confirm-dialog-backdrop" dj-click="{cancel_event}">'
            f'<div class="dj-confirm-dialog{variant_cls}{extra_cls}" '
            f'role="alertdialog" aria-modal="true" aria-labelledby="{title_id}" '
            f'aria-describedby="{msg_id}" onclick="event.stopPropagation()">'
            f'<div class="dj-confirm-dialog__header">'
            f'<h3 class="dj-confirm-dialog__title" id="{title_id}">{title}</h3>'
            f'<button class="dj-confirm-dialog__close" dj-click="{cancel_event}" '
            f'aria-label="Close">&times;</button>'
            f"</div>"
            f'<div class="dj-confirm-dialog__body" id="{msg_id}">'
            f'<p class="dj-confirm-dialog__message">{message}</p>'
            f"</div>"
            f'<div class="dj-confirm-dialog__footer">'
            f'<button class="dj-confirm-dialog__btn dj-confirm-dialog__btn--cancel" '
            f'dj-click="{cancel_event}">{cancel_label}</button>'
            f'<button class="dj-confirm-dialog__btn dj-confirm-dialog__btn--confirm" '
            f'dj-click="{confirm_event}">{confirm_label}</button>'
            f"</div>"
            f"</div>"
            f"</div>"
        )


class PopconfirmHandler:
    """Block handler for {% popconfirm message="Delete?" %}...{% endpopconfirm %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        message = conditional_escape(kw.get("message", "Are you sure?"))
        confirm_event = conditional_escape(kw.get("confirm_event", "confirm"))
        cancel_event = conditional_escape(kw.get("cancel_event", "cancel"))
        confirm_label = conditional_escape(kw.get("confirm_label", "Yes"))
        cancel_label = conditional_escape(kw.get("cancel_label", "No"))
        placement = conditional_escape(kw.get("placement", "top"))
        variant = conditional_escape(kw.get("variant", "default"))
        custom_class = conditional_escape(kw.get("custom_class", ""))

        variant_cls = f" dj-popconfirm--{variant}" if variant != "default" else ""
        extra_cls = f" {custom_class}" if custom_class else ""

        js_toggle = (
            "(function(el){"
            "var w=el.closest('.dj-popconfirm-wrapper');"
            "w.classList.toggle('dj-popconfirm-open');"
            "document.addEventListener('click',function h(e){"
            "if(!w.contains(e.target)){"
            "w.classList.remove('dj-popconfirm-open');"
            "document.removeEventListener('click',h);"
            "}},true);"
            "})(this)"
        )

        js_close = (
            "(function(el){"
            "el.closest('.dj-popconfirm-wrapper').classList.remove('dj-popconfirm-open');"
            "})(this)"
        )

        return mark_safe(
            f'<div class="dj-popconfirm-wrapper{variant_cls}{extra_cls}">'
            f'<div class="dj-popconfirm-trigger" onclick="{js_toggle}" '
            f'aria-expanded="false" aria-haspopup="true">'
            f"{content}"
            f"</div>"
            f'<div class="dj-popconfirm dj-popconfirm-{placement}" role="tooltip">'
            f'<p class="dj-popconfirm__message">{message}</p>'
            f'<div class="dj-popconfirm__actions">'
            f'<button class="dj-popconfirm__btn dj-popconfirm__btn--cancel" '
            f'onclick="{js_close}" dj-click="{cancel_event}">{cancel_label}</button>'
            f'<button class="dj-popconfirm__btn dj-popconfirm__btn--confirm" '
            f'onclick="{js_close}" dj-click="{confirm_event}">{confirm_label}</button>'
            f"</div>"
            f"</div>"
            f"</div>"
        )


INLINE_HANDLERS.extend(
    [
        ("confirm_dialog", ConfirmDialogHandler()),
    ]
)


# ===========================================================================
# CASCADING FORM COMPONENTS
# ===========================================================================

# CURRENCY_SYMBOLS imported from djust.components.utils at module top


class DependentSelectHandler:
    """Inline handler for {% dependent_select name="city" parent="country" source_event="load_cities" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        parent = conditional_escape(kw.get("parent", ""))
        source_event = conditional_escape(kw.get("source_event", name))
        label = conditional_escape(kw.get("label", ""))
        placeholder = conditional_escape(kw.get("placeholder", "Select..."))
        value = conditional_escape(str(kw.get("value", "")))
        loading = kw.get("loading", False)
        disabled = kw.get("disabled", False)
        required = kw.get("required", False)
        error = conditional_escape(kw.get("error", ""))
        custom_class = conditional_escape(kw.get("custom_class", ""))

        options = kw.get("options", None)
        if options is None:
            options = context.get("options", [])
        if not isinstance(options, list):
            options = []

        disabled_attr = " disabled" if disabled else ""
        required_attr = " required" if required else ""

        cls = "dj-dependent-select"
        if loading:
            cls += " dj-dependent-select--loading"
        if error:
            cls += " dj-dependent-select--error"
        if custom_class:
            cls += f" {custom_class}"

        label_html = ""
        if label:
            req_mark = ' <span class="form-required">*</span>' if required else ""
            label_html = f'<label class="form-label" for="{name}">{label}{req_mark}</label>'

        opt_parts = [f'<option value="">{placeholder}</option>']
        for opt in options:
            if isinstance(opt, dict):
                ov = conditional_escape(str(opt.get("value", "")))
                ol = conditional_escape(str(opt.get("label", ov)))
            else:
                ov = conditional_escape(str(opt))
                ol = ov
            selected = " selected" if ov == value else ""
            opt_parts.append(f'<option value="{ov}"{selected}>{ol}</option>')

        spinner_html = (
            '<span class="dj-dependent-select__spinner" aria-hidden="true"></span>'
            if loading
            else ""
        )

        error_html = (
            f'<span class="form-error-message" role="alert">{error}</span>' if error else ""
        )

        return mark_safe(
            f'<div class="{cls}">'
            f"{label_html}"
            f'<div class="dj-dependent-select__control">'
            f'<select name="{name}" id="{name}" '
            f'data-parent="{parent}" '
            f'data-source-event="{source_event}" '
            f'dj-change="{source_event}"'
            f"{disabled_attr}{required_attr}>"
            f"{''.join(opt_parts)}"
            f"</select>"
            f"{spinner_html}"
            f"</div>"
            f"{error_html}"
            f"</div>"
        )


class CurrencyInputHandler:
    """Inline handler for {% currency_input name="price" currency="USD" min=0 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        currency = str(kw.get("currency", "USD")).upper()
        e_currency = conditional_escape(currency)
        value = conditional_escape(str(kw.get("value", "")))
        label = conditional_escape(kw.get("label", ""))
        placeholder = conditional_escape(kw.get("placeholder", "0.00"))
        step = conditional_escape(str(kw.get("step", "0.01")))
        event = conditional_escape(kw.get("event", name))
        disabled = kw.get("disabled", False)
        required = kw.get("required", False)
        error = conditional_escape(kw.get("error", ""))
        custom_class = conditional_escape(kw.get("custom_class", ""))

        symbol = CURRENCY_SYMBOLS.get(currency, currency)
        e_symbol = conditional_escape(symbol)

        disabled_attr = " disabled" if disabled else ""
        required_attr = " required" if required else ""
        min_val = kw.get("min_val", kw.get("min", None))
        max_val = kw.get("max_val", kw.get("max", None))
        min_attr = f' min="{conditional_escape(str(min_val))}"' if min_val is not None else ""
        max_attr = f' max="{conditional_escape(str(max_val))}"' if max_val is not None else ""

        cls = "dj-currency-input"
        if error:
            cls += " dj-currency-input--error"
        if custom_class:
            cls += f" {custom_class}"

        label_html = ""
        if label:
            req_mark = ' <span class="form-required">*</span>' if required else ""
            label_html = f'<label class="form-label" for="{name}">{label}{req_mark}</label>'

        error_html = (
            f'<span class="form-error-message" role="alert">{error}</span>' if error else ""
        )

        return mark_safe(
            f'<div class="{cls}">'
            f"{label_html}"
            f'<div class="dj-currency-input__control">'
            f'<span class="dj-currency-input__symbol">{e_symbol}</span>'
            f'<input type="number" name="{name}" id="{name}" '
            f'value="{value}" placeholder="{placeholder}" '
            f'step="{step}"{min_attr}{max_attr} '
            f'data-currency="{e_currency}" '
            f'dj-input="{event}" '
            f'class="dj-currency-input__field"'
            f"{disabled_attr}{required_attr}>"
            f'<span class="dj-currency-input__code">{e_currency}</span>'
            f"</div>"
            f"{error_html}"
            f"</div>"
        )


class FormErrorsHandler:
    """Inline handler for {% form_errors form=form %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        form = kw.get("form", None)
        if form is None:
            form = context.get("form", None)
        custom_class = conditional_escape(kw.get("custom_class", ""))

        if form is None or not hasattr(form, "non_field_errors"):
            return ""

        errors = form.non_field_errors()
        if not errors:
            return ""

        cls = "dj-form-errors"
        if custom_class:
            cls += f" {custom_class}"

        items = []
        for err in errors:
            items.append(f'<li class="dj-form-errors__item">{conditional_escape(str(err))}</li>')

        return mark_safe(
            f'<div class="{cls}" role="alert">'
            f'<ul class="dj-form-errors__list">{"".join(items)}</ul>'
            f"</div>"
        )


class FieldErrorHandler:
    """Inline handler for {% field_error field=form.email %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        field = kw.get("field", None)
        custom_class = conditional_escape(kw.get("custom_class", ""))

        if field is None:
            return ""

        if hasattr(field, "errors"):
            errors = field.errors
        else:
            return ""

        if not errors:
            return ""

        cls = "dj-field-error"
        if custom_class:
            cls += f" {custom_class}"

        items = []
        for err in errors:
            items.append(
                f'<span class="dj-field-error__message">{conditional_escape(str(err))}</span>'
            )

        return mark_safe(f'<div class="{cls}" role="alert">{"".join(items)}</div>')


INLINE_HANDLERS.extend(
    [
        ("dependent_select", DependentSelectHandler()),
        ("currency_input", CurrencyInputHandler()),
        ("form_errors", FormErrorsHandler()),
        ("field_error", FieldErrorHandler()),
    ]
)

BLOCK_HANDLERS.extend(
    [
        ("popconfirm", "endpopconfirm", PopconfirmHandler()),
    ]
)


# ===========================================================================
# APP CHROME COMPONENTS (Sidebar, Nav Menu, App Shell)
# ===========================================================================


class SidebarHandler:
    """Block handler for {% sidebar %}...{% endsidebar %}

    Renders a collapsible sidebar navigation with menu items, sections,
    and mobile drawer support.
    """

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        sidebar_id = conditional_escape(kw.get("id", "sidebar"))
        collapsed = kw.get("collapsed", False)
        title = kw.get("title", "")
        toggle_event = conditional_escape(kw.get("toggle_event", "toggle_sidebar"))
        custom_class = conditional_escape(kw.get("class", ""))

        collapsed_cls = " dj-sidebar--collapsed" if collapsed else ""
        cls = f"dj-sidebar{collapsed_cls}"
        if custom_class:
            cls += f" {custom_class}"

        header_html = ""
        if title:
            header_html = (
                f'<div class="dj-sidebar__header">'
                f'<span class="dj-sidebar__title">{conditional_escape(title)}</span>'
                f'<button class="dj-sidebar__toggle" dj-click="{toggle_event}">'
                f"&#9776;</button></div>"
            )

        backdrop = f'<div class="dj-sidebar__backdrop" dj-click="{toggle_event}"></div>'

        return mark_safe(
            f'<nav class="{cls}" id="{sidebar_id}" role="navigation">'
            f"{header_html}"
            f'<ul class="dj-sidebar__menu">{content}</ul>'
            f"{backdrop}</nav>"
        )


class SidebarItemHandler:
    """Block handler for {% sidebar_item %}...{% endsidebar_item %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        href = conditional_escape(kw.get("href", "#"))
        icon = kw.get("icon", "")
        item_id = conditional_escape(kw.get("id", ""))
        event = kw.get("event", "")
        active = conditional_escape(kw.get("active", ""))

        is_active = (item_id and item_id == active) or (href and href == active)
        active_cls = " dj-sidebar__item--active" if is_active else ""

        icon_html = ""
        if icon:
            icon_html = f'<span class="dj-sidebar__icon">{conditional_escape(icon)}</span>'

        has_children = content.strip() != ""

        if event:
            trigger = (
                f'<button class="dj-sidebar__link{active_cls}" '
                f'dj-click="{conditional_escape(event)}">'
                f'{icon_html}<span class="dj-sidebar__label">{label}</span></button>'
            )
        else:
            trigger = (
                f'<a class="dj-sidebar__link{active_cls}" href="{href}">'
                f'{icon_html}<span class="dj-sidebar__label">{label}</span></a>'
            )

        if has_children:
            return mark_safe(
                f'<li class="dj-sidebar__item dj-sidebar__item--parent">'
                f"{trigger}"
                f'<ul class="dj-sidebar__submenu">{content}</ul></li>'
            )

        return mark_safe(f'<li class="dj-sidebar__item">{trigger}</li>')


class SidebarSectionHandler:
    """Inline handler for {% sidebar_section label="..." %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        return mark_safe(
            f'<li class="dj-sidebar__section">'
            f'<span class="dj-sidebar__section-label">{label}</span></li>'
        )


class NavMenuHandler:
    """Block handler for {% nav_menu %}...{% endnav_menu %}

    Renders a top horizontal navigation bar with brand, hamburger toggle,
    and responsive mobile collapse.
    """

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        nav_id = conditional_escape(kw.get("id", "nav-menu"))
        brand = kw.get("brand", "")
        brand_href = conditional_escape(kw.get("brand_href", "/"))
        toggle_event = conditional_escape(kw.get("toggle_event", "toggle_nav"))
        mobile_open = kw.get("mobile_open", False)
        custom_class = conditional_escape(kw.get("class", ""))

        cls = "dj-nav"
        if custom_class:
            cls += f" {custom_class}"

        mobile_cls = " dj-nav__list--open" if mobile_open else ""

        brand_html = ""
        if brand:
            brand_html = (
                f'<a class="dj-nav__brand" href="{brand_href}">{conditional_escape(brand)}</a>'
            )

        hamburger = (
            f'<button class="dj-nav__hamburger" dj-click="{toggle_event}" '
            f'aria-label="Toggle navigation">&#9776;</button>'
        )

        return mark_safe(
            f'<nav class="{cls}" id="{nav_id}" role="navigation">'
            f'<div class="dj-nav__container">'
            f"{brand_html}{hamburger}"
            f'<ul class="dj-nav__list{mobile_cls}">{content}</ul>'
            f"</div></nav>"
        )


class NavItemHandler:
    """Block handler for {% nav_item %}...{% endnav_item %}

    Renders a navigation item. If content is present, it becomes a dropdown parent.
    """

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        href = conditional_escape(kw.get("href", "#"))
        event = kw.get("event", "")
        item_id = conditional_escape(kw.get("id", ""))
        active = conditional_escape(kw.get("active", ""))
        mega = kw.get("mega", False)
        description = kw.get("description", "")

        is_active = (item_id and item_id == active) or (href and href == active)
        active_cls = " dj-nav__item--active" if is_active else ""

        has_children = content.strip() != ""

        if has_children:
            mega_cls = " dj-nav__dropdown--mega" if mega else ""
            return mark_safe(
                f'<li class="dj-nav__item dj-nav__item--has-dropdown{active_cls}">'
                f'<button class="dj-nav__link">{label}'
                f'<span class="dj-nav__caret">&#9662;</span></button>'
                f'<div class="dj-nav__dropdown{mega_cls}">'
                f'<ul class="dj-nav__dropdown-list">{content}</ul></div></li>'
            )

        desc_html = ""
        if description:
            desc_html = (
                f'<span class="dj-nav__dropdown-desc">{conditional_escape(description)}</span>'
            )

        if event:
            return mark_safe(
                f'<li class="dj-nav__item{active_cls}">'
                f'<button class="dj-nav__link" dj-click="{conditional_escape(event)}">'
                f"{label}{desc_html}</button></li>"
            )

        return mark_safe(
            f'<li class="dj-nav__item{active_cls}">'
            f'<a class="dj-nav__link" href="{href}">'
            f"{label}{desc_html}</a></li>"
        )


class AppShellHandler:
    """Block handler for {% app_shell %}...{% endapp_shell %}

    Wraps sidebar, header, and content regions in a responsive layout.
    """

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        shell_id = conditional_escape(kw.get("id", "app-shell"))
        sidebar_collapsed = kw.get("sidebar_collapsed", False)
        custom_class = conditional_escape(kw.get("class", ""))

        cls = "dj-app-shell"
        if sidebar_collapsed:
            cls += " dj-app-shell--sidebar-collapsed"
        if custom_class:
            cls += f" {custom_class}"

        # In Rust block mode, content is pre-rendered; wrap in shell layout
        return mark_safe(f'<div class="{cls}" id="{shell_id}">{content}</div>')


class AppSidebarHandler:
    """Block handler for {% app_sidebar %}...{% endapp_sidebar %}"""

    def render(self, args, content, context):
        return mark_safe(f'<aside class="dj-app-shell__sidebar">{content}</aside>')


class AppHeaderHandler:
    """Block handler for {% app_header %}...{% endapp_header %}"""

    def render(self, args, content, context):
        return mark_safe(f'<header class="dj-app-shell__header">{content}</header>')


class AppContentHandler:
    """Block handler for {% app_content %}...{% endapp_content %}"""

    def render(self, args, content, context):
        return mark_safe(f'<main class="dj-app-shell__content">{content}</main>')


INLINE_HANDLERS.extend(
    [
        ("sidebar_section", SidebarSectionHandler()),
    ]
)

BLOCK_HANDLERS.extend(
    [
        ("sidebar", "endsidebar", SidebarHandler()),
        ("sidebar_item", "endsidebar_item", SidebarItemHandler()),
        ("nav_menu", "endnav_menu", NavMenuHandler()),
        ("nav_item", "endnav_item", NavItemHandler()),
        ("app_shell", "endapp_shell", AppShellHandler()),
        ("app_sidebar", "endapp_sidebar", AppSidebarHandler()),
        ("app_header", "endapp_header", AppHeaderHandler()),
        ("app_content", "endapp_content", AppContentHandler()),
    ]
)


# ---------------------------------------------------------------------------
# Toolbar (#87)
# ---------------------------------------------------------------------------


class ToolbarHandler:
    """Block handler for {% toolbar %}...{% endtoolbar %}

    Horizontal action bar with grouped buttons, separators, overflow menu.
    """

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        toolbar_id = conditional_escape(kw.get("id", "toolbar"))
        custom_class = conditional_escape(kw.get("class", ""))
        size = conditional_escape(kw.get("size", "md"))
        variant = conditional_escape(kw.get("variant", "default"))

        cls = f"dj-toolbar dj-toolbar--{size} dj-toolbar--{variant}"
        if custom_class:
            cls += f" {custom_class}"

        return mark_safe(f'<div class="{cls}" id="{toolbar_id}" role="toolbar">{content}</div>')


class ToolbarSeparatorHandler:
    """Inline handler for {% toolbar_separator %}"""

    def render(self, args, context):
        return mark_safe('<div class="dj-toolbar__separator" role="separator"></div>')


class ToolbarOverflowHandler:
    """Block handler for {% toolbar_overflow %}...{% endtoolbar_overflow %}"""

    def render(self, args, content, context):
        return mark_safe(
            f'<div class="dj-toolbar__overflow">'
            f'<button class="dj-toolbar__overflow-trigger" aria-label="More actions" '
            f'aria-expanded="false" aria-haspopup="true">'
            f'<span class="dj-toolbar__overflow-icon">&#8942;</span></button>'
            f'<div class="dj-toolbar__overflow-menu">{content}</div></div>'
        )


# ---------------------------------------------------------------------------
# Inline Edit (#88)
# ---------------------------------------------------------------------------


class InlineEditHandler:
    """Inline handler for {% inline_edit value=title event="update_title" %}

    Click text to edit in-place. Shows input on click, saves on Enter/blur,
    cancels on Escape.
    """

    def render(self, args, context):
        kw = _parse_args(args, context)
        value = conditional_escape(str(kw.get("value", "")))
        event = conditional_escape(kw.get("event", "inline_edit"))
        field = conditional_escape(kw.get("field", ""))
        input_type = conditional_escape(kw.get("type", "text"))
        placeholder = conditional_escape(kw.get("placeholder", ""))
        custom_class = conditional_escape(kw.get("class", ""))
        editing = kw.get("editing", False)

        cls = "dj-inline-edit"
        if editing:
            cls += " dj-inline-edit--editing"
        if custom_class:
            cls += f" {custom_class}"

        if editing:
            return mark_safe(
                f'<span class="{cls}">'
                f'<input class="dj-inline-edit__input" type="{input_type}" '
                f'value="{value}" placeholder="{placeholder}" '
                f'data-field="{field}" '
                f'dj-keydown.enter="{event}" '
                f'dj-blur="{event}" '
                f'dj-keydown.escape="inline_edit_cancel" '
                f"autofocus></span>"
            )
        else:
            return mark_safe(
                f'<span class="{cls}" dj-click="inline_edit_start" '
                f'data-field="{field}" title="Click to edit">'
                f'<span class="dj-inline-edit__display">{value}</span>'
                f'<span class="dj-inline-edit__icon">&#9998;</span></span>'
            )


# ---------------------------------------------------------------------------
# Filter Bar (#166)
# ---------------------------------------------------------------------------


class FilterBarHandler:
    """Block handler for {% filter_bar %}...{% endfilter_bar %}

    Horizontal bar composing filter controls with responsive collapse.
    """

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        bar_id = conditional_escape(kw.get("id", "filter-bar"))
        custom_class = conditional_escape(kw.get("class", ""))

        cls = "dj-filter-bar"
        if custom_class:
            cls += f" {custom_class}"

        return mark_safe(
            f'<div class="{cls}" id="{bar_id}" role="search">'
            f'<div class="dj-filter-bar__controls">{content}</div></div>'
        )


class FilterSelectHandler:
    """Inline handler for {% filter_select name="status" options=statuses %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", kw.get("name", "")))
        options = kw.get("options", [])
        value = kw.get("value", "")
        event = conditional_escape(kw.get("event", "filter_change"))

        opt_html = f'<option value="">{label}</option>'
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict):
                    ov = conditional_escape(str(opt.get("value", "")))
                    ol = conditional_escape(str(opt.get("label", ov)))
                    raw_v = str(opt.get("value", ""))
                else:
                    ov = conditional_escape(str(opt))
                    ol = ov
                    raw_v = str(opt)
                selected = " selected" if raw_v == str(value) else ""
                opt_html += f'<option value="{ov}"{selected}>{ol}</option>'

        return mark_safe(
            f'<div class="dj-filter-bar__control dj-filter-bar__select-wrap">'
            f'<select class="dj-filter-bar__select" name="{name}" '
            f'dj-change="{event}">{opt_html}</select></div>'
        )


class FilterDateRangeHandler:
    """Inline handler for {% filter_date_range name="dates" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        label = conditional_escape(kw.get("label", kw.get("name", "")))
        start = conditional_escape(str(kw.get("start", "")))
        end = conditional_escape(str(kw.get("end", "")))
        event = conditional_escape(kw.get("event", "filter_change"))

        return mark_safe(
            f'<div class="dj-filter-bar__control dj-filter-bar__date-range">'
            f'<label class="dj-filter-bar__label">{label}</label>'
            f'<input class="dj-filter-bar__date" type="date" name="{name}_start" '
            f'value="{start}" dj-change="{event}">'
            f'<span class="dj-filter-bar__date-sep">&ndash;</span>'
            f'<input class="dj-filter-bar__date" type="date" name="{name}_end" '
            f'value="{end}" dj-change="{event}"></div>'
        )


class FilterSearchHandler:
    """Inline handler for {% filter_search name="q" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = conditional_escape(kw.get("name", ""))
        placeholder = conditional_escape(kw.get("placeholder", "Search\u2026"))
        value = conditional_escape(str(kw.get("value", "")))
        debounce = kw.get("debounce", 300)
        event = conditional_escape(kw.get("event", "filter_change"))

        return mark_safe(
            f'<div class="dj-filter-bar__control dj-filter-bar__search-wrap">'
            f'<input class="dj-filter-bar__search" type="search" name="{name}" '
            f'placeholder="{placeholder}" value="{value}" '
            f'dj-input="{event}" dj-debounce="{int(debounce)}"></div>'
        )


INLINE_HANDLERS.extend(
    [
        ("toolbar_separator", ToolbarSeparatorHandler()),
        ("inline_edit", InlineEditHandler()),
        ("filter_select", FilterSelectHandler()),
        ("filter_date_range", FilterDateRangeHandler()),
        ("filter_search", FilterSearchHandler()),
    ]
)

BLOCK_HANDLERS.extend(
    [
        ("toolbar", "endtoolbar", ToolbarHandler()),
        ("toolbar_overflow", "endtoolbar_overflow", ToolbarOverflowHandler()),
        ("filter_bar", "endfilter_bar", FilterBarHandler()),
    ]
)


# ===========================================================================
# SOCIAL / USER-FACING COMPONENTS
# ===========================================================================


class AvatarGroupHandler:
    """Inline handler for {% avatar_group users=users max=5 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        users = kw.get("users", [])
        if isinstance(users, str):
            users = []
        max_display = int(kw.get("max", 5))
        size = conditional_escape(str(kw.get("size", "md")))
        custom_class = conditional_escape(str(kw.get("class", "")))

        visible = users[:max_display]
        overflow = len(users) - max_display

        parts = []
        for i, user in enumerate(visible):
            if isinstance(user, dict):
                name = user.get("name", "")
                src = user.get("avatar", "") or user.get("src", "")
            elif hasattr(user, "get_full_name"):
                name = user.get_full_name() or str(user)
                src = getattr(user, "avatar", "")
                if hasattr(src, "url"):
                    src = src.url
            else:
                name = str(user)
                src = ""
            e_name = conditional_escape(str(name))
            e_src = conditional_escape(str(src))
            initials = conditional_escape("".join(w[0].upper() for w in str(name).split()[:2] if w))
            z = len(visible) - i
            if e_src:
                parts.append(
                    f'<span class="dj-avatar-group__item" title="{e_name}" '
                    f'style="z-index:{z}">'
                    f'<img src="{e_src}" alt="{e_name}" '
                    f'class="dj-avatar-group__img"></span>'
                )
            else:
                parts.append(
                    f'<span class="dj-avatar-group__item '
                    f'dj-avatar-group__initials" title="{e_name}" '
                    f'style="z-index:{z}">{initials}</span>'
                )

        overflow_html = ""
        if overflow > 0:
            overflow_html = (
                f'<span class="dj-avatar-group__item dj-avatar-group__overflow">+{overflow}</span>'
            )

        cls = f"dj-avatar-group dj-avatar-group--{size}"
        if custom_class:
            cls += f" {custom_class}"
        return mark_safe(f'<div class="{cls}">{"".join(parts)}{overflow_html}</div>')


class HoverCardHandler:
    """Block handler for {% hover_card trigger="@user" %}...{% endhover_card %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        trigger = conditional_escape(str(kw.get("trigger", "")))
        position = conditional_escape(str(kw.get("position", "bottom")))
        delay_in = int(kw.get("delay_in", 200))
        delay_out = int(kw.get("delay_out", 300))
        custom_class = conditional_escape(str(kw.get("class", "")))

        cls = f"dj-hover-card dj-hover-card--{position}"
        if custom_class:
            cls += f" {custom_class}"
        return mark_safe(
            f'<span class="{cls}" data-delay-in="{delay_in}" '
            f'data-delay-out="{delay_out}">'
            f'<span class="dj-hover-card__trigger" tabindex="0">{trigger}</span>'
            f'<div class="dj-hover-card__content">{content}</div>'
            f"</span>"
        )


class NotificationPopoverHandler:
    """Inline handler for {% notification_popover notifications=notifs %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        notifications = kw.get("notifications", [])
        if isinstance(notifications, str):
            notifications = []
        unread_count = int(kw.get("unread_count", 0))
        mark_read_event = conditional_escape(str(kw.get("mark_read_event", "mark_read")))
        toggle_event = conditional_escape(str(kw.get("toggle_event", "toggle_notifications")))
        is_open = kw.get("open", False)
        custom_class = conditional_escape(str(kw.get("class", "")))
        title = conditional_escape(str(kw.get("title", "Notifications")))

        badge_html = ""
        if unread_count > 0:
            display = "99+" if unread_count > 99 else str(unread_count)
            badge_html = f'<span class="dj-notif-popover__badge">{display}</span>'

        open_cls = "dj-notif-popover--open" if is_open else ""
        cls = f"dj-notif-popover {open_cls}"
        if custom_class:
            cls += f" {custom_class}"

        bell_html = (
            f'<button class="dj-notif-popover__bell" dj-click="{toggle_event}" '
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
        for notif in notifications:
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
            e_n_id = conditional_escape(str(n_id))
            e_n_title = conditional_escape(str(n_title))
            e_n_body = conditional_escape(str(n_body))
            e_n_time = conditional_escape(str(n_time))
            read_cls = "dj-notif-popover__item--read" if n_read else ""
            mark_attr = ""
            if not n_read:
                mark_attr = f' dj-click="{mark_read_event}" data-id="{e_n_id}"'
            items_html.append(
                f'<div class="dj-notif-popover__item {read_cls}"{mark_attr}>'
                f'<div class="dj-notif-popover__item-title">{e_n_title}</div>'
                f'<div class="dj-notif-popover__item-body">{e_n_body}</div>'
                f'<div class="dj-notif-popover__item-time">{e_n_time}</div>'
                f"</div>"
            )

        panel_html = ""
        if is_open:
            empty = ""
            if not notifications:
                empty = '<div class="dj-notif-popover__empty">No notifications</div>'
            panel_html = (
                f'<div class="dj-notif-popover__panel">'
                f'<div class="dj-notif-popover__header">{title}</div>'
                f"{''.join(items_html)}{empty}"
                f"</div>"
            )

        return mark_safe(f'<div class="{cls}">{bell_html}{panel_html}</div>')


INLINE_HANDLERS.extend(
    [
        ("avatar_group", AvatarGroupHandler()),
        ("notification_popover", NotificationPopoverHandler()),
    ]
)

BLOCK_HANDLERS.extend(
    [
        ("hover_card", "endhover_card", HoverCardHandler()),
    ]
)


# ===========================================================================
# AI CHAT INTERFACE HANDLERS
# ===========================================================================


class ConversationThreadHandler:
    """Inline handler for {% conversation_thread messages=... %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        messages = kw.get("messages", [])
        stream_event = kw.get("stream_event", "new_message")
        streaming = kw.get("streaming", False)
        custom_class = kw.get("class", "")

        e_stream = conditional_escape(str(stream_event))
        e_class = conditional_escape(str(custom_class))

        cls = "dj-chat-thread"
        if e_class:
            cls += f" {e_class}"

        msgs_html = []
        prev_sender = None
        if not isinstance(messages, list):
            messages = []
        for msg in messages:
            if isinstance(msg, dict):
                sender = msg.get("sender", "user")
                name = msg.get("name", "")
                text = msg.get("text", "")
                time = msg.get("time", "")
            else:
                sender = getattr(msg, "sender", "user")
                name = getattr(msg, "name", "")
                text = getattr(msg, "text", "")
                time = getattr(msg, "time", "")

            e_name = conditional_escape(str(name))
            e_text = conditional_escape(str(text))
            e_time = conditional_escape(str(time))

            grouped = "dj-chat-msg--grouped" if sender == prev_sender else ""
            side = "dj-chat-msg--ai" if sender == "ai" else "dj-chat-msg--user"

            initials = str(name)[:1].upper() if name else "?"
            avatar = (
                f'<span class="dj-chat-avatar">{conditional_escape(initials)}</span>'
                if sender != prev_sender
                else '<span class="dj-chat-avatar dj-chat-avatar--hidden"></span>'
            )

            header = ""
            if sender != prev_sender:
                header = (
                    f'<div class="dj-chat-msg__header">'
                    f'<span class="dj-chat-msg__name">{e_name}</span>'
                    f'<span class="dj-chat-msg__time">{e_time}</span>'
                    f"</div>"
                )

            msgs_html.append(
                f'<div class="dj-chat-msg {side} {grouped}">'
                f"{avatar}"
                f'<div class="dj-chat-bubble">'
                f"{header}"
                f'<div class="dj-chat-msg__text">{e_text}</div>'
                f"</div></div>"
            )
            prev_sender = sender

        streaming_html = ""
        if streaming:
            streaming_html = (
                '<div class="dj-chat-msg dj-chat-msg--ai">'
                '<span class="dj-chat-avatar">&#8943;</span>'
                '<div class="dj-chat-bubble">'
                '<div class="dj-chat-typing">'
                '<span class="dj-chat-typing__dot"></span>'
                '<span class="dj-chat-typing__dot"></span>'
                '<span class="dj-chat-typing__dot"></span>'
                "</div></div></div>"
            )

        return mark_safe(
            f'<div class="{cls}" data-stream-event="{e_stream}">'
            f"{''.join(msgs_html)}{streaming_html}"
            f"</div>"
        )


class ThinkingIndicatorHandler:
    """Inline handler for {% thinking_indicator status=... %}"""

    VALID_STATUSES = {"thinking", "searching", "generating", "tool_use", "idle"}

    def render(self, args, context):
        kw = _parse_args(args, context)
        status = kw.get("status", "thinking")
        label = kw.get("label", "")
        custom_class = kw.get("class", "")

        safe_status = status if status in self.VALID_STATUSES else "thinking"
        if safe_status == "idle":
            return ""

        e_label = conditional_escape(str(label)) if label else ""
        e_class = conditional_escape(str(custom_class))

        cls = f"dj-thinking dj-thinking--{safe_status}"
        if e_class:
            cls += f" {e_class}"

        if safe_status == "thinking":
            anim = (
                '<span class="dj-thinking__dots">'
                '<span class="dj-thinking__dot"></span>'
                '<span class="dj-thinking__dot"></span>'
                '<span class="dj-thinking__dot"></span>'
                "</span>"
            )
        elif safe_status == "searching":
            anim = '<span class="dj-thinking__pulse"></span>'
        elif safe_status == "generating":
            anim = '<span class="dj-thinking__cursor"></span>'
        else:
            anim = '<span class="dj-thinking__spinner"></span>'

        label_html = f'<span class="dj-thinking__label">{e_label}</span>' if e_label else ""

        return mark_safe(
            f'<div class="{cls}" role="status" aria-label="{e_label or safe_status}">'
            f"{anim}{label_html}"
            f"</div>"
        )


class MultimodalInputHandler:
    """Inline handler for {% multimodal_input name=... event=... %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = kw.get("name", "message")
        event = kw.get("event", "send")
        placeholder = kw.get("placeholder", "Type a message...")
        accept_files = kw.get("accept_files", False)
        accept_voice = kw.get("accept_voice", False)
        file_accept = kw.get("file_accept", "*/*")
        disabled = kw.get("disabled", False)
        custom_class = kw.get("class", "")

        e_name = conditional_escape(str(name))
        e_event = conditional_escape(str(event))
        e_placeholder = conditional_escape(str(placeholder))
        e_accept = conditional_escape(str(file_accept))
        e_class = conditional_escape(str(custom_class))
        disabled_attr = " disabled" if disabled else ""

        cls = "dj-mminput"
        if disabled:
            cls += " dj-mminput--disabled"
        if e_class:
            cls += f" {e_class}"

        textarea = (
            f'<textarea class="dj-mminput__text" name="{e_name}" '
            f'placeholder="{e_placeholder}" rows="1"{disabled_attr}></textarea>'
        )

        file_btn = ""
        if accept_files:
            file_btn = (
                f'<label class="dj-mminput__btn dj-mminput__file-btn" title="Attach file">'
                f'<input type="file" accept="{e_accept}" hidden{disabled_attr}>'
                f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                f'stroke-width="2" width="18" height="18">'
                f'<path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/>'
                f"</svg></label>"
            )

        voice_btn = ""
        if accept_voice:
            voice_btn = (
                f'<button type="button" class="dj-mminput__btn dj-mminput__voice-btn" '
                f'title="Voice input"{disabled_attr}>'
                f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                f'stroke-width="2" width="18" height="18">'
                f'<path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/>'
                f'<path d="M19 10v2a7 7 0 01-14 0v-2"/>'
                f'<line x1="12" y1="19" x2="12" y2="23"/>'
                f'<line x1="8" y1="23" x2="16" y2="23"/>'
                f"</svg></button>"
            )

        send_btn = (
            f'<button type="button" class="dj-mminput__btn dj-mminput__send-btn" '
            f'dj-click="{e_event}" title="Send"{disabled_attr}>'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" width="18" height="18">'
            f'<line x1="22" y1="2" x2="11" y2="13"/>'
            f'<polygon points="22 2 15 22 11 13 2 9 22 2"/>'
            f"</svg></button>"
        )

        return mark_safe(f'<div class="{cls}">{file_btn}{voice_btn}{textarea}{send_btn}</div>')


class FeedbackWidgetHandler:
    """Inline handler for {% feedback event=... mode=... %}"""

    VALID_MODES = {"thumbs", "stars", "emoji"}

    def render(self, args, context):
        kw = _parse_args(args, context)
        event = kw.get("event", "rate_response")
        mode = kw.get("mode", "thumbs")
        value = kw.get("value", None)
        custom_class = kw.get("class", "")

        if mode not in self.VALID_MODES:
            mode = "thumbs"

        e_event = conditional_escape(str(event))
        e_class = conditional_escape(str(custom_class))

        cls = f"dj-feedback dj-feedback--{mode}"
        if e_class:
            cls += f" {e_class}"

        if mode == "thumbs":
            buttons = self._render_thumbs(e_event, value)
        elif mode == "stars":
            buttons = self._render_stars(e_event, value)
        else:
            buttons = self._render_emoji(e_event, value)

        return mark_safe(f'<div class="{cls}" role="group" aria-label="Feedback">{buttons}</div>')

    def _render_thumbs(self, e_event, value):
        up_cls = "dj-feedback__btn--active" if value == "up" else ""
        down_cls = "dj-feedback__btn--active" if value == "down" else ""
        return (
            f'<button class="dj-feedback__btn {up_cls}" '
            f'dj-click="{e_event}" data-value="up" aria-label="Thumbs up">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" width="18" height="18">'
            f'<path d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14z"/>'
            f'<path d="M7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"/>'
            f"</svg></button>"
            f'<button class="dj-feedback__btn {down_cls}" '
            f'dj-click="{e_event}" data-value="down" aria-label="Thumbs down">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" width="18" height="18">'
            f'<path d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3H10z"/>'
            f'<path d="M17 2h2.67A2.31 2.31 0 0122 4v7a2.31 2.31 0 01-2.33 2H17"/>'
            f"</svg></button>"
        )

    def _render_stars(self, e_event, value):
        parts = []
        current = int(value) if value and str(value).isdigit() else 0
        for i in range(1, 6):
            active = "dj-feedback__star--active" if i <= current else ""
            fill = "currentColor" if i <= current else "none"
            parts.append(
                f'<button class="dj-feedback__btn dj-feedback__star {active}" '
                f'dj-click="{e_event}" data-value="{i}" aria-label="{i} star">'
                f'<svg viewBox="0 0 24 24" fill="{fill}" stroke="currentColor" '
                f'stroke-width="2" width="18" height="18">'
                f'<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>'
                f"</svg></button>"
            )
        return "".join(parts)

    def _render_emoji(self, e_event, value):
        emojis = [
            ("\U0001f44d", "thumbs_up"),
            ("\u2764\ufe0f", "heart"),
            ("\U0001f60a", "smile"),
            ("\U0001f914", "thinking"),
            ("\U0001f44e", "thumbs_down"),
        ]
        parts = []
        for emoji, val in emojis:
            active = "dj-feedback__btn--active" if value == val else ""
            e_val = conditional_escape(val)
            parts.append(
                f'<button class="dj-feedback__btn {active}" '
                f'dj-click="{e_event}" data-value="{e_val}" aria-label="{e_val}">'
                f"{emoji}</button>"
            )
        return "".join(parts)


INLINE_HANDLERS.extend(
    [
        ("conversation_thread", ConversationThreadHandler()),
        ("thinking_indicator", ThinkingIndicatorHandler()),
        ("multimodal_input", MultimodalInputHandler()),
        ("feedback", FeedbackWidgetHandler()),
    ]
)


# ===========================================================================
# AI TRUST / TRANSPARENCY HANDLERS
# ===========================================================================


class ApprovalGateHandler:
    """Inline handler for {% approval_gate message=... risk=... %}"""

    VALID_RISKS = {"low", "medium", "high", "critical"}

    def render(self, args, context):
        kw = _parse_args(args, context)
        message = kw.get("message", "")
        risk = kw.get("risk", "medium")
        approve_event = kw.get("approve_event", "approve")
        reject_event = kw.get("reject_event", "reject")
        approve_label = kw.get("approve_label", "Approve")
        reject_label = kw.get("reject_label", "Reject")
        custom_class = kw.get("class", "")

        if risk not in self.VALID_RISKS:
            risk = "medium"

        e_msg = conditional_escape(str(message))
        e_approve_evt = conditional_escape(str(approve_event))
        e_reject_evt = conditional_escape(str(reject_event))
        e_approve_lbl = conditional_escape(str(approve_label))
        e_reject_lbl = conditional_escape(str(reject_label))
        e_class = conditional_escape(str(custom_class))

        cls = f"dj-approval dj-approval--{risk}"
        if e_class:
            cls += f" {e_class}"

        risk_label = risk.capitalize()

        if risk in ("high", "critical"):
            icon = (
                '<svg class="dj-approval__icon" viewBox="0 0 24 24" fill="none" '
                'stroke="currentColor" stroke-width="2" width="20" height="20">'
                '<path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>'
                '<path d="M12 9v4M12 17h.01"/></svg>'
            )
        else:
            icon = (
                '<svg class="dj-approval__icon" viewBox="0 0 24 24" fill="none" '
                'stroke="currentColor" stroke-width="2" width="20" height="20">'
                '<circle cx="12" cy="12" r="10"/>'
                '<path d="M12 16v-4M12 8h.01"/></svg>'
            )

        return mark_safe(
            f'<div class="{cls}" role="alert">'
            f'<div class="dj-approval__header">'
            f"{icon}"
            f'<span class="dj-approval__risk">{risk_label} Risk</span>'
            f"</div>"
            f'<div class="dj-approval__message">{e_msg}</div>'
            f'<div class="dj-approval__actions">'
            f'<button class="dj-approval__btn dj-approval__btn--reject" '
            f'dj-click="{e_reject_evt}">{e_reject_lbl}</button>'
            f'<button class="dj-approval__btn dj-approval__btn--approve" '
            f'dj-click="{e_approve_evt}">{e_approve_lbl}</button>'
            f"</div>"
            f"</div>"
        )


class SourceCitationHandler:
    """Inline handler for {% source_citation index=1 title=... %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        index = kw.get("index", 1)
        title = kw.get("title", "")
        url = kw.get("url", "")
        relevance = kw.get("relevance", None)
        custom_class = kw.get("class", "")

        try:
            idx = int(index)
        except (ValueError, TypeError):
            idx = 1

        e_title = conditional_escape(str(title)) if title else ""
        e_url = conditional_escape(str(url)) if url else ""
        e_class = conditional_escape(str(custom_class))

        cls = "dj-citation"
        if e_class:
            cls += f" {e_class}"

        popover_parts = []
        if e_title:
            popover_parts.append(f'<span class="dj-citation__title">{e_title}</span>')
        if e_url:
            popover_parts.append(
                f'<a class="dj-citation__url" href="{e_url}" '
                f'target="_blank" rel="noopener noreferrer">{e_url}</a>'
            )
        if relevance is not None:
            try:
                pct = min(100, max(0, float(relevance) * 100))
                popover_parts.append(
                    f'<span class="dj-citation__relevance">Relevance: {pct:.0f}%</span>'
                )
            except (ValueError, TypeError):
                pass

        popover_html = "".join(popover_parts)

        return mark_safe(
            f'<span class="{cls}" tabindex="0">'
            f'<sup class="dj-citation__marker">[{idx}]</sup>'
            f'<span class="dj-citation__popover">{popover_html}</span>'
            f"</span>"
        )


class ModelSelectorHandler:
    """Inline handler for {% model_selector name=... options=... %}"""

    TIER_LABELS = {
        "free": "Free",
        "standard": "Standard",
        "premium": "Premium",
        "enterprise": "Enterprise",
    }

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = kw.get("name", "model")
        options = kw.get("options", [])
        value = str(kw.get("value", "")) if kw.get("value") else ""
        event = kw.get("event", "select_model")
        placeholder = kw.get("placeholder", "Select a model...")
        disabled = kw.get("disabled", False)
        label = kw.get("label", "")
        custom_class = kw.get("class", "")

        e_name = conditional_escape(str(name))
        e_event = conditional_escape(str(event or name))
        e_placeholder = conditional_escape(str(placeholder))
        e_class = conditional_escape(str(custom_class))
        disabled_attr = " disabled" if disabled else ""
        disabled_cls = " dj-model-sel--disabled" if disabled else ""

        cls = f"dj-model-sel{disabled_cls}"
        if e_class:
            cls += f" {e_class}"

        if not isinstance(options, list):
            options = []

        selected_opt = None
        for opt in options:
            if isinstance(opt, dict) and str(opt.get("value", "")) == value:
                selected_opt = opt
                break

        if selected_opt:
            selected_html = self._option_inner(selected_opt)
        else:
            selected_html = f'<span class="dj-model-sel__placeholder">{e_placeholder}</span>'

        opt_parts = []
        for opt in options:
            if not isinstance(opt, dict):
                continue
            ov = str(opt.get("value", ""))
            active_cls = " dj-model-sel__opt--active" if ov == value else ""
            inner = self._option_inner(opt)
            opt_parts.append(
                f'<div class="dj-model-sel__opt{active_cls}" '
                f'data-value="{conditional_escape(ov)}" '
                f'dj-click="{e_event}" '
                f'role="option" aria-selected="{"true" if ov == value else "false"}">'
                f"{inner}</div>"
            )

        label_html = ""
        if label:
            label_html = (
                f'<label class="dj-model-sel__label">{conditional_escape(str(label))}</label>'
            )

        return mark_safe(
            f'<div class="{cls}">'
            f"{label_html}"
            f'<input type="hidden" name="{e_name}" value="{conditional_escape(value)}">'
            f'<div class="dj-model-sel__trigger" tabindex="0" role="combobox" '
            f'aria-expanded="false" aria-haspopup="listbox"{disabled_attr}>'
            f"{selected_html}"
            f'<span class="dj-model-sel__chevron">&#9662;</span>'
            f"</div>"
            f'<div class="dj-model-sel__dropdown" role="listbox">'
            f"{''.join(opt_parts)}"
            f"</div></div>"
        )

    def _option_inner(self, opt):
        label = conditional_escape(str(opt.get("label", "")))
        desc = conditional_escape(str(opt.get("description", ""))) if opt.get("description") else ""
        ctx_win = (
            conditional_escape(str(opt.get("context_window", "")))
            if opt.get("context_window")
            else ""
        )
        tier = str(opt.get("tier", "")).lower()
        tier_label = (
            conditional_escape(self.TIER_LABELS.get(tier, tier.capitalize())) if tier else ""
        )

        parts = [f'<span class="dj-model-sel__name">{label}</span>']
        if desc:
            parts.append(f'<span class="dj-model-sel__desc">{desc}</span>')

        meta = []
        if ctx_win:
            meta.append(f'<span class="dj-model-sel__ctx">{ctx_win}</span>')
        if tier_label:
            safe_tier = conditional_escape(tier)
            meta.append(
                f'<span class="dj-model-sel__tier dj-model-sel__tier--{safe_tier}">'
                f"{tier_label}</span>"
            )
        if meta:
            parts.append(f'<span class="dj-model-sel__meta">{"".join(meta)}</span>')

        return f'<span class="dj-model-sel__info">{"".join(parts)}</span>'


class TokenCounterHandler:
    """Inline handler for {% token_counter current=... max=... %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        try:
            current = int(kw.get("current", 0))
        except (ValueError, TypeError):
            current = 0
        try:
            max_tokens = int(kw.get("max", 4096))
        except (ValueError, TypeError):
            max_tokens = 4096

        label = kw.get("label", None)
        show_label = kw.get("show_label", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))

        if max_tokens <= 0:
            pct = 0.0
        else:
            pct = min(100.0, max(0.0, (current / max_tokens) * 100))

        if pct >= 85:
            threshold = "dj-token--danger"
        elif pct >= 60:
            threshold = "dj-token--warn"
        else:
            threshold = "dj-token--ok"

        cls = f"dj-token {threshold}"
        if e_class:
            cls += f" {e_class}"

        label_html = ""
        if show_label:
            if label:
                display_label = conditional_escape(str(label))
            else:
                display_label = f"{current:,} / {max_tokens:,}"
            label_html = f'<span class="dj-token__label">{display_label}</span>'

        return mark_safe(
            f'<div class="{cls}" role="meter" '
            f'aria-valuenow="{current}" aria-valuemin="0" aria-valuemax="{max_tokens}" '
            f'aria-label="Token usage">'
            f"{label_html}"
            f'<div class="dj-token__track">'
            f'<div class="dj-token__bar" style="width:{pct:.1f}%"></div>'
            f"</div>"
            f"</div>"
        )


INLINE_HANDLERS.extend(
    [
        ("approval_gate", ApprovalGateHandler()),
        ("source_citation", SourceCitationHandler()),
        ("model_selector", ModelSelectorHandler()),
        ("token_counter", TokenCounterHandler()),
    ]
)


# ===========================================================================
# COLLABORATION COMPONENT HANDLERS
# ===========================================================================


class ChatBubbleHandler:
    """Inline handler for {% chat_bubble message=... %}"""

    VALID_STATUSES = {"sending", "sent", "delivered", "read", "error"}

    def render(self, args, context):
        kw = _parse_args(args, context)
        message = kw.get("message", {})
        custom_class = kw.get("class", "")

        if not isinstance(message, dict):
            message = {}

        sender = message.get("sender", "user")
        name = message.get("name", "")
        text = message.get("text", "")
        time_str = message.get("time", "")
        avatar_src = message.get("avatar", "")
        status = message.get("status", "")

        e_name = conditional_escape(str(name))
        e_text = conditional_escape(str(text))
        e_time = conditional_escape(str(time_str))
        e_avatar = conditional_escape(str(avatar_src))
        e_class = conditional_escape(str(custom_class))

        side = "dj-bubble--user" if sender == "user" else "dj-bubble--other"
        cls = f"dj-bubble {side}"
        if e_class:
            cls += f" {e_class}"

        initials = conditional_escape(
            "".join(w[0].upper() for w in str(name).split()[:2] if w) or "?"
        )
        if e_avatar:
            avatar_html = (
                f'<span class="dj-bubble__avatar">'
                f'<img src="{e_avatar}" alt="{e_name}" class="dj-bubble__avatar-img">'
                f"</span>"
            )
        else:
            avatar_html = (
                f'<span class="dj-bubble__avatar dj-bubble__avatar--initials">{initials}</span>'
            )

        status_html = ""
        if status and status in self.VALID_STATUSES:
            e_status = conditional_escape(str(status))
            status_icons = {
                "sending": "&#8987;",
                "sent": "&#10003;",
                "delivered": "&#10003;&#10003;",
                "read": "&#10003;&#10003;",
                "error": "&#9888;",
            }
            icon = status_icons.get(status, "")
            status_html = (
                f'<span class="dj-bubble__status dj-bubble__status--{e_status}" '
                f'aria-label="{e_status}">{icon}</span>'
            )

        header_html = ""
        if e_name or e_time:
            name_part = f'<span class="dj-bubble__name">{e_name}</span>' if e_name else ""
            time_part = f'<span class="dj-bubble__time">{e_time}</span>' if e_time else ""
            header_html = f'<div class="dj-bubble__header">{name_part}{time_part}</div>'

        footer_html = ""
        if status_html:
            footer_html = f'<div class="dj-bubble__footer">{status_html}</div>'

        return mark_safe(
            f'<div class="{cls}">'
            f"{avatar_html}"
            f'<div class="dj-bubble__content">'
            f"{header_html}"
            f'<div class="dj-bubble__text">{e_text}</div>'
            f"{footer_html}"
            f"</div>"
            f"</div>"
        )


class PresenceAvatarsHandler:
    """Inline handler for {% presence_avatars users=... %}"""

    VALID_STATUSES = {"online", "away", "busy", "offline"}

    def render(self, args, context):
        kw = _parse_args(args, context)
        users = kw.get("users", [])
        max_display = int(kw.get("max", 5))
        custom_class = kw.get("class", "")

        if not isinstance(users, list):
            users = []

        e_class = conditional_escape(str(custom_class))

        visible = users[:max_display]
        overflow = len(users) - max_display

        parts = []
        for i, user in enumerate(visible):
            if isinstance(user, dict):
                name = user.get("name", "")
                src = user.get("avatar", "") or user.get("src", "")
                status = user.get("status", "online")
            else:
                name = str(user)
                src = ""
                status = "online"

            e_name = conditional_escape(str(name))
            e_src = conditional_escape(str(src))
            safe_status = status if status in self.VALID_STATUSES else "online"
            initials = conditional_escape(
                "".join(w[0].upper() for w in str(name).split()[:2] if w) or "?"
            )
            z = len(visible) - i

            if e_src:
                avatar_inner = f'<img src="{e_src}" alt="{e_name}" class="dj-presence__img">'
            else:
                avatar_inner = f'<span class="dj-presence__initials">{initials}</span>'

            dot = f'<span class="dj-presence__dot dj-presence__dot--{safe_status}"></span>'

            parts.append(
                f'<span class="dj-presence__item" title="{e_name}" '
                f'style="z-index:{z}">'
                f"{avatar_inner}{dot}"
                f"</span>"
            )

        if overflow > 0:
            parts.append(
                f'<span class="dj-presence__item dj-presence__overflow">+{overflow}</span>'
            )

        cls = "dj-presence"
        if e_class:
            cls += f" {e_class}"

        total = len(users)
        label = f"{total} user{'s' if total != 1 else ''} present"

        return mark_safe(
            f'<div class="{cls}" role="group" aria-label="{label}">{"".join(parts)}</div>'
        )


class MentionsInputHandler:
    """Inline handler for {% mentions_input name=... users=... %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = kw.get("name", "message")
        users = kw.get("users", [])
        event = kw.get("event", "send")
        placeholder = kw.get("placeholder", "Type @ to mention...")
        disabled = kw.get("disabled", False)
        custom_class = kw.get("class", "")

        if not isinstance(users, list):
            users = []

        e_name = conditional_escape(str(name))
        e_event = conditional_escape(str(event))
        e_placeholder = conditional_escape(str(placeholder))
        e_class = conditional_escape(str(custom_class))
        disabled_attr = " disabled" if disabled else ""

        cls = "dj-mentions"
        if disabled:
            cls += " dj-mentions--disabled"
        if e_class:
            cls += f" {e_class}"

        items_html = []
        for user in users:
            if not isinstance(user, dict):
                continue
            uid = conditional_escape(str(user.get("id", "")))
            uname = conditional_escape(str(user.get("name", "")))
            avatar_src = conditional_escape(str(user.get("avatar", "")))

            initials = (
                conditional_escape(
                    "".join(w[0].upper() for w in str(user.get("name", "")).split()[:2] if w)
                )
                or "?"
            )

            if avatar_src:
                avatar_html = (
                    f'<img src="{avatar_src}" alt="{uname}" class="dj-mentions__avatar-img">'
                )
            else:
                avatar_html = f'<span class="dj-mentions__avatar-initials">{initials}</span>'

            items_html.append(
                f'<li class="dj-mentions__item" data-user-id="{uid}" '
                f'data-user-name="{uname}" role="option">'
                f'<span class="dj-mentions__avatar">{avatar_html}</span>'
                f'<span class="dj-mentions__name">{uname}</span>'
                f"</li>"
            )

        users_json = conditional_escape(_json.dumps(users, default=str))

        return mark_safe(
            f'<div class="{cls}" dj-hook="MentionsInput" '
            f'data-users="{users_json}">'
            f'<input type="text" class="dj-mentions__input" name="{e_name}" '
            f'placeholder="{e_placeholder}" autocomplete="off"{disabled_attr} '
            f'dj-keydown.enter="{e_event}">'
            f'<ul class="dj-mentions__dropdown" role="listbox">'
            f"{''.join(items_html)}"
            f"</ul>"
            f"</div>"
        )


INLINE_HANDLERS.extend(
    [
        ("chat_bubble", ChatBubbleHandler()),
        ("presence_avatars", PresenceAvatarsHandler()),
        ("mentions_input", MentionsInputHandler()),
    ]
)


# ===========================================================================
# TEXT DISPLAY + LOADING PATTERN HANDLERS
# ===========================================================================


class ExpandableTextHandler:
    """Block handler for {% expandable_text max_lines=3 %}...{% endexpandable_text %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        max_lines = int(kw.get("max_lines", 3))
        expanded = kw.get("expanded", False)
        toggle_event = kw.get("toggle_event", "toggle_expand")
        more_label = kw.get("more_label", "Read more")
        less_label = kw.get("less_label", "Show less")
        custom_class = kw.get("class", "")

        e_event = conditional_escape(str(toggle_event))
        e_more = conditional_escape(str(more_label))
        e_less = conditional_escape(str(less_label))
        e_class = conditional_escape(str(custom_class))

        cls = "dj-expandable-text"
        if expanded:
            cls += " dj-expandable-text--expanded"
        if e_class:
            cls += f" {e_class}"

        if expanded:
            style = ""
            label = e_less
        else:
            style = (
                f' style="-webkit-line-clamp:{max_lines};'
                f'display:-webkit-box;-webkit-box-orient:vertical;overflow:hidden"'
            )
            label = e_more

        return mark_safe(
            f'<div class="{cls}">'
            f'<div class="dj-expandable-text__content"{style}>{content}</div>'
            f'<button class="dj-expandable-text__toggle" dj-click="{e_event}">'
            f"{label}</button>"
            f"</div>"
        )


class TruncatedListHandler:
    """Inline handler for {% truncated_list items=items max=3 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        items = kw.get("items", [])
        max_count = int(kw.get("max", 3))
        expanded = kw.get("expanded", False)
        toggle_event = kw.get("toggle_event", "toggle_list")
        overflow_label = kw.get("overflow_label", "+{count} more")
        custom_class = kw.get("class", "")

        if not isinstance(items, (list, tuple)):
            items = []

        e_class = conditional_escape(str(custom_class))
        cls = "dj-truncated-list"
        if expanded:
            cls += " dj-truncated-list--expanded"
        if e_class:
            cls += f" {e_class}"

        total = len(items)
        visible = items if expanded else items[:max_count]
        hidden_count = max(0, total - max_count)

        items_html = []
        for item in visible:
            if isinstance(item, dict):
                label = conditional_escape(str(item.get("label", item.get("name", ""))))
            else:
                label = conditional_escape(str(item))
            items_html.append(f'<span class="dj-truncated-list__item">{label}</span>')

        overflow_html = ""
        if hidden_count > 0:
            e_event = conditional_escape(str(toggle_event))
            if expanded:
                overflow_text = conditional_escape("Show less")
            else:
                overflow_text = conditional_escape(
                    str(overflow_label).replace("{count}", str(hidden_count))
                )
            overflow_html = (
                f'<button class="dj-truncated-list__overflow" dj-click="{e_event}">'
                f"{overflow_text}</button>"
            )

        return mark_safe(
            f'<div class="{cls}" role="list">{"".join(items_html)}{overflow_html}</div>'
        )


class MarkdownTextareaHandler:
    """Inline handler for {% markdown_textarea name="content" preview=True %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = kw.get("name", "content")
        value = kw.get("value", "")
        preview = kw.get("preview", False)
        toggle_event = kw.get("toggle_event", "toggle_preview")
        placeholder = kw.get("placeholder", "Write markdown here...")
        rows = int(kw.get("rows", 6))
        disabled = kw.get("disabled", False)
        custom_class = kw.get("class", "")

        e_name = conditional_escape(str(name))
        e_value = conditional_escape(str(value))
        e_event = conditional_escape(str(toggle_event))
        e_placeholder = conditional_escape(str(placeholder))
        e_class = conditional_escape(str(custom_class))
        disabled_attr = " disabled" if disabled else ""

        cls = "dj-md-textarea"
        if preview:
            cls += " dj-md-textarea--preview"
        if e_class:
            cls += f" {e_class}"

        write_active = "" if preview else " dj-md-textarea__tab--active"
        preview_active = " dj-md-textarea__tab--active" if preview else ""

        toolbar = (
            f'<div class="dj-md-textarea__toolbar">'
            f'<button type="button" class="dj-md-textarea__tab{write_active}" '
            f'dj-click="{e_event}" data-mode="write">Write</button>'
            f'<button type="button" class="dj-md-textarea__tab{preview_active}" '
            f'dj-click="{e_event}" data-mode="preview">Preview</button>'
            f"</div>"
        )

        if preview:
            body = (
                f'<div class="dj-md-textarea__preview" data-raw="{e_value}">'
                f"{e_value}</div>"
                f'<input type="hidden" name="{e_name}" value="{e_value}">'
            )
        else:
            body = (
                f'<textarea class="dj-md-textarea__input" name="{e_name}" '
                f'rows="{rows}" placeholder="{e_placeholder}"{disabled_attr}>'
                f"{e_value}</textarea>"
            )

        return mark_safe(f'<div class="{cls}" dj-hook="MarkdownTextarea">{toolbar}{body}</div>')


class SkeletonForHandler:
    """Inline handler for {% skeleton_for component="data_table" columns=5 rows=10 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        component = kw.get("component", "text")
        columns = int(kw.get("columns", 4))
        rows = int(kw.get("rows", 5))
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        cls = "dj-skeleton"
        if e_class:
            cls += f" {e_class}"

        supported = {"data_table", "card", "list", "text"}
        if component not in supported:
            component = "text"

        if component == "data_table":
            return mark_safe(self._render_table(cls, columns, rows))
        elif component == "card":
            return mark_safe(self._render_card(cls))
        elif component == "list":
            return mark_safe(self._render_list(cls, rows))
        else:
            return mark_safe(self._render_text(cls, rows))

    def _render_table(self, cls, cols, rows):
        header_cells = "".join(
            '<th><span class="dj-skeleton__line dj-skeleton__pulse" '
            'style="width:70%">&nbsp;</span></th>'
            for _ in range(cols)
        )
        header = f"<thead><tr>{header_cells}</tr></thead>"
        body_rows = []
        for _ in range(rows):
            cells = "".join(
                '<td><span class="dj-skeleton__line dj-skeleton__pulse">&nbsp;</span></td>'
                for _ in range(cols)
            )
            body_rows.append(f"<tr>{cells}</tr>")
        body = f"<tbody>{''.join(body_rows)}</tbody>"
        return (
            f'<div class="{cls} dj-skeleton--data-table" '
            f'role="status" aria-label="Loading">'
            f'<table class="dj-skeleton__table">{header}{body}</table>'
            f"</div>"
        )

    def _render_card(self, cls):
        return (
            f'<div class="{cls} dj-skeleton--card" '
            f'role="status" aria-label="Loading">'
            f'<div class="dj-skeleton__card-image dj-skeleton__pulse">&nbsp;</div>'
            f'<div class="dj-skeleton__card-body">'
            f'<span class="dj-skeleton__line dj-skeleton__pulse" '
            f'style="width:60%">&nbsp;</span>'
            f'<span class="dj-skeleton__line dj-skeleton__pulse" '
            f'style="width:90%">&nbsp;</span>'
            f'<span class="dj-skeleton__line dj-skeleton__pulse" '
            f'style="width:40%">&nbsp;</span>'
            f"</div></div>"
        )

    def _render_list(self, cls, rows):
        items = []
        for _ in range(rows):
            items.append(
                '<div class="dj-skeleton__list-item">'
                '<span class="dj-skeleton__circle dj-skeleton__pulse">&nbsp;</span>'
                '<span class="dj-skeleton__line dj-skeleton__pulse" '
                'style="width:80%">&nbsp;</span>'
                "</div>"
            )
        return (
            f'<div class="{cls} dj-skeleton--list" '
            f'role="status" aria-label="Loading">'
            f"{''.join(items)}</div>"
        )

    def _render_text(self, cls, rows):
        widths = [95, 85, 90, 70, 80, 60, 75, 88, 65, 92]
        lines = []
        for i in range(rows):
            w = widths[i % len(widths)]
            lines.append(
                f'<span class="dj-skeleton__line dj-skeleton__pulse" '
                f'style="width:{w}%">&nbsp;</span>'
            )
        return (
            f'<div class="{cls} dj-skeleton--text" '
            f'role="status" aria-label="Loading">'
            f"{''.join(lines)}</div>"
        )


class AwaitHandler:
    """Block handler for {% await loading_event="data_loaded" %}...{% endawait %}"""

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        loading_event = kw.get("loading_event", "data_loaded")
        loaded = kw.get("loaded", False)
        error = kw.get("error", "")
        error_event = kw.get("error_event", "")
        custom_class = kw.get("class", "")

        e_event = conditional_escape(str(loading_event))
        e_class = conditional_escape(str(custom_class))

        cls = "dj-content-loader"
        if loaded:
            cls += " dj-content-loader--loaded"
        if error:
            cls += " dj-content-loader--error"
        if e_class:
            cls += f" {e_class}"

        if error:
            e_error = conditional_escape(str(error))
            retry_html = ""
            if error_event:
                e_retry = conditional_escape(str(error_event))
                retry_html = (
                    f'<button class="dj-content-loader__retry" dj-click="{e_retry}">Retry</button>'
                )
            return mark_safe(
                f'<div class="{cls}" data-loading-event="{e_event}">'
                f'<div class="dj-content-loader__error" role="alert">'
                f'<span class="dj-content-loader__error-msg">{e_error}</span>'
                f"{retry_html}</div></div>"
            )

        if loaded:
            return mark_safe(
                f'<div class="{cls}" data-loading-event="{e_event}">'
                f'<div class="dj-content-loader__content">{content}</div>'
                f"</div>"
            )

        return mark_safe(
            f'<div class="{cls}" data-loading-event="{e_event}" '
            f'role="status" aria-label="Loading">'
            f'<div class="dj-content-loader__placeholder">{content}</div>'
            f"</div>"
        )


INLINE_HANDLERS.extend(
    [
        ("truncated_list", TruncatedListHandler()),
        ("markdown_textarea", MarkdownTextareaHandler()),
        ("skeleton_for", SkeletonForHandler()),
    ]
)

BLOCK_HANDLERS.extend(
    [
        ("expandable_text", "endexpandable_text", ExpandableTextHandler()),
        ("await", "endawait", AwaitHandler()),
    ]
)


# ---------------------------------------------------------------------------
# v1.5 Remaining Components
# ---------------------------------------------------------------------------


class TimePickerHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        name = kw.get("name", "time")
        value = kw.get("value", "")
        event = kw.get("event", "")
        format_24h = kw.get("format_24h", False)
        step = kw.get("step", 1)
        disabled = kw.get("disabled", False)
        label = kw.get("label", "")
        custom_class = kw.get("class", "")

        e_name = conditional_escape(str(name))
        e_value = conditional_escape(str(value))
        e_event = conditional_escape(str(event)) if event else ""
        e_label = conditional_escape(str(label)) if label else ""
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-time-picker"]
        if disabled:
            classes.append("dj-time-picker--disabled")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        hour, minute = 0, 0
        if value:
            parts = str(value).split(":")
            try:
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                pass

        parts_html = []
        if e_label:
            parts_html.append(
                f'<label class="dj-time-picker__label" for="{e_name}">{e_label}</label>'
            )

        event_attr = f' dj-change="{e_event}"' if e_event else ""
        disabled_attr = " disabled" if disabled else ""

        parts_html.append(f'<input type="hidden" name="{e_name}" value="{e_value}"{event_attr}>')
        parts_html.append('<div class="dj-time-picker__controls">')

        hour_options = []
        if format_24h:
            for h in range(24):
                sel = " selected" if h == hour else ""
                hour_options.append(f'<option value="{h}"{sel}>{h:02d}</option>')
        else:
            display_hour = hour % 12 or 12
            for h in range(1, 13):
                sel = " selected" if h == display_hour else ""
                hour_options.append(f'<option value="{h}"{sel}>{h}</option>')

        parts_html.append(
            f'<select class="dj-time-picker__hour" aria-label="Hour"{disabled_attr}>'
            f"{''.join(hour_options)}</select>"
        )
        parts_html.append('<span class="dj-time-picker__separator">:</span>')

        try:
            step_val = max(1, int(step))
        except (ValueError, TypeError):
            step_val = 1
        minute_options = []
        for m in range(0, 60, step_val):
            sel = " selected" if m == minute else ""
            minute_options.append(f'<option value="{m}"{sel}>{m:02d}</option>')

        parts_html.append(
            f'<select class="dj-time-picker__minute" aria-label="Minute"{disabled_attr}>'
            f"{''.join(minute_options)}</select>"
        )

        if not format_24h:
            is_pm = hour >= 12
            parts_html.append(
                f'<select class="dj-time-picker__period" aria-label="AM/PM"{disabled_attr}>'
                f'<option value="AM"{"" if is_pm else " selected"}>AM</option>'
                f'<option value="PM"{" selected" if is_pm else ""}>PM</option></select>'
            )

        parts_html.append("</div>")
        return mark_safe(f'<div class="{class_str}">{"".join(parts_html)}</div>')


class WizardHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        steps = kw.get("steps", [])
        active = kw.get("active", "")
        event = kw.get("event", "set_step")
        show_numbers = kw.get("show_numbers", True)
        custom_class = kw.get("class", "")

        e_event = conditional_escape(str(event))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-wizard"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(steps, list):
            steps = []

        active_idx = 0
        for i, step in enumerate(steps):
            if isinstance(step, dict) and step.get("id") == active:
                active_idx = i
                break

        indicators = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            step_id = conditional_escape(str(step.get("id", "")))
            step_label = conditional_escape(str(step.get("label", "")))
            step_cls = "dj-wizard__step"
            if i < active_idx:
                step_cls += " dj-wizard__step--completed"
            elif i == active_idx:
                step_cls += " dj-wizard__step--active"
            number_html = f'<span class="dj-wizard__number">{i + 1}</span>' if show_numbers else ""
            indicators.append(
                f'<button class="{step_cls}" dj-click="{e_event}" data-value="{step_id}">'
                f'{number_html}<span class="dj-wizard__label">{step_label}</span></button>'
            )

        nav_items = []
        for i, ind in enumerate(indicators):
            nav_items.append(ind)
            if i < len(indicators) - 1:
                conn_cls = "dj-wizard__connector"
                if i < active_idx:
                    conn_cls += " dj-wizard__connector--completed"
                nav_items.append(f'<div class="{conn_cls}"></div>')

        nav = f'<nav class="dj-wizard__nav" role="tablist">{"".join(nav_items)}</nav>'
        return mark_safe(
            f'<div class="{class_str}">{nav}<div class="dj-wizard__body">{content}</div></div>'
        )


class BottomSheetHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        title = kw.get("title", "")
        is_open = kw.get("open", False)
        close_event = kw.get("close_event", "close_sheet")
        custom_class = kw.get("class", "")

        if not is_open:
            return ""

        e_title = conditional_escape(str(title))
        e_close = conditional_escape(str(close_event))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-bottom-sheet"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        title_html = f'<h3 class="dj-bottom-sheet__title">{e_title}</h3>' if title else ""

        return mark_safe(
            f'<div class="dj-bottom-sheet__backdrop" dj-click="{e_close}">'
            f'<div class="{class_str}" onclick="event.stopPropagation()">'
            f'<div class="dj-bottom-sheet__handle"><div class="dj-bottom-sheet__handle-bar"></div></div>'
            f'<div class="dj-bottom-sheet__header">{title_html}'
            f'<button class="dj-bottom-sheet__close" dj-click="{e_close}">&times;</button></div>'
            f'<div class="dj-bottom-sheet__body">{content}</div></div></div>'
        )


class InfiniteScrollHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        load_event = kw.get("load_event", "load_more")
        threshold = kw.get("threshold", "200px")
        loading = kw.get("loading", False)
        finished = kw.get("finished", False)
        custom_class = kw.get("class", "")

        e_event = conditional_escape(str(load_event))
        e_threshold = conditional_escape(str(threshold))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-infinite-scroll"]
        if loading:
            classes.append("dj-infinite-scroll--loading")
        if finished:
            classes.append("dj-infinite-scroll--finished")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        sentinel = ""
        if loading:
            sentinel = (
                '<div class="dj-infinite-scroll__spinner" role="status" aria-label="Loading"></div>'
            )
        elif finished:
            sentinel = '<div class="dj-infinite-scroll__done">No more items</div>'

        return mark_safe(
            f'<div class="{class_str}" dj-hook="InfiniteScroll" '
            f'data-event="{e_event}" data-threshold="{e_threshold}">'
            f'<div class="dj-infinite-scroll__content">{content}</div>{sentinel}</div>'
        )


class CountdownHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        target = kw.get("target", "")
        event = kw.get("event", "")
        show_days = kw.get("show_days", True)
        show_seconds = kw.get("show_seconds", True)
        custom_class = kw.get("class", "")

        e_target = conditional_escape(str(target))
        e_event = conditional_escape(str(event)) if event else ""
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-countdown"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)
        event_attr = f' data-event="{e_event}"' if e_event else ""

        segments = []
        if show_days:
            segments.append(
                '<div class="dj-countdown__segment">'
                '<span class="dj-countdown__value" data-unit="days">00</span>'
                '<span class="dj-countdown__label">Days</span></div>'
            )
        segments.append(
            '<div class="dj-countdown__segment">'
            '<span class="dj-countdown__value" data-unit="hours">00</span>'
            '<span class="dj-countdown__label">Hours</span></div>'
        )
        segments.append(
            '<div class="dj-countdown__segment">'
            '<span class="dj-countdown__value" data-unit="minutes">00</span>'
            '<span class="dj-countdown__label">Minutes</span></div>'
        )
        if show_seconds:
            segments.append(
                '<div class="dj-countdown__segment">'
                '<span class="dj-countdown__value" data-unit="seconds">00</span>'
                '<span class="dj-countdown__label">Seconds</span></div>'
            )

        separators = []
        for i, seg in enumerate(segments):
            separators.append(seg)
            if i < len(segments) - 1:
                separators.append('<span class="dj-countdown__separator">:</span>')

        return mark_safe(
            f'<div class="{class_str}" dj-hook="Countdown" '
            f'data-target="{e_target}"{event_attr} '
            f'role="timer">{"".join(separators)}</div>'
        )


class CookieConsentHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        message = kw.get("message", "We use cookies to improve your experience.")
        accept_event = kw.get("accept_event", "accept_cookies")
        reject_event = kw.get("reject_event", "")
        accept_label = kw.get("accept_label", "Accept")
        reject_label = kw.get("reject_label", "Decline")
        privacy_url = kw.get("privacy_url", "")
        show_reject = kw.get("show_reject", True)
        position = kw.get("position", "bottom")
        custom_class = kw.get("class", "")

        e_msg = conditional_escape(str(message))
        e_accept = conditional_escape(str(accept_event))
        e_accept_label = conditional_escape(str(accept_label))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-cookie-consent", f"dj-cookie-consent--{conditional_escape(str(position))}"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        msg_text = content.strip() if content and content.strip() else e_msg

        privacy_html = ""
        if privacy_url:
            e_url = conditional_escape(str(privacy_url))
            privacy_html = f' <a href="{e_url}" class="dj-cookie-consent__link">Privacy Policy</a>'

        buttons = [
            f'<button class="dj-cookie-consent__accept" dj-click="{e_accept}">{e_accept_label}</button>'
        ]
        if show_reject and reject_event:
            e_reject = conditional_escape(str(reject_event))
            e_reject_label = conditional_escape(str(reject_label))
            buttons.append(
                f'<button class="dj-cookie-consent__reject" dj-click="{e_reject}">{e_reject_label}</button>'
            )

        return mark_safe(
            f'<div class="{class_str}" role="banner" aria-label="Cookie consent">'
            f'<p class="dj-cookie-consent__message">{msg_text}{privacy_html}</p>'
            f'<div class="dj-cookie-consent__actions">{"".join(buttons)}</div></div>'
        )


class FormArrayHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        name = kw.get("name", "items")
        rows = kw.get("rows", [{"value": ""}])
        min_rows = kw.get("min", 1)
        max_rows = kw.get("max", 10)
        add_event = kw.get("add_event", "add_row")
        remove_event = kw.get("remove_event", "remove_row")
        add_label = kw.get("add_label", "Add row")
        custom_class = kw.get("class", "")

        e_name = conditional_escape(str(name))
        e_add_event = conditional_escape(str(add_event))
        e_remove_event = conditional_escape(str(remove_event))
        e_add_label = conditional_escape(str(add_label))
        e_class = conditional_escape(str(custom_class))

        try:
            min_rows = int(min_rows)
        except (ValueError, TypeError):
            min_rows = 1
        try:
            max_rows = int(max_rows)
        except (ValueError, TypeError):
            max_rows = 10

        if not isinstance(rows, list):
            rows = [{"value": ""}]

        classes = ["dj-form-array"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        row_count = len(rows)
        can_add = row_count < max_rows
        can_remove = row_count > min_rows

        rows_html = []
        for i, row in enumerate(rows):
            val = conditional_escape(str(row.get("value", "") if isinstance(row, dict) else row))
            remove_html = ""
            if can_remove:
                remove_html = (
                    f'<button class="dj-form-array__remove" type="button" '
                    f'dj-click="{e_remove_event}" data-value="{i}" '
                    f'aria-label="Remove row {i + 1}">&times;</button>'
                )
            rows_html.append(
                f'<div class="dj-form-array__row" data-index="{i}">'
                f'<input type="text" name="{e_name}[{i}]" value="{val}" '
                f'class="dj-form-array__input">{remove_html}</div>'
            )

        add_disabled = "" if can_add else " disabled"
        add_html = (
            f'<button class="dj-form-array__add" type="button" '
            f'dj-click="{e_add_event}"{add_disabled}>{e_add_label}</button>'
        )

        return mark_safe(
            f'<div class="{class_str}" data-min="{min_rows}" data-max="{max_rows}">'
            f'<div class="dj-form-array__rows">{"".join(rows_html)}</div>{add_html}</div>'
        )


class ScrollSpyHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        sections = kw.get("sections", [])
        active = kw.get("active", "")
        active_event = kw.get("active_event", "section_changed")
        offset = kw.get("offset", "0px")
        custom_class = kw.get("class", "")

        e_event = conditional_escape(str(active_event))
        e_offset = conditional_escape(str(offset))
        e_class = conditional_escape(str(custom_class))

        if not isinstance(sections, list):
            sections = []

        sections_json = conditional_escape(_json.dumps(sections))

        classes = ["dj-scroll-spy"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        nav_items = []
        for section in sections:
            if isinstance(section, dict):
                s_id = section.get("id", "")
                s_label = section.get("label", s_id)
            else:
                s_id = str(section)
                s_label = s_id
            e_id = conditional_escape(str(s_id))
            e_label = conditional_escape(str(s_label))
            active_cls = " dj-scroll-spy__item--active" if str(s_id) == str(active) else ""
            nav_items.append(
                f'<a href="#{e_id}" class="dj-scroll-spy__item{active_cls}" '
                f'data-section="{e_id}">{e_label}</a>'
            )

        return mark_safe(
            f'<nav class="{class_str}" dj-hook="ScrollSpy" '
            f'data-sections="{sections_json}" data-event="{e_event}" '
            f'data-offset="{e_offset}" role="navigation" aria-label="Section navigation">'
            f"{''.join(nav_items)}</nav>"
        )


class PageAlertHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        alert_type = kw.get("variant", kw.get("type", "info"))
        dismissible = kw.get("dismissible", False)
        dismiss_event = kw.get("dismiss_event", "dismiss_alert")
        icon = kw.get("icon", "")
        custom_class = kw.get("class", "")

        e_type = conditional_escape(str(alert_type))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-page-alert", f"dj-page-alert--{e_type}"]
        if dismissible:
            classes.append("dj-page-alert--dismissible")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        icon_html = (
            f'<span class="dj-page-alert__icon">{conditional_escape(str(icon))}</span>'
            if icon
            else ""
        )

        dismiss_html = ""
        if dismissible:
            e_dismiss = conditional_escape(str(dismiss_event))
            dismiss_html = (
                f'<button class="dj-page-alert__dismiss" dj-click="{e_dismiss}" '
                f'aria-label="Dismiss">&times;</button>'
            )

        return mark_safe(
            f'<div class="{class_str}" role="alert">{icon_html}'
            f'<span class="dj-page-alert__message">{content}</span>{dismiss_html}</div>'
        )


class DropdownMenuHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        label = kw.get("label", "Menu")
        items = kw.get("items", [])
        is_open = kw.get("open", False)
        toggle_event = kw.get("toggle_event", "toggle_menu")
        align = kw.get("align", "left")
        custom_class = kw.get("class", "")

        e_label = conditional_escape(str(label))
        e_toggle = conditional_escape(str(toggle_event))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-dropdown-menu"]
        if is_open:
            classes.append("dj-dropdown-menu--open")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        trigger = (
            f'<button class="dj-dropdown-menu__trigger" dj-click="{e_toggle}" '
            f'aria-expanded="{"true" if is_open else "false"}" aria-haspopup="true">{e_label}</button>'
        )

        if not is_open:
            return mark_safe(f'<div class="{class_str}">{trigger}</div>')

        if not isinstance(items, list):
            items = []

        menu_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("divider"):
                menu_items.append('<hr class="dj-dropdown-menu__divider" role="separator">')
                continue
            item_cls = "dj-dropdown-menu__item"
            if item.get("danger"):
                item_cls += " dj-dropdown-menu__item--danger"
            if item.get("disabled"):
                item_cls += " dj-dropdown-menu__item--disabled"

            e_item_label = conditional_escape(str(item.get("label", "")))
            e_event = conditional_escape(str(item.get("event", "")))
            disabled_attr = " disabled" if item.get("disabled") else ""
            event_attr = f' dj-click="{e_event}"' if e_event else ""
            icon_html = ""
            if item.get("icon"):
                icon_html = f'<span class="dj-dropdown-menu__icon">{conditional_escape(str(item["icon"]))}</span>'
            menu_items.append(
                f'<button class="{item_cls}" role="menuitem"{event_attr}{disabled_attr}>'
                f"{icon_html}{e_item_label}</button>"
            )

        menu_html = (
            f'<div class="dj-dropdown-menu__content dj-dropdown-menu--{conditional_escape(str(align))}" '
            f'role="menu">{"".join(menu_items)}</div>'
        )

        return mark_safe(f'<div class="{class_str}">{trigger}{menu_html}</div>')


class MeterHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        segments = kw.get("segments", [])
        total = kw.get("total", 100)
        label = kw.get("label", "")
        show_legend = kw.get("show_legend", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))

        classes = ["dj-meter"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        try:
            total = int(total)
        except (ValueError, TypeError):
            total = 100

        if not isinstance(segments, list):
            segments = []

        label_html = (
            f'<div class="dj-meter__label">{conditional_escape(str(label))}</div>' if label else ""
        )

        bar_parts = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            val = seg.get("value", 0)
            try:
                val = float(val)
            except (ValueError, TypeError):
                val = 0
            pct = min(100, max(0, (val / total) * 100)) if total > 0 else 0
            color = conditional_escape(str(seg.get("color", "")))
            seg_label = conditional_escape(str(seg.get("label", "")))
            style = f"width:{pct:.1f}%"
            if color:
                style += f";background:{color}"
            bar_parts.append(
                f'<div class="dj-meter__segment" style="{style}" role="meter" '
                f'aria-valuenow="{int(val)}" aria-valuemin="0" aria-valuemax="{total}" '
                f'aria-label="{seg_label}"></div>'
            )

        bar = f'<div class="dj-meter__bar">{"".join(bar_parts)}</div>'

        legend_html = ""
        if show_legend and segments:
            items = []
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                color = conditional_escape(str(seg.get("color", "")))
                seg_label = conditional_escape(str(seg.get("label", "")))
                val = seg.get("value", 0)
                swatch_style = f"background:{color}" if color else ""
                items.append(
                    f'<div class="dj-meter__legend-item">'
                    f'<span class="dj-meter__legend-swatch" style="{swatch_style}"></span>'
                    f'<span class="dj-meter__legend-label">{seg_label}</span>'
                    f'<span class="dj-meter__legend-value">{val}</span></div>'
                )
            legend_html = f'<div class="dj-meter__legend">{"".join(items)}</div>'

        return mark_safe(f'<div class="{class_str}">{label_html}{bar}{legend_html}</div>')


class ExportDialogHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        formats = kw.get("formats", [])
        columns = kw.get("columns", [])
        event = kw.get("event", "export")
        is_open = kw.get("open", False)
        close_event = kw.get("close_event", "close_export")
        selected_format = kw.get("selected_format", "")
        title = kw.get("title", "Export Data")
        custom_class = kw.get("class", "")

        if not is_open:
            return ""

        e_title = conditional_escape(str(title))
        e_event = conditional_escape(str(event))
        e_close = conditional_escape(str(close_event))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-export-dialog"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(formats, list):
            formats = []
        if not isinstance(columns, list):
            columns = []
        if not selected_format and formats:
            selected_format = formats[0]

        format_options = []
        for fmt in formats:
            e_fmt = conditional_escape(str(fmt))
            checked = " checked" if str(fmt) == str(selected_format) else ""
            format_options.append(
                f'<label class="dj-export-dialog__format">'
                f'<input type="radio" name="export_format" value="{e_fmt}"{checked}>'
                f'<span class="dj-export-dialog__format-label">{e_fmt.upper()}</span></label>'
            )

        col_options = []
        for col in columns:
            if not isinstance(col, dict):
                continue
            e_id = conditional_escape(str(col.get("id", "")))
            e_label = conditional_escape(str(col.get("label", "")))
            checked = " checked" if col.get("checked", True) else ""
            col_options.append(
                f'<label class="dj-export-dialog__column">'
                f'<input type="checkbox" name="export_col" value="{e_id}"{checked}>'
                f"<span>{e_label}</span></label>"
            )

        return mark_safe(
            f'<div class="dj-export-dialog__backdrop" dj-click="{e_close}">'
            f'<div class="{class_str}" onclick="event.stopPropagation()">'
            f'<div class="dj-export-dialog__header"><h3>{e_title}</h3>'
            f'<button class="dj-export-dialog__close" dj-click="{e_close}">&times;</button></div>'
            f'<div class="dj-export-dialog__body">'
            f'<div class="dj-export-dialog__formats">'
            f'<h4 class="dj-export-dialog__section-title">Format</h4>{"".join(format_options)}</div>'
            f'<div class="dj-export-dialog__columns">'
            f'<h4 class="dj-export-dialog__section-title">Columns</h4>{"".join(col_options)}</div></div>'
            f'<div class="dj-export-dialog__footer">'
            f'<button class="dj-export-dialog__cancel" dj-click="{e_close}">Cancel</button>'
            f'<button class="dj-export-dialog__submit" dj-click="{e_event}">Export</button>'
            f"</div></div></div>"
        )


class ImportWizardHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        accepted_formats = kw.get("accepted_formats", ".csv")
        model_fields = kw.get("model_fields", [])
        event = kw.get("event", "import_data")
        step = kw.get("step", "upload")
        upload_event = kw.get("upload_event", "upload_file")
        custom_class = kw.get("class", "")

        e_event = conditional_escape(str(event))
        e_formats = conditional_escape(str(accepted_formats))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-import-wizard"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(model_fields, list):
            model_fields = []

        steps = ["upload", "map", "preview"]
        step_labels = {"upload": "Upload", "map": "Map Fields", "preview": "Preview"}
        active_idx = steps.index(step) if step in steps else 0

        step_items = []
        for i, s in enumerate(steps):
            step_cls = "dj-import-wizard__step"
            if i < active_idx:
                step_cls += " dj-import-wizard__step--completed"
            elif i == active_idx:
                step_cls += " dj-import-wizard__step--active"
            step_items.append(
                f'<div class="{step_cls}">'
                f'<span class="dj-import-wizard__step-number">{i + 1}</span>'
                f'<span class="dj-import-wizard__step-label">{step_labels[s]}</span></div>'
            )
        nav = f'<div class="dj-import-wizard__nav">{"".join(step_items)}</div>'

        if step == "upload":
            e_upload = conditional_escape(str(upload_event))
            body = (
                f'<div class="dj-import-wizard__upload">'
                f'<div class="dj-import-wizard__dropzone">'
                f"<p>Drag &amp; drop or click to upload</p>"
                f'<input type="file" accept="{e_formats}" '
                f'class="dj-import-wizard__file-input" dj-change="{e_upload}">'
                f'<p class="dj-import-wizard__formats">Accepted: {e_formats}</p></div></div>'
            )
        elif step == "map":
            field_rows = []
            for field in model_fields:
                if not isinstance(field, dict):
                    continue
                e_id = conditional_escape(str(field.get("id", "")))
                e_label = conditional_escape(str(field.get("label", "")))
                field_rows.append(
                    f'<div class="dj-import-wizard__field-row">'
                    f'<span class="dj-import-wizard__field-label">{e_label}</span>'
                    f'<select class="dj-import-wizard__field-select" name="map_{e_id}">'
                    f'<option value="">-- Skip --</option></select></div>'
                )
            body = f'<div class="dj-import-wizard__mapping">{"".join(field_rows)}</div>'
        else:
            body = (
                f'<div class="dj-import-wizard__preview">'
                f"<p>Preview your data before importing.</p>"
                f'<button class="dj-import-wizard__import-btn" dj-click="{e_event}">Import</button></div>'
            )

        return mark_safe(f'<div class="{class_str}">{nav}{body}</div>')


class AuditLogHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        entries = kw.get("entries", [])
        stream_event = kw.get("stream_event", "")
        columns = kw.get("columns", ["timestamp", "user", "action", "resource", "detail"])
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-audit-log"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(entries, list):
            entries = []
        if not isinstance(columns, list):
            columns = ["timestamp", "user", "action", "resource", "detail"]

        stream_attr = ""
        if stream_event:
            stream_attr = f' data-stream-event="{conditional_escape(str(stream_event))}"'

        col_labels = {
            "timestamp": "Timestamp",
            "user": "User",
            "action": "Action",
            "resource": "Resource",
            "detail": "Detail",
        }

        headers = [
            f'<th class="dj-audit-log__th">{conditional_escape(col_labels.get(c, c.title()))}</th>'
            for c in columns
        ]
        thead = f"<thead><tr>{''.join(headers)}</tr></thead>"

        rows = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            cells = []
            for col in columns:
                val = conditional_escape(str(entry.get(col, "")))
                cell_cls = f"dj-audit-log__td dj-audit-log__td--{col}"
                if col == "action":
                    cell_cls += (
                        f" dj-audit-log__action--{conditional_escape(str(entry.get('action', '')))}"
                    )
                cells.append(f'<td class="{cell_cls}">{val}</td>')
            rows.append(f'<tr class="dj-audit-log__row">{"".join(cells)}</tr>')

        tbody = (
            f"<tbody>{''.join(rows)}</tbody>"
            if rows
            else (
                f'<tbody><tr><td colspan="{len(columns)}" class="dj-audit-log__empty">No entries</td></tr></tbody>'
            )
        )

        return mark_safe(
            f'<div class="{class_str}"{stream_attr}>'
            f'<table class="dj-audit-log__table">{thead}{tbody}</table></div>'
        )


class ErrorBoundaryHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        fallback = kw.get("fallback", "Something went wrong")
        retry_event = kw.get("retry_event", "")
        custom_class = kw.get("class", "")

        e_fallback = conditional_escape(str(fallback))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-error-boundary"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        # In Rust engine, content is pre-rendered; if empty, show fallback
        if not content or not content.strip():
            classes.append("dj-error-boundary--error")
            class_str = " ".join(classes)
            retry_html = ""
            if retry_event:
                e_retry = conditional_escape(str(retry_event))
                retry_html = (
                    f'<button class="dj-error-boundary__retry" dj-click="{e_retry}">Retry</button>'
                )
            return mark_safe(
                f'<div class="{class_str}" role="alert">'
                f'<div class="dj-error-boundary__fallback">'
                f'<p class="dj-error-boundary__message">{e_fallback}</p>'
                f"{retry_html}</div></div>"
            )

        return mark_safe(f'<div class="{class_str}">{content}</div>')


class SortableListHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        items = kw.get("items", [])
        move_event = kw.get("move_event", "reorder")
        handle = kw.get("handle", True)
        disabled = kw.get("disabled", False)
        custom_class = kw.get("class", "")

        e_event = conditional_escape(str(move_event))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-sortable-list"]
        if disabled:
            classes.append("dj-sortable-list--disabled")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(items, list):
            items = []

        items_html = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = conditional_escape(str(item.get("id", "")))
            label = conditional_escape(str(item.get("label", "")))
            handle_html = (
                '<span class="dj-sortable-list__handle" aria-hidden="true">&#x2630;</span> '
                if handle
                else ""
            )
            drag_attr = ' draggable="true"' if not disabled else ""
            items_html.append(
                f'<li class="dj-sortable-list__item" data-id="{item_id}"{drag_attr} '
                f'role="listitem">'
                f"{handle_html}"
                f'<span class="dj-sortable-list__label">{label}</span></li>'
            )

        disabled_attr = ' data-disabled="true"' if disabled else ""

        return mark_safe(
            f'<ul class="{class_str}" dj-hook="SortableList" '
            f'data-move-event="{e_event}" '
            f'role="list"{disabled_attr}>{"".join(items_html)}</ul>'
        )


class SortableGridHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        items = kw.get("items", [])
        columns = kw.get("columns", 3)
        move_event = kw.get("move_event", "reorder")
        gap = kw.get("gap", "0.75rem")
        disabled = kw.get("disabled", False)
        custom_class = kw.get("class", "")

        e_event = conditional_escape(str(move_event))
        e_class = conditional_escape(str(custom_class))
        e_gap = conditional_escape(str(gap))

        classes = ["dj-sortable-grid"]
        if disabled:
            classes.append("dj-sortable-grid--disabled")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(items, list):
            items = []

        try:
            cols = int(columns)
        except (ValueError, TypeError):
            cols = 3

        items_html = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = conditional_escape(str(item.get("id", "")))
            label = conditional_escape(str(item.get("label", "")))
            thumbnail = item.get("thumbnail", "")
            thumb_html = ""
            if thumbnail:
                e_thumb = conditional_escape(str(thumbnail))
                thumb_html = (
                    f'<img class="dj-sortable-grid__thumb" '
                    f'src="{e_thumb}" alt="{label}" loading="lazy">'
                )
            drag_attr = ' draggable="true"' if not disabled else ""
            items_html.append(
                f'<div class="dj-sortable-grid__item" data-id="{item_id}"{drag_attr}>'
                f"{thumb_html}"
                f'<span class="dj-sortable-grid__label">{label}</span></div>'
            )

        disabled_attr = ' data-disabled="true"' if disabled else ""
        style = f'style="grid-template-columns:repeat({cols},1fr);gap:{e_gap}"'

        return mark_safe(
            f'<div class="{class_str}" dj-hook="SortableGrid" '
            f'data-move-event="{e_event}" data-columns="{cols}" '
            f'{style} role="grid"{disabled_attr}>{"".join(items_html)}</div>'
        )


class ImageCropperHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        src = kw.get("src", "")
        crop_event = kw.get("crop_event", "save_crop")
        aspect_ratio = kw.get("aspect_ratio", "")
        min_width = kw.get("min_width", 50)
        min_height = kw.get("min_height", 50)
        disabled = kw.get("disabled", False)
        custom_class = kw.get("class", "")

        e_src = conditional_escape(str(src))
        e_event = conditional_escape(str(crop_event))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-image-cropper"]
        if disabled:
            classes.append("dj-image-cropper--disabled")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        ratio_attr = ""
        if aspect_ratio:
            e_ratio = conditional_escape(str(aspect_ratio))
            ratio_attr = f' data-aspect-ratio="{e_ratio}"'

        try:
            min_w = int(min_width)
        except (ValueError, TypeError):
            min_w = 50
        try:
            min_h = int(min_height)
        except (ValueError, TypeError):
            min_h = 50

        return mark_safe(
            f'<div class="{class_str}" dj-hook="ImageCropper" '
            f'data-crop-event="{e_event}" '
            f'data-min-width="{min_w}" '
            f'data-min-height="{min_h}"{ratio_attr}>'
            f'<div class="dj-image-cropper__canvas">'
            f'<img class="dj-image-cropper__image" src="{e_src}" alt="Image to crop" draggable="false">'
            f'<div class="dj-image-cropper__overlay"></div>'
            f'<div class="dj-image-cropper__selection"></div>'
            f"</div>"
            f'<div class="dj-image-cropper__actions">'
            f'<button class="dj-image-cropper__crop-btn" type="button">Crop</button>'
            f'<button class="dj-image-cropper__reset-btn" type="button">Reset</button>'
            f"</div>"
            f"</div>"
        )


class SignaturePadHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        name = kw.get("name", "signature")
        save_event = kw.get("save_event", "save_signature")
        width = kw.get("width", 400)
        height = kw.get("height", 200)
        pen_color = kw.get("pen_color", "#000000")
        pen_width = kw.get("pen_width", 2)
        disabled = kw.get("disabled", False)
        custom_class = kw.get("class", "")

        e_name = conditional_escape(str(name))
        e_event = conditional_escape(str(save_event))
        e_color = conditional_escape(str(pen_color))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-signature-pad"]
        if disabled:
            classes.append("dj-signature-pad--disabled")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        try:
            w = int(width)
        except (ValueError, TypeError):
            w = 400
        try:
            h = int(height)
        except (ValueError, TypeError):
            h = 200
        try:
            pw = int(pen_width)
        except (ValueError, TypeError):
            pw = 2

        disabled_attr = " disabled" if disabled else ""

        return mark_safe(
            f'<div class="{class_str}" dj-hook="SignaturePad" '
            f'data-save-event="{e_event}" '
            f'data-pen-color="{e_color}" '
            f'data-pen-width="{pw}">'
            f'<canvas class="dj-signature-pad__canvas" '
            f'width="{w}" height="{h}"'
            f"{disabled_attr}></canvas>"
            f'<input type="hidden" name="{e_name}" class="dj-signature-pad__value">'
            f'<div class="dj-signature-pad__actions">'
            f'<button class="dj-signature-pad__clear-btn" type="button">Clear</button>'
            f'<button class="dj-signature-pad__save-btn" type="button"'
            f"{disabled_attr}>Save</button>"
            f"</div>"
            f"</div>"
        )


class ResizablePanelHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        direction = kw.get("direction", "horizontal")
        min_size = kw.get("min_size", "100px")
        max_size = kw.get("max_size", "none")
        initial_size = kw.get("initial_size", "50%")
        disabled = kw.get("disabled", False)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))

        if direction not in ("horizontal", "vertical"):
            direction = "horizontal"

        classes = ["dj-resizable-panel", f"dj-resizable-panel--{direction}"]
        if disabled:
            classes.append("dj-resizable-panel--disabled")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        e_min = conditional_escape(str(min_size))
        e_max = conditional_escape(str(max_size))
        e_initial = conditional_escape(str(initial_size))

        size_prop = "width" if direction == "horizontal" else "height"
        style_parts = [f"{size_prop}:{e_initial}", f"min-{size_prop}:{e_min}"]
        if max_size != "none":
            style_parts.append(f"max-{size_prop}:{e_max}")
        style = f'style="{";".join(style_parts)}"'

        disabled_attr = ' data-disabled="true"' if disabled else ""

        return mark_safe(
            f'<div class="{class_str}" dj-hook="ResizablePanel" '
            f'data-direction="{direction}" '
            f'data-min-size="{e_min}" data-max-size="{e_max}" '
            f"{style}{disabled_attr}>"
            f'<div class="dj-resizable-panel__content">{content}</div>'
            f'<div class="dj-resizable-panel__handle" role="separator" '
            f'aria-orientation="{direction}" tabindex="0">'
            f'<span class="dj-resizable-panel__handle-bar"></span>'
            f"</div>"
            f"</div>"
        )


class LightboxHandler:
    def render(self, args, context):
        kw = _parse_args(args, context)
        images = kw.get("images", [])
        active = kw.get("active", 0)
        is_open = kw.get("open", False)
        close_event = kw.get("close_event", "close_lightbox")
        navigate_event = kw.get("navigate_event", "lightbox_navigate")
        show_counter = kw.get("show_counter", True)
        custom_class = kw.get("class", "")

        if not is_open:
            return ""

        e_close = conditional_escape(str(close_event))
        e_nav = conditional_escape(str(navigate_event))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-lightbox"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(images, list):
            images = []

        total = len(images)
        try:
            idx = int(active)
        except (ValueError, TypeError):
            idx = 0
        idx = max(0, min(idx, total - 1)) if total else 0

        img_html = ""
        caption_html = ""
        if images and 0 <= idx < total:
            img = images[idx]
            if isinstance(img, dict):
                e_src = conditional_escape(str(img.get("src", "")))
                e_alt = conditional_escape(str(img.get("alt", "")))
                caption = img.get("caption", "")
                img_html = f'<img class="dj-lightbox__image" src="{e_src}" alt="{e_alt}">'
                if caption:
                    caption_html = (
                        f'<p class="dj-lightbox__caption">{conditional_escape(str(caption))}</p>'
                    )

        prev_btn = (
            (
                f'<button class="dj-lightbox__prev" dj-click="{e_nav}" '
                f'data-value="{idx - 1}" aria-label="Previous">&#8249;</button>'
            )
            if total > 1
            else ""
        )

        next_btn = (
            (
                f'<button class="dj-lightbox__next" dj-click="{e_nav}" '
                f'data-value="{idx + 1}" aria-label="Next">&#8250;</button>'
            )
            if total > 1
            else ""
        )

        counter = ""
        if show_counter and total > 1:
            counter = f'<span class="dj-lightbox__counter">{idx + 1} of {total}</span>'

        return mark_safe(
            f'<div class="{class_str}" dj-hook="ImageLightbox" '
            f'data-close-event="{e_close}" data-navigate-event="{e_nav}" '
            f'role="dialog" aria-modal="true">'
            f'<div class="dj-lightbox__backdrop" dj-click="{e_close}"></div>'
            f'<button class="dj-lightbox__close" dj-click="{e_close}" '
            f'aria-label="Close">&times;</button>'
            f"{prev_btn}"
            f'<div class="dj-lightbox__stage">{img_html}{caption_html}</div>'
            f"{next_btn}"
            f"{counter}"
            f"</div>"
        )


class DashboardGridHandler:
    def render(self, args, content, context):
        kw = _parse_args(args, context)
        panels = kw.get("panels", [])
        columns = kw.get("columns", 4)
        row_height = kw.get("row_height", "200px")
        gap = kw.get("gap", "1rem")
        move_event = kw.get("move_event", "dashboard_move")
        resize_event = kw.get("resize_event", "dashboard_resize")
        custom_class = kw.get("class", "")

        e_move = conditional_escape(str(move_event))
        e_resize = conditional_escape(str(resize_event))
        e_gap = conditional_escape(str(gap))
        e_row_height = conditional_escape(str(row_height))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-dashboard-grid"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        try:
            cols = int(columns)
        except (ValueError, TypeError):
            cols = 4

        if not isinstance(panels, list):
            panels = []

        panels_html = []
        for panel in panels:
            if not isinstance(panel, dict):
                continue
            pid = conditional_escape(str(panel.get("id", "")))
            title = conditional_escape(str(panel.get("title", "")))
            pcontent = panel.get("content", "")
            try:
                col = int(panel.get("col", 1))
            except (ValueError, TypeError):
                col = 1
            try:
                row = int(panel.get("row", 1))
            except (ValueError, TypeError):
                row = 1
            try:
                w = int(panel.get("width", 1))
            except (ValueError, TypeError):
                w = 1
            try:
                h = int(panel.get("height", 1))
            except (ValueError, TypeError):
                h = 1

            style = f"grid-column:{col}/span {w};grid-row:{row}/span {h}"

            panels_html.append(
                f'<div class="dj-dashboard-grid__panel" data-panel-id="{pid}" '
                f'style="{style}" draggable="true">'
                f'<div class="dj-dashboard-grid__panel-header">'
                f'<span class="dj-dashboard-grid__panel-title">{title}</span>'
                f'<span class="dj-dashboard-grid__panel-drag" aria-hidden="true">&#x2630;</span>'
                f"</div>"
                f'<div class="dj-dashboard-grid__panel-body">{pcontent}</div>'
                f'<div class="dj-dashboard-grid__panel-resize" role="separator"></div>'
                f"</div>"
            )

        grid_style = (
            f'style="display:grid;grid-template-columns:repeat({cols},1fr);'
            f'grid-auto-rows:minmax({e_row_height},auto);gap:{e_gap}"'
        )

        inner = "".join(panels_html) + (content or "")

        return mark_safe(
            f'<div class="{class_str}" dj-hook="DashboardGrid" '
            f'data-move-event="{e_move}" data-resize-event="{e_resize}" '
            f'data-columns="{cols}" {grid_style}>{inner}</div>'
        )


INLINE_HANDLERS.extend(
    [
        ("time_picker", TimePickerHandler()),
        ("countdown", CountdownHandler()),
        ("scroll_spy", ScrollSpyHandler()),
        ("meter", MeterHandler()),
        ("export_dialog", ExportDialogHandler()),
        ("import_wizard", ImportWizardHandler()),
        ("audit_log", AuditLogHandler()),
        ("sortable_list", SortableListHandler()),
        ("sortable_grid", SortableGridHandler()),
        ("image_cropper", ImageCropperHandler()),
        ("signature_pad", SignaturePadHandler()),
        ("lightbox", LightboxHandler()),
    ]
)

BLOCK_HANDLERS.extend(
    [
        ("wizard", "endwizard", WizardHandler()),
        ("bottom_sheet", "endbottom_sheet", BottomSheetHandler()),
        ("infinite_scroll", "endinfinite_scroll", InfiniteScrollHandler()),
        ("cookie_consent", "endcookie_consent", CookieConsentHandler()),
        ("form_array", "endform_array", FormArrayHandler()),
        ("page_alert", "endpage_alert", PageAlertHandler()),
        ("dropdown_menu", "enddropdown_menu", DropdownMenuHandler()),
        ("error_boundary", "enderror_boundary", ErrorBoundaryHandler()),
        ("resizable_panel", "endresizable_panel", ResizablePanelHandler()),
        ("dashboard_grid", "enddashboard_grid", DashboardGridHandler()),
    ]
)


# ===========================================================================
# DATA VISUALIZATION HANDLERS (v2.0 Batch 2)
# ===========================================================================

import math as _math
from datetime import date as _date, timedelta as _timedelta


class BarChartHandler:
    """Inline handler for {% bar_chart data=data labels=labels %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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
        e_color = conditional_escape(str(color)) if color else ""
        color_attr = f' fill="{e_color}"' if e_color else ""

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
                    f'y="{y - 4:.1f}" text-anchor="middle" font-size="10">{val:g}</text>'
                )
            if i < len(labels):
                parts.append(
                    f'<text class="dj-bar-chart__label" x="{x + bar_w / 2:.1f}" '
                    f'y="{pad_top + chart_h + 16:.1f}" text-anchor="middle" font-size="10">'
                    f"{lbl}</text>"
                )

        parts.append("</svg>")
        return mark_safe(f'<div class="{class_str}">{"".join(parts)}</div>')


class LineChartHandler:
    """Inline handler for {% line_chart series=series labels=labels %}"""

    COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6", "#06b6d4"]

    def render(self, args, context):
        kw = _parse_args(args, context)
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
                        f'r="3" fill="{color}"><title>{name}: {v:g}</title></circle>'
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


class PieChartHandler:
    """Inline handler for {% pie_chart segments=segments %}"""

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

    def render(self, args, context):
        kw = _parse_args(args, context)
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
                    f'font-size="10" fill="#fff" font-weight="600">{pct * 100:.0f}%</text>'
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


class SparklineHandler:
    """Inline handler for {% sparkline data=values %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class HeatmapHandler:
    """Inline handler for {% heatmap data=matrix x_labels=x y_labels=y %}"""

    _interpolate_color = staticmethod(interpolate_color)

    def render(self, args, context):
        kw = _parse_args(args, context)
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
                f'text-anchor="middle" font-size="10">{conditional_escape(str(lbl))}</text>'
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


class TreemapHandler:
    """Inline handler for {% treemap data=data value_key="size" label_key="name" %}"""

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
                rw = w * (val / total)
                rects.append((cx, y, rw, h, label, val, idx))
                cx += rw
        else:
            cy = y
            for label, val, idx in items:
                rh = h * (val / total)
                rects.append((x, cy, w, rh, label, val, idx))
                cy += rh
        return rects

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class CalendarHeatmapHandler:
    """Inline handler for {% calendar_heatmap data=activity_data year=2026 %}"""

    LEVELS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]

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

    def render(self, args, context):
        kw = _parse_args(args, context)
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


INLINE_HANDLERS.extend(
    [
        ("bar_chart", BarChartHandler()),
        ("line_chart", LineChartHandler()),
        ("pie_chart", PieChartHandler()),
        ("sparkline", SparklineHandler()),
        ("heatmap", HeatmapHandler()),
        ("treemap", TreemapHandler()),
        ("calendar_heatmap", CalendarHeatmapHandler()),
    ]
)


# ===========================================================================
# BATCH 3 — Developer Tools + Specialized Components
# ===========================================================================

import re as _re_mod


class TerminalHandler:
    """Inline handler for {% terminal output=lines %}"""

    ANSI_RE = _re_mod.compile(r"\033\[([0-9;]*)m")
    ANSI_COLORS = {
        "30": "#000",
        "31": "#e74c3c",
        "32": "#2ecc71",
        "33": "#f1c40f",
        "34": "#3498db",
        "35": "#9b59b6",
        "36": "#1abc9c",
        "37": "#ecf0f1",
        "90": "#7f8c8d",
        "91": "#ff6b6b",
        "92": "#55efc4",
        "93": "#ffeaa7",
        "94": "#74b9ff",
        "95": "#a29bfe",
        "96": "#81ecec",
        "97": "#fff",
    }

    @classmethod
    def _ansi_to_html(cls, text):
        result = []
        open_spans = 0
        last_end = 0
        for m in cls.ANSI_RE.finditer(text):
            start, end = m.span()
            result.append(conditional_escape(text[last_end:start]))
            last_end = end
            codes = m.group(1).split(";")
            for code in codes:
                if code == "0" or code == "":
                    result.append("</span>" * open_spans)
                    open_spans = 0
                elif code == "1":
                    result.append('<span style="font-weight:bold">')
                    open_spans += 1
                elif code in cls.ANSI_COLORS:
                    color = cls.ANSI_COLORS[code]
                    result.append(f'<span style="color:{color}">')
                    open_spans += 1
        result.append(conditional_escape(text[last_end:]))
        result.append("</span>" * open_spans)
        return "".join(result)

    def render(self, args, context):
        kw = _parse_args(args, context)
        output = kw.get("output", [])
        title = kw.get("title", "")
        stream_event = kw.get("stream_event", "")
        show_line_numbers = kw.get("show_line_numbers", False)
        wrap = kw.get("wrap", False)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-terminal"]
        if wrap:
            classes.append("dj-terminal--wrap")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(output, list):
            output = []

        title_html = ""
        if title:
            e_title = conditional_escape(str(title))
            title_html = (
                f'<div class="dj-terminal__titlebar">'
                f'<span class="dj-terminal__title">{e_title}</span>'
                f'<span class="dj-terminal__dots">'
                f'<span class="dj-terminal__dot dj-terminal__dot--red"></span>'
                f'<span class="dj-terminal__dot dj-terminal__dot--yellow"></span>'
                f'<span class="dj-terminal__dot dj-terminal__dot--green"></span>'
                f"</span></div>"
            )

        lines_html = []
        for i, line in enumerate(output):
            line_text = self._ansi_to_html(str(line))
            num_html = ""
            if show_line_numbers:
                num_html = f'<span class="dj-terminal__line-num">{i + 1}</span>'
            lines_html.append(
                f'<div class="dj-terminal__line">{num_html}'
                f'<span class="dj-terminal__text">{line_text}</span></div>'
            )

        stream_attr = ""
        if stream_event:
            e_stream = conditional_escape(str(stream_event))
            stream_attr = f' data-stream-event="{e_stream}"'

        return mark_safe(
            f'<div class="{class_str}" dj-hook="Terminal"{stream_attr}>'
            f"{title_html}"
            f'<div class="dj-terminal__body">{"".join(lines_html)}</div>'
            f"</div>"
        )


class MarkdownEditorHandler:
    """Inline handler for {% markdown_editor name="content" %}"""

    TOOLBAR_BUTTONS = [
        ("bold", "B", "**", "**"),
        ("italic", "I", "_", "_"),
        ("code", "&lt;/&gt;", "`", "`"),
        ("link", "Link", "[", "](url)"),
        ("heading", "H", "## ", ""),
    ]

    def render(self, args, context):
        kw = _parse_args(args, context)
        name = kw.get("name", "content")
        value = kw.get("value", "")
        preview = kw.get("preview", True)
        toolbar = kw.get("toolbar", True)
        placeholder = kw.get("placeholder", "Write markdown...")
        rows = kw.get("rows", 12)
        disabled = kw.get("disabled", False)
        event = kw.get("event", "")
        custom_class = kw.get("class", "")

        e_name = conditional_escape(str(name))
        e_value = conditional_escape(str(value))
        e_placeholder = conditional_escape(str(placeholder))
        e_class = conditional_escape(str(custom_class))

        try:
            rows = int(rows)
        except (ValueError, TypeError):
            rows = 12

        classes = ["dj-md-editor"]
        if preview:
            classes.append("dj-md-editor--split")
        if disabled:
            classes.append("dj-md-editor--disabled")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        disabled_attr = " disabled" if disabled else ""
        event_attr = ""
        if event:
            e_event = conditional_escape(str(event))
            event_attr = f' dj-input="{e_event}"'

        toolbar_html = ""
        if toolbar:
            btns = []
            for btn_id, label, prefix, suffix in self.TOOLBAR_BUTTONS:
                btns.append(
                    f'<button type="button" class="dj-md-editor__btn" '
                    f'data-action="{btn_id}" data-prefix="{conditional_escape(prefix)}" '
                    f'data-suffix="{conditional_escape(suffix)}" '
                    f'aria-label="{btn_id.title()}">{label}</button>'
                )
            toolbar_html = f'<div class="dj-md-editor__toolbar">{"".join(btns)}</div>'

        textarea_html = (
            f'<textarea class="dj-md-editor__textarea" name="{e_name}" '
            f'placeholder="{e_placeholder}" rows="{rows}"'
            f"{disabled_attr}{event_attr}>{e_value}</textarea>"
        )

        preview_html = ""
        if preview:
            preview_html = '<div class="dj-md-editor__preview" aria-label="Preview"></div>'

        panes = f'<div class="dj-md-editor__panes">{textarea_html}{preview_html}</div>'

        return mark_safe(
            f'<div class="{class_str}" dj-hook="MarkdownEditor">{toolbar_html}{panes}</div>'
        )


class JsonViewerHandler:
    """Inline handler for {% json_viewer data=json_data %}"""

    def _render_node(self, value, depth, collapsed_depth):
        collapsed = depth >= collapsed_depth

        if isinstance(value, dict):
            if not value:
                return '<span class="dj-json__bracket">{}</span>'
            collapse_cls = " dj-json__node--collapsed" if collapsed else ""
            toggle = (
                f'<span class="dj-json__toggle" role="button" tabindex="0" '
                f'aria-expanded="{"false" if collapsed else "true"}">'
                f"{'&#9654;' if collapsed else '&#9660;'}</span>"
            )
            items = []
            for k, v in value.items():
                e_key = conditional_escape(str(k))
                items.append(
                    f'<div class="dj-json__pair">'
                    f'<span class="dj-json__key">"{e_key}"</span>'
                    f'<span class="dj-json__colon">: </span>'
                    f"{self._render_node(v, depth + 1, collapsed_depth)}</div>"
                )
            count = f' <span class="dj-json__count">({len(value)} keys)</span>' if collapsed else ""
            return (
                f'<div class="dj-json__node dj-json__node--object{collapse_cls}">'
                f"{toggle}"
                f'<span class="dj-json__bracket">{{</span>{count}'
                f'<div class="dj-json__children">{"".join(items)}</div>'
                f'<span class="dj-json__bracket">}}</span></div>'
            )

        if isinstance(value, list):
            if not value:
                return '<span class="dj-json__bracket">[]</span>'
            collapse_cls = " dj-json__node--collapsed" if collapsed else ""
            toggle = (
                f'<span class="dj-json__toggle" role="button" tabindex="0" '
                f'aria-expanded="{"false" if collapsed else "true"}">'
                f"{'&#9654;' if collapsed else '&#9660;'}</span>"
            )
            items = []
            for i, v in enumerate(value):
                items.append(
                    f'<div class="dj-json__item">'
                    f"{self._render_node(v, depth + 1, collapsed_depth)}"
                    f"{',' if i < len(value) - 1 else ''}</div>"
                )
            count = (
                f' <span class="dj-json__count">({len(value)} items)</span>' if collapsed else ""
            )
            return (
                f'<div class="dj-json__node dj-json__node--array{collapse_cls}">'
                f"{toggle}"
                f'<span class="dj-json__bracket">[</span>{count}'
                f'<div class="dj-json__children">{"".join(items)}</div>'
                f'<span class="dj-json__bracket">]</span></div>'
            )

        if isinstance(value, str):
            return f'<span class="dj-json__value dj-json__value--string">"{conditional_escape(value)}"</span>'
        if isinstance(value, bool):
            return f'<span class="dj-json__value dj-json__value--bool">{"true" if value else "false"}</span>'
        if isinstance(value, (int, float)):
            return f'<span class="dj-json__value dj-json__value--number">{value}</span>'
        if value is None:
            return '<span class="dj-json__value dj-json__value--null">null</span>'

        return f'<span class="dj-json__value">{conditional_escape(str(value))}</span>'

    def render(self, args, context):
        kw = _parse_args(args, context)
        data = kw.get("data", None)
        collapsed_depth = kw.get("collapsed_depth", 2)
        root_label = kw.get("root_label", "root")
        copy_button = kw.get("copy_button", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        e_label = conditional_escape(str(root_label))

        try:
            collapsed_depth = int(collapsed_depth)
        except (ValueError, TypeError):
            collapsed_depth = 2

        classes = ["dj-json-viewer"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        copy_html = ""
        if copy_button:
            copy_html = (
                '<button class="dj-json-viewer__copy" type="button" '
                'aria-label="Copy JSON">Copy</button>'
            )

        try:
            raw_json = _json.dumps(data, indent=2, default=str)
        except (TypeError, ValueError):
            raw_json = str(data)

        tree_html = self._render_node(data, 0, collapsed_depth)

        return mark_safe(
            f'<div class="{class_str}" dj-hook="JsonViewer" '
            f'data-collapsed-depth="{collapsed_depth}">'
            f'<div class="dj-json-viewer__header">'
            f'<span class="dj-json-viewer__label">{e_label}</span>'
            f"{copy_html}</div>"
            f'<div class="dj-json-viewer__tree">{tree_html}</div>'
            f'<script type="application/json" class="dj-json-viewer__raw">'
            f"{conditional_escape(raw_json)}</script>"
            f"</div>"
        )


class LogViewerHandler:
    """Inline handler for {% log_viewer lines=log_lines %}"""

    LEVEL_RE = _re_mod.compile(
        r"\b(INFO|WARN(?:ING)?|ERROR|DEBUG|TRACE|FATAL|CRITICAL)\b", _re_mod.IGNORECASE
    )

    @classmethod
    def _detect_level(cls, line):
        m = cls.LEVEL_RE.search(line)
        if m:
            level = m.group(1).upper()
            if level in ("WARN", "WARNING"):
                return "warn"
            if level in ("ERROR", "FATAL", "CRITICAL"):
                return "error"
            if level in ("DEBUG", "TRACE"):
                return "debug"
            return "info"
        return ""

    def render(self, args, context):
        kw = _parse_args(args, context)
        lines = kw.get("lines", [])
        stream_event = kw.get("stream_event", "")
        show_line_numbers = kw.get("show_line_numbers", True)
        auto_scroll = kw.get("auto_scroll", True)
        filter_level = kw.get("filter_level", "")
        wrap = kw.get("wrap", False)
        max_lines = kw.get("max_lines", 0)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        classes = ["dj-log-viewer"]
        if wrap:
            classes.append("dj-log-viewer--wrap")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(lines, list):
            lines = []
        try:
            max_lines = int(max_lines)
        except (ValueError, TypeError):
            max_lines = 0

        display_lines = lines
        if max_lines and max_lines > 0:
            display_lines = display_lines[-max_lines:]

        lines_html = []
        for i, line in enumerate(display_lines):
            line_str = str(line)
            level = self._detect_level(line_str)
            e_line = conditional_escape(line_str)

            if filter_level and level != str(filter_level).lower():
                continue

            level_cls = f" dj-log-viewer__line--{level}" if level else ""
            num_html = ""
            if show_line_numbers:
                num_html = f'<span class="dj-log-viewer__num">{i + 1}</span>'
            lines_html.append(
                f'<div class="dj-log-viewer__line{level_cls}">'
                f'{num_html}<span class="dj-log-viewer__text">{e_line}</span></div>'
            )

        stream_attr = ""
        if stream_event:
            e_stream = conditional_escape(str(stream_event))
            stream_attr = f' data-stream-event="{e_stream}"'

        scroll_attr = ' data-auto-scroll="true"' if auto_scroll else ""

        return mark_safe(
            f'<div class="{class_str}" dj-hook="LogViewer"'
            f'{stream_attr}{scroll_attr} role="log" aria-live="polite">'
            f'<div class="dj-log-viewer__body">{"".join(lines_html)}</div>'
            f"</div>"
        )


class FileTreeHandler:
    """Inline handler for {% file_tree nodes=files %}"""

    FOLDER_ICON = "&#x1F4C1;"
    FOLDER_OPEN_ICON = "&#x1F4C2;"
    DEFAULT_FILE_ICON = "&#x1F4C4;"

    def _render_tree_node(self, node, depth, event, show_icons, selected):
        if not isinstance(node, dict):
            return ""

        name = str(node.get("name", ""))
        node_type = str(node.get("type", "file"))
        children = node.get("children", [])
        expanded = node.get("expanded", True)
        e_name = conditional_escape(name)
        e_type = conditional_escape(node_type)

        is_selected = name == selected
        selected_cls = " dj-file-tree__node--selected" if is_selected else ""
        type_cls = f" dj-file-tree__node--{e_type}"

        icon_html = ""
        if show_icons:
            if node_type == "folder":
                icon = self.FOLDER_OPEN_ICON if expanded else self.FOLDER_ICON
            else:
                icon = self.DEFAULT_FILE_ICON
            icon_html = f'<span class="dj-file-tree__icon" aria-hidden="true">{icon}</span>'

        indent_style = f' style="padding-left:{depth * 1.25}rem"'
        e_event = conditional_escape(str(event))

        if node_type == "folder" and isinstance(children, list) and children:
            expand_cls = " dj-file-tree__node--expanded" if expanded else ""
            toggle = (
                f'<span class="dj-file-tree__toggle" role="button" tabindex="0" '
                f'aria-expanded="{"true" if expanded else "false"}">'
                f"{'&#9660;' if expanded else '&#9654;'}</span>"
            )
            children_html = []
            for child in children:
                children_html.append(
                    self._render_tree_node(child, depth + 1, event, show_icons, selected)
                )
            child_display = ' style="display:none"' if not expanded else ""
            return (
                f'<div class="dj-file-tree__node{type_cls}{selected_cls}{expand_cls}"'
                f'{indent_style} data-name="{e_name}" data-type="{e_type}">'
                f"{toggle}{icon_html}"
                f'<span class="dj-file-tree__name">{e_name}</span></div>'
                f'<div class="dj-file-tree__children"{child_display}>'
                f"{''.join(children_html)}</div>"
            )

        return (
            f'<div class="dj-file-tree__node{type_cls}{selected_cls}"'
            f'{indent_style} data-name="{e_name}" data-type="{e_type}" '
            f'dj-click="{e_event}" role="treeitem" tabindex="0">'
            f'{icon_html}<span class="dj-file-tree__name">{e_name}</span></div>'
        )

    def render(self, args, context):
        kw = _parse_args(args, context)
        nodes = kw.get("nodes", [])
        selected = kw.get("selected", "")
        event = kw.get("event", "select_file")
        show_icons = kw.get("show_icons", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        e_event = conditional_escape(str(event))
        e_selected = conditional_escape(str(selected))

        classes = ["dj-file-tree"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(nodes, list):
            nodes = []

        nodes_html = []
        for node in nodes:
            nodes_html.append(self._render_tree_node(node, 0, event, show_icons, str(selected)))

        return mark_safe(
            f'<div class="{class_str}" dj-hook="FileTree" '
            f'data-event="{e_event}" data-selected="{e_selected}" '
            f'role="tree">{"".join(nodes_html)}</div>'
        )


class TourHandler:
    """Inline handler for {% tour steps=tour_steps active=0 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


INLINE_HANDLERS.extend(
    [
        ("terminal", TerminalHandler()),
        ("markdown_editor", MarkdownEditorHandler()),
        ("json_viewer", JsonViewerHandler()),
        ("log_viewer", LogViewerHandler()),
        ("file_tree", FileTreeHandler()),
        ("tour", TourHandler()),
    ]
)


# ---------------------------------------------------------------------------
# v2.0 Batch 4 — Enterprise + Specialized Components
# ---------------------------------------------------------------------------

import calendar as _calendar_mod


class CalendarViewHandler:
    """Inline handler for {% calendar events=events month=month year=year %}"""

    COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6"]

    def render(self, args, context):
        kw = _parse_args(args, context)
        events = kw.get("events", [])
        month = kw.get("month", 1)
        year = kw.get("year", 2026)
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
            month_name = _calendar_mod.month_name[month]
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
            cal = _calendar_mod.Calendar(firstweekday=start_day)
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


class GanttChartHandler:
    """Inline handler for {% gantt_chart tasks=tasks %}"""

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

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class DiffViewerHandler:
    """Inline handler for {% diff_viewer old=old_text new=new_text %}"""

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

    def render(self, args, context):
        kw = _parse_args(args, context)
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
        old_rows, new_rows = [], []
        old_num = new_num = 0

        for tag, old_line, new_line in ops:
            if tag == "equal":
                old_num += 1
                new_num += 1
                num_o = f'<span class="dj-diff__num">{old_num}</span>' if show_ln else ""
                num_n = f'<span class="dj-diff__num">{new_num}</span>' if show_ln else ""
                old_rows.append(
                    f'<div class="dj-diff__line">{num_o}<span class="dj-diff__text">{conditional_escape(old_line)}</span></div>'
                )
                new_rows.append(
                    f'<div class="dj-diff__line">{num_n}<span class="dj-diff__text">{conditional_escape(new_line)}</span></div>'
                )
            elif tag == "delete":
                old_num += 1
                num_html = f'<span class="dj-diff__num">{old_num}</span>' if show_ln else ""
                old_rows.append(
                    f'<div class="dj-diff__line dj-diff__line--del">{num_html}<span class="dj-diff__text">{conditional_escape(old_line)}</span></div>'
                )
                new_rows.append('<div class="dj-diff__line dj-diff__line--empty"></div>')
            elif tag == "insert":
                new_num += 1
                num_html = f'<span class="dj-diff__num">{new_num}</span>' if show_ln else ""
                old_rows.append('<div class="dj-diff__line dj-diff__line--empty"></div>')
                new_rows.append(
                    f'<div class="dj-diff__line dj-diff__line--add">{num_html}<span class="dj-diff__text">{conditional_escape(new_line)}</span></div>'
                )

        return (
            f'<div class="{class_str}">'
            f'<div class="dj-diff__pane dj-diff__pane--old"><div class="dj-diff__pane-header">{e_title_old}</div>{"".join(old_rows)}</div>'
            f'<div class="dj-diff__pane dj-diff__pane--new"><div class="dj-diff__pane-header">{e_title_new}</div>{"".join(new_rows)}</div></div>'
        )

    def _render_unified(self, class_str, ops, show_ln):
        rows = []
        old_num = new_num = 0
        for tag, old_line, new_line in ops:
            if tag == "equal":
                old_num += 1
                new_num += 1
                num_html = (
                    f'<span class="dj-diff__num">{old_num}</span><span class="dj-diff__num">{new_num}</span>'
                    if show_ln
                    else ""
                )
                rows.append(
                    f'<div class="dj-diff__line">{num_html}<span class="dj-diff__marker"> </span><span class="dj-diff__text">{conditional_escape(old_line)}</span></div>'
                )
            elif tag == "delete":
                old_num += 1
                num_html = (
                    f'<span class="dj-diff__num">{old_num}</span><span class="dj-diff__num"></span>'
                    if show_ln
                    else ""
                )
                rows.append(
                    f'<div class="dj-diff__line dj-diff__line--del">{num_html}<span class="dj-diff__marker">-</span><span class="dj-diff__text">{conditional_escape(old_line)}</span></div>'
                )
            elif tag == "insert":
                new_num += 1
                num_html = (
                    f'<span class="dj-diff__num"></span><span class="dj-diff__num">{new_num}</span>'
                    if show_ln
                    else ""
                )
                rows.append(
                    f'<div class="dj-diff__line dj-diff__line--add">{num_html}<span class="dj-diff__marker">+</span><span class="dj-diff__text">{conditional_escape(new_line)}</span></div>'
                )
        return f'<div class="{class_str}"><div class="dj-diff__unified">{"".join(rows)}</div></div>'


class PivotTableHandler:
    """Inline handler for {% pivot_table data=data rows="category" cols="quarter" values="revenue" agg="sum" %}"""

    AGG_FUNCS = {
        "sum": sum,
        "avg": lambda vals: sum(vals) / len(vals) if vals else 0,
        "count": len,
        "min": lambda vals: min(vals) if vals else 0,
        "max": lambda vals: max(vals) if vals else 0,
    }

    @staticmethod
    def _format_val(v):
        if v == int(v):
            return str(int(v))
        return f"{v:.2f}"

    def render(self, args, context):
        kw = _parse_args(args, context)
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

        row_keys, col_keys = [], []
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
            f'<div class="{class_str}"><table class="dj-pivot__table" role="grid">{"".join(parts)}</table></div>'
        )


class OrgChartHandler:
    """Inline handler for {% org_chart nodes=nodes root=ceo_id %}"""

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
            avatar_html = f'<img class="dj-org__avatar" src="{conditional_escape(str(avatar))}" alt="{name}" />'
        else:
            initials = "".join(w[0] for w in str(node.get("name", "")).split()[:2]).upper() or "?"
            avatar_html = f'<span class="dj-org__initials">{conditional_escape(initials)}</span>'
        node_html = (
            f'<div class="dj-org__card" data-id="{conditional_escape(nid)}"{click_attr}>'
            f'{avatar_html}<div class="dj-org__info">'
            f'<span class="dj-org__name">{name}</span>'
            f'<span class="dj-org__title">{title}</span></div></div>'
        )
        child_ids = children.get(nid, [])
        if not child_ids:
            return f'<li class="dj-org__node">{node_html}</li>'
        child_items = "".join(
            self._render_node(cid, node_map, children, e_event) for cid in child_ids
        )
        return f'<li class="dj-org__node">{node_html}<ul class="dj-org__children">{child_items}</ul></li>'

    def render(self, args, context):
        kw = _parse_args(args, context)
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
        child_ids_set = set()
        for n in nodes:
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id", ""))
            if not nid:
                continue
            node_map[nid] = n
            parent = n.get("parent", "")
            if parent:
                children.setdefault(str(parent), []).append(nid)
                child_ids_set.add(nid)

        root_id = str(root) if root else ""
        if not root_id:
            roots = [nid for nid in node_map if nid not in child_ids_set]
            root_id = roots[0] if roots else (list(node_map.keys())[0] if node_map else "")

        if not node_map or not root_id:
            return mark_safe(f'<div class="{class_str}" role="tree"></div>')

        e_event = conditional_escape(str(event)) if event else ""
        tree_html = self._render_node(root_id, node_map, children, e_event)
        return mark_safe(
            f'<div class="{class_str}" role="tree"><ul class="dj-org__root">{tree_html}</ul></div>'
        )


class ComparisonTableHandler:
    """Inline handler for {% comparison_table plans=plans features=features %}"""

    @staticmethod
    def _render_value(val):
        if val is True:
            return '<span class="dj-compare__check" aria-label="Yes">&#10003;</span>'
        if val is False:
            return '<span class="dj-compare__cross" aria-label="No">&#10007;</span>'
        return conditional_escape(str(val))

    def render(self, args, context):
        kw = _parse_args(args, context)
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
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            name = conditional_escape(str(plan.get("name", "")))
            price = conditional_escape(str(plan.get("price", "")))
            highlighted = plan.get("highlighted", False)
            hl_class = " dj-compare__plan--highlighted" if highlighted else ""
            click_attr = f' dj-click="{e_event}" data-value="{name}"' if e_event else ""
            price_html = f'<div class="dj-compare__price">{price}</div>' if price else ""
            header_cells.append(
                f'<th class="dj-compare__plan{hl_class}"{click_attr}>'
                f'<div class="dj-compare__plan-name">{name}</div>{price_html}</th>'
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
            f'<div class="{class_str}"><table class="dj-compare__table" role="grid">{header}{body}</table></div>'
        )


class MasonryGridHandler:
    """Inline handler for {% masonry_grid items=items columns=3 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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
        for items_in_col in col_items:
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


INLINE_HANDLERS.extend(
    [
        ("calendar", CalendarViewHandler()),
        ("gantt_chart", GanttChartHandler()),
        ("diff_viewer", DiffViewerHandler()),
        ("pivot_table", PivotTableHandler()),
        ("org_chart", OrgChartHandler()),
        ("comparison_table", ComparisonTableHandler()),
        ("masonry_grid", MasonryGridHandler()),
    ]
)


# ---------------------------------------------------------------------------
# v2.0 Batch 5 — Collaboration Suite
# ---------------------------------------------------------------------------


class CursorsOverlayHandler:
    """Inline handler for {% cursors users=users %}"""

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

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class LiveIndicatorHandler:
    """Inline handler for {% live_indicator user=user field="title" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class CollabSelectionHandler:
    """Inline handler for {% collab_selection users=users %}"""

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

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class ActivityFeedHandler:
    """Inline handler for {% activity_feed events=events stream=True %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
        events = kw.get("events", [])
        stream_event = kw.get("stream", "")
        max_items = 50
        try:
            max_items = int(kw.get("max", 50))
        except (ValueError, TypeError):
            pass
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


class ReactionsHandler:
    """Inline handler for {% reactions options=emojis counts=counts event="react" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


INLINE_HANDLERS.extend(
    [
        ("cursors", CursorsOverlayHandler()),
        ("live_indicator", LiveIndicatorHandler()),
        ("collab_selection", CollabSelectionHandler()),
        ("activity_feed", ActivityFeedHandler()),
        ("reactions", ReactionsHandler()),
    ]
)


# ===========================================================================
# v2.0 FINAL BATCH HANDLERS
# ===========================================================================


class MapPickerHandler:
    """Inline handler for {% map_picker lat=lat lng=lng pick_event="set_location" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class PromptEditorHandler:
    """Inline handler for {% prompt_editor template=t variables=v event="save_prompt" %}"""

    def render(self, args, context):
        import re as _re

        kw = _parse_args(args, context)
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

        preview_text = conditional_escape(str(tmpl))
        for v in unique_vars:
            val = variables.get(v, "{{" + v + "}}")
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


class VoiceInputHandler:
    """Inline handler for {% voice_input event="transcribe" lang="en-US" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class CronInputHandler:
    """Inline handler for {% cron_input name="schedule" value="0 9 * * 1-5" %}"""

    FIELD_LABELS = ["Minute", "Hour", "Day", "Month", "Weekday"]

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class ErrorPageHandler:
    """Inline handler for {% error_page code=404 title="Not Found" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class ImageUploadPreviewHandler:
    """Inline handler for {% image_upload_preview name="photos" max=5 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class AnimatedNumberHandler:
    """Inline handler for {% animated_number value=revenue prefix="$" %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class RibbonHandler:
    """Inline handler for {% ribbon text="Popular" variant="primary" %}"""

    VARIANT_MAP = {
        "primary": "dj-ribbon--primary",
        "success": "dj-ribbon--success",
        "warning": "dj-ribbon--warning",
        "danger": "dj-ribbon--danger",
    }

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class BreadcrumbDropdownHandler:
    """Inline handler for {% breadcrumb_dropdown items=items %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class DataCardGridHandler:
    """Inline handler for {% data_card_grid items=items columns=3 %}"""

    def render(self, args, context):
        kw = _parse_args(args, context)
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


class AgentStepHandler:
    """Block handler for {% agent_step tool="search_db" status="complete" %}...{% endagent_step %}"""

    STATUS_ICONS = {
        "pending": "&#9711;",
        "running": "&#8987;",
        "complete": "&#10003;",
        "error": "&#10007;",
    }

    def render(self, args, content, context):
        kw = _parse_args(args, context)
        tool = kw.get("tool", "")
        status = kw.get("status", "pending")
        duration = kw.get("duration", "")
        custom_class = kw.get("class", "")

        status = str(status) if str(status) in self.STATUS_ICONS else "pending"

        e_class = conditional_escape(str(custom_class))
        e_tool = conditional_escape(str(tool))
        e_duration = conditional_escape(str(duration))
        e_content = conditional_escape(str(content).strip()) if content else ""

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


class QRCodeHandler:
    """Inline handler for {% qr_code data="https://example.com" size="md" %}"""

    SIZE_MAP = {"sm": 128, "md": 200, "lg": 300}

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

    def render(self, args, context):
        kw = _parse_args(args, context)
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


INLINE_HANDLERS.extend(
    [
        ("map_picker", MapPickerHandler()),
        ("prompt_editor", PromptEditorHandler()),
        ("voice_input", VoiceInputHandler()),
        ("cron_input", CronInputHandler()),
        ("error_page", ErrorPageHandler()),
        ("image_upload_preview", ImageUploadPreviewHandler()),
        ("animated_number", AnimatedNumberHandler()),
        ("ribbon", RibbonHandler()),
        ("breadcrumb_dropdown", BreadcrumbDropdownHandler()),
        ("data_card_grid", DataCardGridHandler()),
        ("qr_code", QRCodeHandler()),
    ]
)

BLOCK_HANDLERS.extend(
    [
        ("agent_step", "endagent_step", AgentStepHandler()),
    ]
)
