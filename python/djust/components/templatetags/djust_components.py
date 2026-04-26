"""
Template tags for djust-components.

Usage:
    {% load djust_components %}
    {% modal id="my-modal" title="Confirm" open=modal_open %}...{% endmodal %}
    {% tabs id="my-tabs" active=active_tab %}
        {% tab "overview" label="Overview" %}...{% endtab %}
        {% tab "settings" label="Settings" %}...{% endtab %}
    {% endtabs %}
"""

import calendar as _calendar
import datetime
import json as _json
import re as _re
import uuid

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe


# Shared register instance — sub-modules import from _registry and register
# their tags on the same Library.  We re-export it here so that
# {% load djust_components %} picks up all tags.
from djust.components.templatetags._registry import register  # noqa: F401

# Import sub-modules so their @register decorators execute at load time.
# Done via importlib so static analyzers don't flag them as "unused imports" —
# the import is purely for the side effect of running each module's top-level code.
import importlib

for _submodule in (
    "djust.components.templatetags._charts",
    "djust.components.templatetags._dev_tools",
    "djust.components.templatetags._advanced",
):
    importlib.import_module(_submodule)
del _submodule, importlib

# Re-export helpers moved to sub-modules for backward compatibility
# (importing from _forms also triggers its @register decorators at load time)
from djust.components.templatetags._forms import (  # noqa: E402
    _get_field_type,  # noqa: F401
    _infer_columns,  # noqa: F401
    _queryset_to_rows,  # noqa: F401
)

# Explicit re-export so `from djust_components import _get_field_type` works
# and static analyzers (CodeQL) recognize these as public re-exports.
__all__ = ("_get_field_type", "_infer_columns", "_queryset_to_rows", "register")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve(value, context):
    """Resolve a template variable or return the literal value."""
    if isinstance(value, template.Variable):
        try:
            return value.resolve(context)
        except template.VariableDoesNotExist:
            return ""
    return value


def _parse_kv_args(bits, parser):
    """Parse key=value arguments from template tag tokens."""
    kwargs = {}
    for bit in bits:
        if "=" in bit:
            key, val = bit.split("=", 1)
            # Strip quotes for literal strings
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                kwargs[key] = val[1:-1]
            else:
                kwargs[key] = template.Variable(val)
        else:
            raise template.TemplateSyntaxError(
                f"Unexpected argument '{bit}'. Use key=value format."
            )
    return kwargs


# ---------------------------------------------------------------------------
# 1. Modal
# ---------------------------------------------------------------------------


class ModalNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        title = kw.get("title", "")
        is_open = kw.get("open", False)
        size = kw.get("size", "md")  # sm, md, lg, xl
        close_event = kw.get("close_event", "close_modal")

        if not is_open:
            return ""

        content = self.nodelist.render(context)
        size_class = {
            "sm": "dj-modal--sm",
            "md": "dj-modal--md",
            "lg": "dj-modal--lg",
            "xl": "dj-modal--xl",
        }.get(size, "dj-modal--md")
        e_close_event = conditional_escape(close_event)
        e_title = conditional_escape(title)
        component_id = kw.get("component_id", "")
        cid_attr = (
            f' data-component-id="{conditional_escape(component_id)}"' if component_id else ""
        )

        return mark_safe(f"""<div class="dj-modal-backdrop" dj-click="{e_close_event}"{cid_attr}>
  <div class="dj-modal {size_class}" onclick="event.stopPropagation()">
    <div class="dj-modal__header">
      <h3 class="dj-modal__title">{e_title}</h3>
      <button class="dj-modal__close" dj-click="{e_close_event}"{cid_attr}>&times;</button>
    </div>
    <div class="dj-modal__body">{content}</div>
  </div>
</div>""")


@register.tag("modal")
def do_modal(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endmodal",))
    parser.delete_first_token()
    return ModalNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# 2. Tabs
# ---------------------------------------------------------------------------


class TabNode(template.Node):
    """A single tab pane."""

    def __init__(self, tab_id, label, icon, nodelist):
        self.tab_id = tab_id
        self.label = label
        self.icon = icon
        self.nodelist = nodelist

    def render(self, context):
        return self.nodelist.render(context)


class TabsNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        tabs_id = kw.get("id", "tabs")
        active = kw.get("active", "")
        event = kw.get("event", "set_tab")

        # Collect tab nodes
        tabs = [n for n in self.nodelist if isinstance(n, TabNode)]
        if not active and tabs:
            active = _resolve(tabs[0].tab_id, context)

        component_id = kw.get("component_id", "")
        cid_attr = (
            f' data-component-id="{conditional_escape(component_id)}"' if component_id else ""
        )

        # Build tab nav
        nav_items = []
        for tab in tabs:
            tid = _resolve(tab.tab_id, context)
            label = _resolve(tab.label, context)
            icon = _resolve(tab.icon, context) if tab.icon else ""
            active_cls = "dj-tab--active" if tid == active else ""
            icon_html = (
                f'<span class="dj-tab__icon">{conditional_escape(icon)}</span> ' if icon else ""
            )
            nav_items.append(
                f'<button class="dj-tab {active_cls}" '
                f'dj-click="{conditional_escape(event)}" data-value="{conditional_escape(tid)}"{cid_attr}>'
                f"{icon_html}{conditional_escape(label)}</button>"
            )

        nav = f'<nav class="dj-tabs__nav">{"".join(nav_items)}</nav>'

        # Build active pane
        pane = ""
        for tab in tabs:
            tid = _resolve(tab.tab_id, context)
            if tid == active:
                pane = f'<div class="dj-tabs__pane">{tab.render(context)}</div>'
                break

        return mark_safe(
            f'<div class="dj-tabs" id="{conditional_escape(tabs_id)}">{nav}{pane}</div>'
        )


@register.tag("tabs")
def do_tabs(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endtabs",))
    parser.delete_first_token()
    return TabsNode(nodelist, kwargs)


@register.tag("tab")
def do_tab(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    tab_id = kwargs.get("id", "")
    label = kwargs.get("label", "")
    icon = kwargs.get("icon", "")
    nodelist = parser.parse(("endtab",))
    parser.delete_first_token()
    return TabNode(tab_id, label, icon, nodelist)


# ---------------------------------------------------------------------------
# 3. Accordion
# ---------------------------------------------------------------------------


class AccordionItemNode(template.Node):
    def __init__(self, item_id, title, nodelist):
        self.item_id = item_id
        self.title = title
        self.nodelist = nodelist

    def render(self, context):
        return self.nodelist.render(context)


class AccordionNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        accordion_id = kw.get("id", "accordion")
        active = kw.get("active", "")
        event = kw.get("event", "accordion_toggle")

        component_id = kw.get("component_id", "")
        cid_attr = (
            f' data-component-id="{conditional_escape(component_id)}"' if component_id else ""
        )

        items = [n for n in self.nodelist if isinstance(n, AccordionItemNode)]
        parts = []
        for idx, item in enumerate(items):
            iid = _resolve(item.item_id, context) or f"item-{idx}"
            title = _resolve(item.title, context)
            is_open = iid == active
            open_cls = "dj-accordion-item--open" if is_open else ""
            chevron_cls = "dj-accordion__chevron--open" if is_open else ""
            content_html = ""
            if is_open:
                content_html = f'<div class="dj-accordion__content">{item.render(context)}</div>'
            parts.append(
                f'<div class="dj-accordion-item {open_cls}">'
                f'<button class="dj-accordion__trigger" dj-click="{conditional_escape(event)}" data-value="{conditional_escape(iid)}"{cid_attr}>'
                f"<span>{conditional_escape(title)}</span>"
                f'<span class="dj-accordion__chevron {chevron_cls}">&#9662;</span>'
                f"</button>"
                f"{content_html}</div>"
            )

        return mark_safe(
            f'<div class="dj-accordion" id="{conditional_escape(accordion_id)}">{"".join(parts)}</div>'
        )


@register.tag("accordion")
def do_accordion(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endaccordion",))
    parser.delete_first_token()
    return AccordionNode(nodelist, kwargs)


@register.tag("accordion_item")
def do_accordion_item(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    item_id = kwargs.get("id", "")
    title = kwargs.get("title", "")
    nodelist = parser.parse(("endaccordion_item",))
    parser.delete_first_token()
    return AccordionItemNode(item_id, title, nodelist)


# ---------------------------------------------------------------------------
# 4. Dropdown
# ---------------------------------------------------------------------------


class DropdownNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        dropdown_id = kw.get("id", "dropdown")
        label = kw.get("label", "Menu")
        is_open = kw.get("open", False)
        toggle_event = kw.get("toggle_event", "toggle_dropdown")
        variant = kw.get("variant", "default")

        content = self.nodelist.render(context)
        open_attr = " data-open" if is_open else ""
        variant_cls = f"dj-dropdown--{conditional_escape(variant)}"
        component_id = kw.get("component_id", "")
        cid_attr = (
            f' data-component-id="{conditional_escape(component_id)}"' if component_id else ""
        )

        menu_html = ""
        if is_open:
            menu_html = f'<div class="dj-dropdown__menu">{content}</div>'

        return mark_safe(
            f'<div class="dj-dropdown {variant_cls}" id="{conditional_escape(dropdown_id)}"{open_attr}>'
            f'<button class="dj-dropdown__trigger" dj-click="{conditional_escape(toggle_event)}"{cid_attr}>{conditional_escape(label)}</button>'
            f"{menu_html}</div>"
        )


@register.tag("dropdown")
def do_dropdown(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("enddropdown",))
    parser.delete_first_token()
    return DropdownNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# 5. Toast
# ---------------------------------------------------------------------------


@register.inclusion_tag("djust_components/toast.html")
def toast_container(toasts, dismiss_event="dismiss_toast"):
    """Render a stack of toast notifications.

    Args:
        toasts: list of dicts with keys: id, type (success|error|warning|info), message
        dismiss_event: djust event name for dismissing a toast
    """
    return {"toasts": toasts, "dismiss_event": dismiss_event}


# ---------------------------------------------------------------------------
# 6. Tooltip
# ---------------------------------------------------------------------------


class TooltipNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        text = kw.get("text", "")
        position = kw.get("position", "top")  # top, bottom, left, right
        content = self.nodelist.render(context)
        component_id = kw.get("component_id", "")
        cid_attr = (
            f' data-component-id="{conditional_escape(component_id)}"' if component_id else ""
        )

        return mark_safe(
            f'<span class="dj-tooltip dj-tooltip--{conditional_escape(position)}"{cid_attr}>'
            f"{content}"
            f'<span class="dj-tooltip__text">{conditional_escape(text)}</span>'
            f"</span>"
        )


@register.tag("tooltip")
def do_tooltip(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endtooltip",))
    parser.delete_first_token()
    return TooltipNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# 7. Progress
# ---------------------------------------------------------------------------


@register.inclusion_tag("djust_components/progress.html")
def progress(value=0, label="", size="md", color="primary", show_label=True):
    """Render a progress bar.

    Args:
        value: 0-100
        label: text label
        size: sm, md, lg
        color: primary, success, warning, danger
        show_label: whether to show percentage
    """
    value = max(0, min(100, int(value)))
    return {
        "value": value,
        "label": label,
        "size": size,
        "color": color,
        "show_label": show_label,
    }


# ---------------------------------------------------------------------------
# 8. Badge
# ---------------------------------------------------------------------------


@register.inclusion_tag("djust_components/badge.html")
def badge(label="", status="default", pulse=False):
    """Render a status badge.

    Args:
        label: display text
        status: online, offline, warning, error, default
        pulse: whether the dot should animate
    """
    return {"label": label, "status": status, "pulse": pulse}


# ---------------------------------------------------------------------------
# 9. Card
# ---------------------------------------------------------------------------


class CardNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        title = kw.get("title", "")
        subtitle = kw.get("subtitle", "")
        variant = kw.get("variant", "default")  # default, outlined, elevated
        extra_class = kw.get("class", "")

        content = self.nodelist.render(context)

        header = ""
        if title:
            sub = (
                f'<p class="dj-card__subtitle">{conditional_escape(subtitle)}</p>'
                if subtitle
                else ""
            )
            header = f'<div class="dj-card__header"><h3 class="dj-card__title">{conditional_escape(title)}</h3>{sub}</div>'

        return mark_safe(
            f'<div class="dj-card dj-card--{conditional_escape(variant)} {conditional_escape(extra_class)}">'
            f"{header}"
            f'<div class="dj-card__body">{content}</div>'
            f"</div>"
        )


@register.tag("card")
def do_card(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endcard",))
    parser.delete_first_token()
    return CardNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# 10. Table
# ---------------------------------------------------------------------------


@register.inclusion_tag("djust_components/table.html")
def data_table(
    rows,
    columns,
    sort_by="",
    sort_desc=False,
    sort_event="table_sort",
    page=1,
    total_pages=1,
    prev_event="table_prev",
    next_event="table_next",
    selectable=False,
    selected_rows=None,
    select_event="table_select",
    row_key="id",
    search=False,
    search_query="",
    search_event="table_search",
    search_debounce=300,
    filters=None,
    filter_event="table_filter",
    loading=False,
    empty_title="No data",
    empty_description="",
    empty_icon="",
    paginate=False,
    page_event="table_page",
    striped=False,
    compact=False,
    # Phase 2 params
    editable_columns=None,
    edit_event="table_cell_edit",
    resizable=False,
    reorderable=False,
    reorder_event="table_reorder",
    frozen_left=0,
    frozen_right=0,
    column_visibility=False,
    visibility_event="table_visibility",
    density="comfortable",
    density_toggle=False,
    density_event="table_density",
    responsive_cards=False,
    editable_rows=False,
    edit_row_event="table_row_edit",
    save_row_event="table_row_save",
    cancel_row_event="table_row_cancel",
    editing_rows=None,
    # Phase 3 params
    expandable=False,
    expand_event="table_expand",
    expanded_rows=None,
    bulk_actions=None,
    bulk_action_event="table_bulk_action",
    exportable=False,
    export_event="table_export",
    export_formats=None,
    group_by="",
    group_event="table_group",
    group_toggle_event="table_group_toggle",
    collapsible_groups=True,
    collapsed_groups=None,
    keyboard_nav=False,
    virtual_scroll=False,
    virtual_row_height=40,
    virtual_buffer=5,
    server_mode=False,
    facets=False,
    facet_counts=None,
    persist_key="",
    printable=False,
    column_stats=None,
    # Phase 4 params
    footer_aggregations=None,
    row_class_map=None,
    column_groups=None,
    row_drag=False,
    row_drag_event="table_row_drag",
    copyable=False,
    copy_event="table_copy",
    copy_format="csv",
    # Phase 5 params
    importable=False,
    import_event="table_import",
    import_formats=None,
    import_preview=True,
    import_preview_data=None,
    import_errors=None,
    import_pending=False,
    computed_columns=None,
    cell_merge_key="_merge",
    column_expressions=None,
    expression_event="table_expression",
    active_expressions=None,
    conditional_formatting=None,
    # Phase 6 params (#1111: row-level navigation)
    row_click_event="",
    row_click_value_key="id",
    row_url="",
):
    """Render a sortable data table with search, filters, selection, pagination, and editing.

    Phase 1 args:
        rows: list of dicts
        columns: list of dicts with keys: key, label, sortable, filterable, filter_type, filter_options, width
        sort_by: current sort column key
        sort_desc: sort descending?
        sort_event: djust event for sorting
        page: current page number
        total_pages: total pages
        prev_event: djust event for previous page
        next_event: djust event for next page
        selectable: enable row selection checkboxes
        selected_rows: list of selected row IDs/keys
        select_event: selection event name
        row_key: key field for row identity
        search: show global search box
        search_query: current search value
        search_event: search event name
        search_debounce: debounce ms for search input
        filters: per-column filter values {col_key: value}
        filter_event: filter event name
        loading: show loading/skeleton state
        empty_title: empty state title
        empty_description: empty state description
        empty_icon: empty state icon
        paginate: show pagination controls
        page_event: pagination event name
        striped: alternating row backgrounds
        compact: reduced padding

    Phase 2 args:
        editable_columns: list of column keys that support inline editing
        edit_event: inline cell edit event name
        resizable: enable column resize (client-side JS)
        reorderable: enable column reorder via drag (client-side JS)
        reorder_event: column reorder persist event
        frozen_left: number of columns frozen on the left
        frozen_right: number of columns frozen on the right
        column_visibility: show column visibility dropdown
        visibility_event: column visibility toggle persist event
        density: row density — "compact", "comfortable", or "spacious"
        density_toggle: show density toggle buttons
        density_event: density change event
        responsive_cards: collapse rows to stacked cards on narrow viewports
        editable_rows: enable row edit mode with Edit/Save/Cancel buttons
        edit_row_event: enter row edit mode event
        save_row_event: save edited row event
        cancel_row_event: cancel row edit event
        editing_rows: list of row keys currently in edit mode

    Phase 3 args:
        expandable: enable row expansion with detail rows
        expand_event: row expand toggle event name
        expanded_rows: list of expanded row IDs/keys
        bulk_actions: list of dicts with key/label for bulk action buttons
        bulk_action_event: bulk action event name
        exportable: show export buttons
        export_event: export event name
        export_formats: list of export formats (csv, json)
        group_by: column key to group rows by
        group_event: group change event name
        group_toggle_event: group collapse/expand toggle event
        collapsible_groups: allow group collapse/expand
        collapsed_groups: list of collapsed group values
        keyboard_nav: enable keyboard navigation
        virtual_scroll: enable virtual scrolling for large datasets
        virtual_row_height: row height in px for virtual scroll
        virtual_buffer: number of buffer rows for virtual scroll
        server_mode: explicit server-driven sort/filter/page
        facets: show faceted filtering with counts
        facet_counts: dict of {col_key: {value: count}}
        persist_key: localStorage key for state persistence
        printable: add print-friendly styles
        column_stats: dict of {col_key: {min, max, avg, sum, count}}

    Phase 4 args:
        footer_aggregations: dict of {col_key: "sum"|"avg"|"count"|"min"|"max"}
        row_class_map: dict of {col_key: {value: css_class}} for conditional row styling
        column_groups: list of dicts {label, columns} for multi-level headers
        row_drag: enable row drag-and-drop reorder
        row_drag_event: row reorder event name
        copyable: enable copy rows to clipboard
        copy_event: copy event name
        copy_format: "csv" or "tsv"

    Phase 5 args:
        importable: show import button/dropzone
        import_event: import event name
        import_formats: list of import formats (csv, json)
        import_preview: preview imported data before confirming
        import_preview_data: staged import rows for preview
        import_errors: import validation errors
        import_pending: whether import preview is awaiting confirmation
        computed_columns: list of virtual computed column dicts
        cell_merge_key: row data key holding colspan info
        column_expressions: dict of column expression filter configs
        expression_event: column expression filter event name
        active_expressions: dict of active column expression filters
        conditional_formatting: list of formatting preset dicts

    Phase 6 args (#1110, #1111):
        row_click_event: dj-click event name fired when any <tr> is clicked.
            Empty string (default) disables row-level click events.
            LiveView-idiomatic — preferred over row_url for routing inside
            a LiveView app. CSP-friendly (no inline JS).
        row_click_value_key: row dict key whose value is sent as data-value
            on the dj-click. Default "id". Override for slug-based routing
            (e.g. "uuid", "slug").
        row_url: row dict key holding a URL. When set, the <tr> gets
            data-href + an inline onclick that navigates via
            window.location. **Security**: values flow into JS via
            ``this.dataset.href`` — only assign developer-controlled URLs
            (typically computed from ``reverse()``). User-controlled
            strings could enable ``javascript:`` URI execution.
            **CSP**: requires ``'unsafe-inline'`` in script-src; prefer
            row_click_event when CSP is strict. row_click_event takes
            precedence when both are set.

    Cell-level link column (#1110):
        column dicts now accept ``link`` (key in row dict holding the
        href) and ``link_class`` (optional CSS class on the <a>). When
        ``col.link`` is set the cell renders as
        ``<a href="{{ row[col.link] }}">{{ row[col.key] }}</a>``;
        otherwise plain text (pre-#1110 behavior).
    """
    return {
        "rows": rows,
        "columns": columns,
        "sort_by": sort_by,
        "sort_desc": sort_desc,
        "sort_event": sort_event,
        "page": page,
        "total_pages": total_pages,
        "prev_event": prev_event,
        "next_event": next_event,
        "selectable": selectable,
        "selected_rows": selected_rows or [],
        "select_event": select_event,
        "row_key": row_key,
        "search": search,
        "search_query": search_query,
        "search_event": search_event,
        "search_debounce": search_debounce,
        "filters": filters or {},
        "filter_event": filter_event,
        "loading": loading,
        "empty_title": empty_title,
        "empty_description": empty_description,
        "empty_icon": empty_icon,
        "paginate": paginate,
        "page_event": page_event,
        "striped": striped,
        "compact": compact,
        # Phase 2
        "editable_columns": editable_columns or [],
        "edit_event": edit_event,
        "resizable": resizable,
        "reorderable": reorderable,
        "reorder_event": reorder_event,
        "frozen_left": frozen_left,
        "frozen_right": frozen_right,
        "column_visibility": column_visibility,
        "visibility_event": visibility_event,
        "density": density,
        "density_toggle": density_toggle,
        "density_event": density_event,
        "responsive_cards": responsive_cards,
        "editable_rows": editable_rows,
        "edit_row_event": edit_row_event,
        "save_row_event": save_row_event,
        "cancel_row_event": cancel_row_event,
        "editing_rows": editing_rows or [],
        # Phase 3
        "expandable": expandable,
        "expand_event": expand_event,
        "expanded_rows": expanded_rows or [],
        "bulk_actions": bulk_actions or [],
        "bulk_action_event": bulk_action_event,
        "exportable": exportable,
        "export_event": export_event,
        "export_formats": export_formats or ["csv", "json"],
        "group_by": group_by,
        "group_event": group_event,
        "group_toggle_event": group_toggle_event,
        "collapsible_groups": collapsible_groups,
        "collapsed_groups": collapsed_groups or [],
        "keyboard_nav": keyboard_nav,
        "virtual_scroll": virtual_scroll,
        "virtual_row_height": virtual_row_height,
        "virtual_buffer": virtual_buffer,
        "server_mode": server_mode,
        "facets": facets,
        "facet_counts": facet_counts or {},
        "persist_key": persist_key,
        "printable": printable,
        "column_stats": column_stats or {},
        # Phase 4
        "footer_aggregations": footer_aggregations or {},
        "row_class_map": row_class_map or {},
        "column_groups": column_groups or [],
        "row_drag": row_drag,
        "row_drag_event": row_drag_event,
        "copyable": copyable,
        "copy_event": copy_event,
        "copy_format": copy_format,
        # Phase 5
        "importable": importable,
        "import_event": import_event,
        "import_formats": import_formats or ["csv", "json"],
        "import_preview": import_preview,
        "import_preview_data": import_preview_data or [],
        "import_errors": import_errors or [],
        "import_pending": import_pending,
        "computed_columns": computed_columns or [],
        "cell_merge_key": cell_merge_key,
        "column_expressions": column_expressions or {},
        "expression_event": expression_event,
        "active_expressions": active_expressions or {},
        "conditional_formatting": conditional_formatting or [],
        # Phase 6 (#1111: row-level navigation)
        "row_click_event": row_click_event,
        "row_click_value_key": row_click_value_key,
        "row_url": row_url,
    }


# ---------------------------------------------------------------------------
# 11. Pagination
# ---------------------------------------------------------------------------


@register.inclusion_tag("djust_components/pagination.html")
def pagination(page=1, total_pages=1, prev_event="page_prev", next_event="page_next"):
    """Render pagination controls."""
    pages = []
    for p in range(1, total_pages + 1):
        if p == 1 or p == total_pages or abs(p - page) <= 2:
            pages.append(p)
        elif pages and pages[-1] != "...":
            pages.append("...")
    return {
        "page": page,
        "total_pages": total_pages,
        "pages": pages,
        "prev_event": prev_event,
        "next_event": next_event,
    }


# ---------------------------------------------------------------------------
# 12. Avatar
# ---------------------------------------------------------------------------


@register.inclusion_tag("djust_components/avatar.html")
def avatar(src="", alt="", initials="", size="md", status=""):
    """Render an avatar with optional status indicator.

    Args:
        src: image URL (if empty, shows initials)
        alt: alt text
        initials: fallback initials (e.g. "JD")
        size: xs, sm, md, lg, xl
        status: online, offline, busy, away, or empty
    """
    return {
        "src": src,
        "alt": alt,
        "initials": initials or (alt[:2].upper() if alt else ""),
        "size": size,
        "status": status,
    }


# ---------------------------------------------------------------------------
# 13. Alert
# ---------------------------------------------------------------------------

_ALERT_ICONS = {
    "info": "&#8505;",
    "success": "&#10003;",
    "warning": "&#9888;",
    "error": "&#10005;",
    "danger": "&#10005;",
}


class AlertNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        alert_type = kw.get("variant", kw.get("type", "info"))
        title = kw.get("title", "")
        dismissible = kw.get("dismissible", False)
        if isinstance(dismissible, str):
            dismissible = dismissible.lower() not in ("false", "0", "")
        event = kw.get("event", "dismiss_alert")

        content = self.nodelist.render(context)

        # Normalise error/danger to the same CSS class
        css_type = "error" if alert_type == "danger" else conditional_escape(alert_type)
        icon_char = _ALERT_ICONS.get(alert_type, "&#8505;")
        dismissible_cls = " alert-dismissible" if dismissible else ""

        title_html = f'<div class="alert-title">{conditional_escape(title)}</div>' if title else ""
        close_html = (
            f'<button class="alert-close" dj-click="{conditional_escape(event)}">&times;</button>'
            if dismissible
            else ""
        )

        return mark_safe(
            f'<div class="alert alert-{css_type}{dismissible_cls}">'
            f'<span class="alert-icon">{icon_char}</span>'
            f'<div class="alert-body">'
            f"{title_html}"
            f'<div class="alert-message">{content}</div>'
            f"</div>"
            f"{close_html}"
            f"</div>"
        )


@register.tag("alert")
def do_alert(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endalert",))
    parser.delete_first_token()
    return AlertNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# 14. Button
# ---------------------------------------------------------------------------


@register.simple_tag
def dj_button(
    label="",
    variant="primary",
    event="",
    icon="",
    disabled=False,
    loading=False,
    size="md",
    preset="",
):
    """Render a button element.

    Args:
        label: button text
        variant: primary, secondary, danger, ghost, link, success, warning
        event: dj-click event name
        icon: optional icon HTML/text prepended to label
        disabled: disables the button
        loading: shows spinner and disables button
        size: sm, md, lg (md emits no extra class)
        preset: optional preset name (see ``djust_components.presets``)
    """
    # Apply preset defaults — explicit kwargs take precedence.
    if preset:
        from djust.components.presets import get_preset

        preset_params = get_preset("dj_button", preset)
        if preset_params:
            # Only apply preset values for args the caller left at defaults.
            _defaults = {
                "variant": "primary",
                "event": "",
                "icon": "",
                "disabled": False,
                "loading": False,
                "size": "md",
            }
            if label == "" and "label" in preset_params:
                label = preset_params["label"]
            if variant == _defaults["variant"] and "variant" in preset_params:
                variant = preset_params["variant"]
            if event == _defaults["event"] and "event" in preset_params:
                event = preset_params["event"]
            if icon == _defaults["icon"] and "icon" in preset_params:
                icon = preset_params["icon"]
            if disabled == _defaults["disabled"] and "disabled" in preset_params:
                disabled = preset_params["disabled"]
            if loading == _defaults["loading"] and "loading" in preset_params:
                loading = preset_params["loading"]
            if size == _defaults["size"] and "size" in preset_params:
                size = preset_params["size"]

    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if isinstance(loading, str):
        loading = loading.lower() not in ("false", "0", "")

    classes = f"btn btn-{conditional_escape(variant)}"
    if size and size != "md":
        classes += f" btn-{conditional_escape(size)}"
    if loading:
        classes += " btn-loading"

    attrs = f'class="{classes}"'
    if event:
        attrs += f' dj-click="{conditional_escape(event)}"'
    if disabled or loading:
        attrs += " disabled"

    spinner_html = '<span class="btn-spinner"></span>' if loading else ""
    icon_html = f'<span class="btn-icon">{conditional_escape(icon)}</span> ' if icon else ""

    return mark_safe(
        f"<button {attrs}>"
        f"{spinner_html}"
        f"{icon_html}"
        f'<span class="btn-label">{conditional_escape(label)}</span>'
        f"</button>"
    )


# ---------------------------------------------------------------------------
# 15. Input field
# ---------------------------------------------------------------------------


@register.simple_tag
def dj_input(
    name="",
    label="",
    value="",
    placeholder="",
    input_type="text",
    error="",
    helper="",
    required=False,
    disabled=False,
    event="",
):
    """Render a labelled text input inside a form-group wrapper."""
    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_value = conditional_escape(value)
    e_placeholder = conditional_escape(placeholder)
    e_type = conditional_escape(input_type)
    e_error = conditional_escape(error)
    e_helper = conditional_escape(helper)
    dj_event = conditional_escape(event or name)

    required_attr = " required" if required else ""
    disabled_attr = " disabled" if disabled else ""
    error_cls = " form-input-error" if error else ""
    required_span = '<span class="form-required"> *</span>' if required else ""

    label_html = (
        f'<label class="form-label" for="{e_name}">{e_label}{required_span}</label>'
        if label
        else ""
    )
    error_html = f'<span class="form-error-message">{e_error}</span>' if error else ""
    helper_html = f'<span class="form-helper">{e_helper}</span>' if helper else ""

    return mark_safe(
        f'<div class="form-group">'
        f"{label_html}"
        f'<input class="form-input{error_cls}" type="{e_type}" '
        f'name="{e_name}" id="{e_name}" value="{e_value}" '
        f'placeholder="{e_placeholder}" '
        f'dj-input="{dj_event}"{required_attr}{disabled_attr}>'
        f"{error_html}"
        f"{helper_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 16. Select field
# ---------------------------------------------------------------------------


@register.simple_tag
def dj_select(
    name="",
    label="",
    value="",
    options=None,
    error="",
    helper="",
    required=False,
    disabled=False,
    event="",
):
    """Render a labelled <select> inside a form-group wrapper.

    Args:
        options: list of dicts {"value":..., "label":...} or list of 2-tuples
    """
    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if options is None:
        options = []

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_error = conditional_escape(error)
    e_helper = conditional_escape(helper)
    dj_event = conditional_escape(event or name)

    required_attr = " required" if required else ""
    disabled_attr = " disabled" if disabled else ""
    error_cls = " form-select-error" if error else ""

    # Normalise options to list of (val, lbl)
    def _opt_pair(opt):
        if isinstance(opt, dict):
            return str(opt.get("value", "")), str(opt.get("label", ""))
        if isinstance(opt, (list, tuple)) and len(opt) >= 2:
            return str(opt[0]), str(opt[1])
        return str(opt), str(opt)

    options_html_parts = []
    for opt in options:
        ov, ol = _opt_pair(opt)
        selected_attr = " selected" if str(ov) == str(value) else ""
        options_html_parts.append(
            f'<option value="{conditional_escape(ov)}"{selected_attr}>'
            f"{conditional_escape(ol)}</option>"
        )

    required_span = '<span class="form-required"> *</span>' if required else ""
    label_html = (
        f'<label class="form-label" for="{e_name}">{e_label}{required_span}</label>'
        if label
        else ""
    )
    error_html = f'<span class="form-error-message">{e_error}</span>' if error else ""
    helper_html = f'<span class="form-helper">{e_helper}</span>' if helper else ""

    return mark_safe(
        f'<div class="form-group">'
        f"{label_html}"
        f'<select class="form-select{error_cls}" name="{e_name}" id="{e_name}" '
        f'dj-change="{dj_event}"{required_attr}{disabled_attr}>'
        f"{''.join(options_html_parts)}"
        f"</select>"
        f"{error_html}"
        f"{helper_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 17. Checkbox
# ---------------------------------------------------------------------------


@register.simple_tag
def dj_checkbox(name="", label="", checked=False, value="on", event="", disabled=False):
    """Render a single checkbox input."""
    if isinstance(checked, str):
        checked = checked.lower() not in ("false", "0", "")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_value = conditional_escape(value)
    dj_event = conditional_escape(event or name)

    checked_attr = " checked" if checked else ""
    disabled_attr = " disabled" if disabled else ""

    return mark_safe(
        f'<div class="form-checkbox-wrapper">'
        f'<input class="form-checkbox" type="checkbox" '
        f'name="{e_name}" id="{e_name}" value="{e_value}" '
        f'dj-change="{dj_event}"{checked_attr}{disabled_attr}>'
        f'<label class="form-checkbox-label" for="{e_name}">{e_label}</label>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 18. Radio
# ---------------------------------------------------------------------------


@register.simple_tag
def dj_radio(name="", label="", value="", current_value="", event="", disabled=False):
    """Render a single radio button."""
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_value = conditional_escape(value)
    radio_id = conditional_escape(f"{name}_{value}")
    dj_event = conditional_escape(event or name)

    checked_attr = " checked" if str(value) == str(current_value) else ""
    disabled_attr = " disabled" if disabled else ""

    return mark_safe(
        f'<div class="form-radio-wrapper">'
        f'<input class="form-radio" type="radio" '
        f'name="{e_name}" id="{radio_id}" value="{e_value}" '
        f'dj-change="{dj_event}"{checked_attr}{disabled_attr}>'
        f'<label class="form-radio-label" for="{radio_id}">{e_label}</label>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 19. Textarea
# ---------------------------------------------------------------------------


@register.simple_tag
def dj_textarea(
    name="",
    label="",
    value="",
    placeholder="",
    rows=4,
    error="",
    helper="",
    required=False,
    disabled=False,
    event="",
):
    """Render a labelled <textarea> inside a form-group wrapper."""
    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    try:
        rows = int(rows)
    except (ValueError, TypeError):
        rows = 4

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_value = conditional_escape(value)
    e_placeholder = conditional_escape(placeholder)
    e_error = conditional_escape(error)
    e_helper = conditional_escape(helper)
    dj_event = conditional_escape(event or name)

    required_attr = " required" if required else ""
    disabled_attr = " disabled" if disabled else ""
    error_cls = " form-input-error" if error else ""
    required_span = '<span class="form-required"> *</span>' if required else ""

    label_html = (
        f'<label class="form-label" for="{e_name}">{e_label}{required_span}</label>'
        if label
        else ""
    )
    error_html = f'<span class="form-error-message">{e_error}</span>' if error else ""
    helper_html = f'<span class="form-helper">{e_helper}</span>' if helper else ""

    return mark_safe(
        f'<div class="form-group">'
        f"{label_html}"
        f'<textarea class="form-input{error_cls}" name="{e_name}" id="{e_name}" '
        f'rows="{rows}" placeholder="{e_placeholder}" '
        f'dj-input="{dj_event}"{required_attr}{disabled_attr}>'
        f"{e_value}</textarea>"
        f"{error_html}"
        f"{helper_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 20. Form Group (block tag)
# ---------------------------------------------------------------------------


class FormGroupNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        label = kw.get("label", "")
        error = kw.get("error", "")
        helper = kw.get("helper", "")
        required = kw.get("required", False)
        if isinstance(required, str):
            required = required.lower() not in ("false", "0", "")
        for_input = kw.get("for_input", "")

        content = self.nodelist.render(context)

        for_attr = f' for="{conditional_escape(for_input)}"' if for_input else ""
        required_html = '<span class="form-required"> *</span>' if required else ""
        label_html = (
            f'<label class="form-label"{for_attr}>{conditional_escape(label)}{required_html}</label>'
            if label
            else ""
        )
        error_html = (
            f'<span class="form-error-message">{conditional_escape(error)}</span>' if error else ""
        )
        helper_html = (
            f'<span class="form-helper">{conditional_escape(helper)}</span>' if helper else ""
        )

        return mark_safe(
            f'<div class="form-group">{label_html}{content}{error_html}{helper_html}</div>'
        )


@register.tag("form_group")
def do_form_group(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endform_group",))
    parser.delete_first_token()
    return FormGroupNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# 21. Spinner
# ---------------------------------------------------------------------------


@register.simple_tag
def spinner(size="md", color="primary"):
    """Render an animated spinner."""
    e_size = conditional_escape(size)
    e_color = conditional_escape(color)
    return mark_safe(
        f'<span class="spinner spinner-{e_size} spinner-{e_color}" '
        f'aria-label="Loading" role="status"></span>'
    )


# ---------------------------------------------------------------------------
# 22. Skeleton
# ---------------------------------------------------------------------------


@register.simple_tag
def skeleton(skeleton_type="text", lines=3):
    """Render skeleton loading placeholder.

    Args:
        skeleton_type: text, card, avatar, table
        lines: number of lines for text/table type
    """
    try:
        lines = int(lines)
    except (ValueError, TypeError):
        lines = 3

    if skeleton_type == "avatar":
        return mark_safe('<div class="skeleton-avatar"></div>')

    if skeleton_type == "card":
        inner_lines = "".join('<div class="skeleton-line"></div>' for _ in range(max(1, lines)))
        return mark_safe(
            f'<div class="skeleton-card">'
            f'<div class="skeleton-card-header"></div>'
            f'<div class="skeleton-card-body">{inner_lines}</div>'
            f"</div>"
        )

    if skeleton_type == "table":
        rows = "".join('<div class="skeleton-line"></div>' for _ in range(max(1, lines)))
        return mark_safe(
            f'<div class="skeleton-table">'
            f'<div class="skeleton-line skeleton-line-header"></div>'
            f"{rows}"
            f"</div>"
        )

    # Default: text lines
    line_html = "".join('<div class="skeleton-line"></div>' for _ in range(max(1, lines)))
    return mark_safe(f'<div class="skeleton-text">{line_html}</div>')


# ---------------------------------------------------------------------------
# 23. Breadcrumb
# ---------------------------------------------------------------------------


@register.simple_tag
def breadcrumb(items=None):
    """Render breadcrumb navigation.

    Args:
        items: list of dicts {"label":..., "url":..., "active": False}
    """
    if not items:
        return mark_safe('<nav class="breadcrumb"></nav>')

    parts = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            lbl = item.get("label", "")
            url = item.get("url", "")
            active = item.get("active", False)
        else:
            lbl, url, active = str(item), "", False

        e_lbl = conditional_escape(lbl)
        e_url = conditional_escape(url)

        if active or not url:
            crumb = f'<span class="breadcrumb-item breadcrumb-active">{e_lbl}</span>'
        else:
            crumb = f'<a class="breadcrumb-item breadcrumb-link" href="{e_url}">{e_lbl}</a>'

        parts.append(crumb)
        if i < len(items) - 1:
            parts.append('<span class="breadcrumb-separator">&#8250;</span>')

    return mark_safe(f'<nav class="breadcrumb">{"".join(parts)}</nav>')


# ---------------------------------------------------------------------------
# 24. Empty State
# ---------------------------------------------------------------------------


@register.simple_tag
def empty_state(title="", description="", icon="", action_label="", action_event=""):
    """Render an empty-state placeholder with optional CTA."""
    e_title = conditional_escape(title)
    e_description = conditional_escape(description)
    e_icon = conditional_escape(icon)
    e_action_label = conditional_escape(action_label)
    e_action_event = conditional_escape(action_event)

    icon_html = f'<div class="empty-state-icon">{e_icon}</div>' if icon else ""
    title_html = f'<h3 class="empty-state-title">{e_title}</h3>' if title else ""
    desc_html = f'<p class="empty-state-description">{e_description}</p>' if description else ""
    action_html = ""
    if action_label:
        action_html = (
            f'<button class="btn btn-primary empty-state-action" '
            f'dj-click="{e_action_event}">{e_action_label}</button>'
        )

    return mark_safe(
        f'<div class="empty-state">{icon_html}{title_html}{desc_html}{action_html}</div>'
    )


# ---------------------------------------------------------------------------
# 25. Divider
# ---------------------------------------------------------------------------


@register.simple_tag
def dj_divider(label="", vertical=False):
    """Render a horizontal or vertical divider, optionally with a label."""
    if isinstance(vertical, str):
        vertical = vertical.lower() not in ("false", "0", "")

    orientation_cls = "divider-vertical" if vertical else "divider-horizontal"

    if label:
        e_label = conditional_escape(label)
        return mark_safe(f'<div class="divider-label"><span>{e_label}</span></div>')

    return mark_safe(f'<hr class="divider {orientation_cls}">')


# ---------------------------------------------------------------------------
# 26. Switch / Toggle
# ---------------------------------------------------------------------------


@register.simple_tag
def switch(name="", checked=False, label="", event="toggle", size="md", disabled=False):
    """Render an accessible switch/toggle."""
    if isinstance(checked, str):
        checked = checked.lower() not in ("false", "0", "")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_event = conditional_escape(event)
    e_size = conditional_escape(size)

    checked_attr = " checked" if checked else ""
    disabled_attr = " disabled" if disabled else ""
    switch_id = e_name

    label_html = f'<span class="switch-label">{e_label}</span>' if label else ""

    return mark_safe(
        f'<label class="switch-wrapper switch-{e_size}">'
        f'<span class="switch">'
        f'<input type="checkbox" name="{e_name}" id="{switch_id}" '
        f'class="switch-input" dj-change="{e_event}"{checked_attr}{disabled_attr}>'
        f'<span class="switch-track"></span>'
        f'<span class="switch-thumb"></span>'
        f"</span>"
        f"{label_html}"
        f"</label>"
    )


# ---------------------------------------------------------------------------
# 27. Stat Card
# ---------------------------------------------------------------------------


@register.simple_tag
def stat_card(label="", value="", trend="", description="", trend_direction=""):
    """Render a metric/stat card."""
    e_label = conditional_escape(label)
    e_value = conditional_escape(value)
    e_trend = conditional_escape(trend)
    e_description = conditional_escape(description)
    e_dir = conditional_escape(trend_direction)

    trend_html = ""
    if trend:
        dir_cls = f" trend-{e_dir}" if trend_direction else ""
        arrow = {"up": "&#8593;", "down": "&#8595;", "flat": "&#8212;"}.get(trend_direction, "")
        trend_html = f'<span class="stat-trend{dir_cls}">{arrow} {e_trend}</span>'

    desc_html = f'<p class="stat-description">{e_description}</p>' if description else ""

    return mark_safe(
        f'<div class="stat-card">'
        f'<div class="stat-label">{e_label}</div>'
        f'<div class="stat-value">{e_value}</div>'
        f"{trend_html}"
        f"{desc_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 28. Tag / Chip
# ---------------------------------------------------------------------------


@register.simple_tag
def dj_tag(label="", variant="default", dismissible=False, event="dismiss_tag", size=""):
    """Render a tag/chip element."""
    if isinstance(dismissible, str):
        dismissible = dismissible.lower() not in ("false", "0", "")

    e_label = conditional_escape(label)
    e_variant = conditional_escape(variant)
    e_event = conditional_escape(event)
    size_cls = f" tag-{conditional_escape(size)}" if size else ""

    close_html = (
        f'<button class="tag-close" dj-click="{e_event}">&times;</button>' if dismissible else ""
    )

    return mark_safe(f'<span class="tag tag-{e_variant}{size_cls}">{e_label}{close_html}</span>')


# ---------------------------------------------------------------------------
# 29. Timeline (block tag)
# ---------------------------------------------------------------------------


class TimelineNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        content = self.nodelist.render(context)
        return mark_safe(f'<div class="timeline">{content}</div>')


@register.tag("timeline")
def do_timeline(parser, token):
    nodelist = parser.parse(("endtimeline",))
    parser.delete_first_token()
    return TimelineNode(nodelist)


# ---------------------------------------------------------------------------
# 30. Timeline Item (block tag)
# ---------------------------------------------------------------------------


class TimelineItemNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        title = kw.get("title", "")
        time = kw.get("time", "")

        content = self.nodelist.render(context)

        title_html = (
            f'<div class="timeline-title">{conditional_escape(title)}</div>' if title else ""
        )
        time_html = f'<div class="timeline-time">{conditional_escape(time)}</div>' if time else ""

        return mark_safe(
            f'<div class="timeline-item">'
            f'<div class="timeline-marker"></div>'
            f'<div class="timeline-content">'
            f"{title_html}"
            f"{time_html}"
            f"{content}"
            f"</div>"
            f"</div>"
        )


@register.tag("timeline_item")
def do_timeline_item(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endtimeline_item",))
    parser.delete_first_token()
    return TimelineItemNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# 31. Stepper
# ---------------------------------------------------------------------------


@register.simple_tag
def stepper(steps=None, active=0, event="set_step"):
    """Render a step indicator.

    Args:
        steps: list of dicts {"label":..., "complete": False} or list of strings
        active: 0-based index of the current step
        event: dj-click event name for step navigation
    """
    if not steps:
        return mark_safe('<div class="stepper"></div>')

    try:
        active = int(active)
    except (ValueError, TypeError):
        active = 0

    e_event = conditional_escape(event)
    parts = []
    for i, step in enumerate(steps):
        if isinstance(step, dict):
            lbl = step.get("label", "")
            complete = step.get("complete", False)
        else:
            lbl = str(step)
            complete = False

        cls = "stepper-step"
        if i == active:
            cls += " stepper-step-active"
        if complete:
            cls += " stepper-step-complete"

        parts.append(
            f'<button class="{cls}" dj-click="{e_event}" data-value="{i}">'
            f'<span class="stepper-number">{i + 1}</span>'
            f'<span class="stepper-label">{conditional_escape(lbl)}</span>'
            f"</button>"
        )

    return mark_safe(f'<div class="stepper">{"".join(parts)}</div>')


# ===========================================================================
# TIER 2 REMAINING + TIER 3 COMPONENTS
# ===========================================================================

# ---------------------------------------------------------------------------
# 32. Code Block
# ---------------------------------------------------------------------------


@register.simple_tag
def code_block(
    code="", language="", filename="", copy_event="copy_code", highlight=True, theme="github-dark"
):
    """Render a syntax-highlighted code block with optional copy button.

    Args:
        highlight: When True (default), lazy-loads highlight.js from CDN.
        theme: highlight.js theme name (default "github-dark").
    """
    if isinstance(highlight, str):
        highlight = highlight.lower() not in ("false", "0", "")

    e_language = conditional_escape(language or "text")
    e_filename = conditional_escape(filename)
    e_code = conditional_escape(code)
    # Theme is interpolated inside a <script> — HTML escaping is insufficient.
    # Restrict to alphanumeric, hyphens, and underscores to prevent injection.
    safe_theme = theme if _re.match(r"^[a-zA-Z0-9_-]+$", str(theme)) else "github-dark"
    e_theme = conditional_escape(safe_theme)

    filename_html = f'<span class="code-block-filename">{e_filename}</span>' if filename else ""
    lang_html = f'<span class="code-block-lang">{e_language}</span>'
    copy_html = (
        '<button class="code-block-copy" '
        "onclick=\"(function(btn){var pre=btn.closest('.code-block').querySelector('code');"
        "navigator.clipboard&&navigator.clipboard.writeText(pre.textContent).then(function(){"
        "btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy';},2000);});})(this)\">"
        "Copy</button>"
    )

    highlight_html = ""
    if highlight:
        highlight_html = (
            f"<script>"
            f"(function(){{"
            f'var el=document.currentScript.previousElementSibling.querySelector("code");'
            f"if(el.dataset.highlighted)return;"
            f'function doHL(){{if(window.hljs){{hljs.highlightElement(el);el.dataset.highlighted="true";}}}}'
            f"if(window.hljs){{doHL();return;}}"
            f"if(!window.__djcHljsLoading){{"
            f"window.__djcHljsLoading=true;"
            f'var lnk=document.createElement("link");lnk.rel="stylesheet";'
            f'lnk.href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/{e_theme}.min.css";'
            f"document.head.appendChild(lnk);"
            f'var s=document.createElement("script");'
            f's.src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/highlight.min.js";'
            f's.onload=function(){{document.querySelectorAll("pre code[class^=language-]").forEach(function(b)'
            f'{{if(!b.dataset.highlighted){{hljs.highlightElement(b);b.dataset.highlighted="true";}}}});}};'
            f"document.head.appendChild(s);"
            f"}}else{{var iv=setInterval(function(){{if(window.hljs){{clearInterval(iv);doHL();}}}},50);}}"
            f"}})();"
            f"</script>"
        )

    return mark_safe(
        f'<div class="code-block" data-highlight="{e_theme if highlight else ""}">'
        f'<div class="code-block-header">'
        f"{filename_html}{lang_html}{copy_html}"
        f"</div>"
        f'<pre class="code-block-pre"><code class="language-{e_language}">{e_code}</code></pre>'
        f"{highlight_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 33. Combobox (searchable select with server-side filtering)
# ---------------------------------------------------------------------------


@register.simple_tag
def combobox(
    name="",
    label="",
    value="",
    placeholder="Search…",
    options=None,
    event="",
    search_event="",
    required=False,
    error="",
    helper="",
    multiple=False,
    selected=None,
):
    """Render a combobox (searchable select).

    Args:
        options: list of dicts {"value":..., "label":...}
        event: dj-change event when option selected
        search_event: dj-input event for search input (server filters options)
        multiple: when True, enables multi-select with tags
        selected: list of selected values for multi-select mode
    """
    if options is None:
        options = []
    if selected is None:
        selected = []
    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")
    if isinstance(multiple, str):
        multiple = multiple.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_value = conditional_escape(value)
    e_placeholder = conditional_escape(placeholder)
    e_event = conditional_escape(event or name)
    e_search = conditional_escape(search_event or (name + "_search"))
    e_error = conditional_escape(error)
    e_helper = conditional_escape(helper)

    # Build a lookup for option labels
    opt_label_map = {}
    for opt in options:
        if isinstance(opt, dict):
            opt_label_map[str(opt.get("value", ""))] = str(opt.get("label", ""))
        else:
            opt_label_map[str(opt)] = str(opt)

    selected_set = set(str(s) for s in selected) if multiple else set()

    if multiple:
        # Multi-select mode
        # Tags for selected values
        tags_html = ""
        hidden_inputs_html = ""
        for sv in selected:
            e_sv = conditional_escape(str(sv))
            sl = opt_label_map.get(str(sv), str(sv))
            e_sl = conditional_escape(sl)
            tags_html += (
                f'<span class="combobox-tag">'
                f'<span class="combobox-tag-label">{e_sl}</span>'
                f'<button class="combobox-tag-remove" dj-click="{e_event}" '
                f'data-value="{e_sv}" type="button">&times;</button>'
                f"</span>"
            )
            hidden_inputs_html += f'<input type="hidden" name="{e_name}[]" value="{e_sv}">'

        tags_container = f'<div class="combobox-tags">{tags_html}</div>' if selected else ""

        # Options with selected state
        options_html = ""
        for opt in options:
            if isinstance(opt, dict):
                ov = conditional_escape(str(opt.get("value", "")))
                ol = conditional_escape(str(opt.get("label", "")))
            else:
                ov = ol = conditional_escape(str(opt))
            sel_cls = " combobox-option-selected" if str(ov) in selected_set else ""
            options_html += (
                f'<div class="combobox-option{sel_cls}" '
                f'dj-click="{e_event}" data-value="{ov}">{ol}</div>'
            )
    else:
        # Single-select mode (existing behavior)
        tags_container = ""
        hidden_inputs_html = ""

        # Find current label
        current_label = e_value
        for opt in options:
            if isinstance(opt, dict) and str(opt.get("value", "")) == str(value):
                current_label = conditional_escape(str(opt.get("label", value)))
                break

        options_html = ""
        for opt in options:
            if isinstance(opt, dict):
                ov = conditional_escape(str(opt.get("value", "")))
                ol = conditional_escape(str(opt.get("label", "")))
            else:
                ov = ol = conditional_escape(str(opt))
            sel = ' class="combobox-option-selected"' if str(ov) == str(value) else ""
            options_html += (
                f'<div class="combobox-option"{sel} '
                f'dj-click="{e_event}" data-value="{ov}">{ol}</div>'
            )

    label_html = (
        f'<label class="form-label" for="{e_name}-input">{e_label}</label>' if label else ""
    )
    error_html = f'<span class="form-error-message">{e_error}</span>' if error else ""
    helper_html = f'<span class="form-helper">{e_helper}</span>' if helper else ""
    req = " required" if required else ""

    if not multiple:
        current_label = e_value
        for opt in options:
            if isinstance(opt, dict) and str(opt.get("value", "")) == str(value):
                current_label = conditional_escape(str(opt.get("label", value)))
                break
        input_value = current_label
    else:
        input_value = e_value

    return mark_safe(
        f'<div class="form-group">'
        f"{label_html}"
        f'<div class="combobox" id="{e_name}-combobox">'
        f"{tags_container}"
        f"{hidden_inputs_html}"
        f'<input class="combobox-input form-input" type="text" id="{e_name}-input" '
        f'name="{e_name}" placeholder="{e_placeholder}" value="{input_value}" '
        f'dj-input="{e_search}" autocomplete="off"{req}>'
        f'<div class="combobox-dropdown" onmousedown="event.preventDefault()" onclick="this.previousElementSibling.blur()">{options_html}</div>'
        f"</div>"
        f"{error_html}{helper_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 34. Popover
# ---------------------------------------------------------------------------


@register.tag("popover")
def do_popover(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endpopover",))
    parser.delete_first_token()
    return PopoverNode(nodelist, kwargs)


class PopoverNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        trigger = kw.get("trigger", "Click me")
        placement = kw.get("placement", "bottom")
        title = kw.get("title", "")
        uid = kw.get("id", f"pop-{uuid.uuid4().hex[:6]}")

        e_uid = conditional_escape(uid)
        e_trigger = conditional_escape(trigger)
        e_placement = conditional_escape(placement)

        content = self.nodelist.render(context)
        title_html = (
            f'<div class="popover-title">{conditional_escape(title)}</div>' if title else ""
        )

        js = (
            "(function(el){var p=el.parentElement;"
            "if(p.hasAttribute('data-open')){p.removeAttribute('data-open');}else{p.setAttribute('data-open','');"
            "document.addEventListener('click',function h(e){"
            "if(!p.contains(e.target)){p.removeAttribute('data-open');"
            "document.removeEventListener('click',h);}},true);};})(this)"
        )
        return mark_safe(
            f'<div class="popover-wrapper" id="{e_uid}">'
            f'<button class="popover-trigger btn btn-outline btn-sm" '
            f'onclick="{js}" aria-expanded="false">'
            f"{e_trigger}</button>"
            f'<div class="popover popover-{e_placement}" role="tooltip">'
            f"{title_html}"
            f'<div class="popover-content">{content}</div>'
            f"</div>"
            f"</div>"
        )


# ---------------------------------------------------------------------------
# 35. Rating / Stars
# ---------------------------------------------------------------------------


@register.simple_tag
def rating(value=0, max_stars=5, readonly=False, event="set_rating", size="md"):
    """Render a star rating component."""
    try:
        value = float(value)
        max_stars = int(max_stars)
    except (ValueError, TypeError):
        value = 0
        max_stars = 5
    if isinstance(readonly, str):
        readonly = readonly.lower() not in ("false", "0", "")

    e_event = conditional_escape(event)
    size_cls = f" rating-{conditional_escape(size)}" if size != "md" else ""
    parts = []

    for i in range(1, max_stars + 1):
        if i <= value:
            star_cls = "rating-star rating-star-full"
        elif i - 0.5 <= value:
            star_cls = "rating-star rating-star-half"
        else:
            star_cls = "rating-star rating-star-empty"

        if readonly:
            parts.append(f'<span class="{star_cls}">★</span>')
        else:
            parts.append(
                f'<button class="{star_cls}" dj-click="{e_event}" '
                f'data-value="{i}" title="{i} star{"s" if i > 1 else ""}">★</button>'
            )

    return mark_safe(f'<div class="rating{size_cls}">{"".join(parts)}</div>')


# ---------------------------------------------------------------------------
# 36. Copy Button
# ---------------------------------------------------------------------------


@register.simple_tag
def copy_button(text="", label="Copy", copied_label="Copied!", variant="outline", size="sm"):
    """Render a copy-to-clipboard button."""
    e_text = conditional_escape(text)
    e_label = conditional_escape(label)
    e_copied = conditional_escape(copied_label)
    e_variant = conditional_escape(variant)
    e_size = conditional_escape(size)

    return mark_safe(
        f'<button class="btn btn-{e_variant} btn-{e_size} copy-btn" '
        f'data-copy-text="{e_text}" '
        f'data-copied-label="{e_copied}" '
        f"onclick=\"(function(btn){{var t=btn.getAttribute('data-copy-text');"
        f"navigator.clipboard&&navigator.clipboard.writeText(t).then(function(){{"
        f"var orig=btn.textContent;btn.textContent=btn.getAttribute('data-copied-label');"
        f'setTimeout(function(){{btn.textContent=orig;}},2000);}});}})(this)">'
        f"{e_label}</button>"
    )


# ---------------------------------------------------------------------------
# 37. Kbd / Keyboard Shortcut
# ---------------------------------------------------------------------------


@register.simple_tag
def kbd(*keys):
    """Render keyboard shortcut keys.

    Usage: {% kbd "Ctrl" "K" %} → <kbd>Ctrl</kbd>+<kbd>K</kbd>
    """
    if not keys:
        return mark_safe("")
    parts = [f'<kbd class="kbd">{conditional_escape(k)}</kbd>' for k in keys]
    return mark_safe(
        '<span class="kbd-group">' + '<span class="kbd-sep">+</span>'.join(parts) + "</span>"
    )


# ---------------------------------------------------------------------------
# 38. Collapsible
# ---------------------------------------------------------------------------


@register.tag("collapsible")
def do_collapsible(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endcollapsible",))
    parser.delete_first_token()
    return CollapsibleNode(nodelist, kwargs)


class CollapsibleNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        trigger = kw.get("trigger", "Toggle")
        open_ = kw.get("open", False)
        if isinstance(open_, str):
            open_ = open_.lower() not in ("false", "0", "")
        event = kw.get("event", "toggle_collapsible")
        uid = f"coll-{uuid.uuid4().hex[:6]}"

        e_uid = conditional_escape(uid)
        e_trigger = conditional_escape(trigger)
        e_event = conditional_escape(event)
        open_cls = " collapsible-open" if open_ else ""
        content = self.nodelist.render(context)
        component_id = kw.get("component_id", "")
        cid_attr = (
            f' data-component-id="{conditional_escape(component_id)}"' if component_id else ""
        )

        return mark_safe(
            f'<div class="collapsible{open_cls}" id="{e_uid}">'
            f'<button class="collapsible-trigger" '
            f"onclick=\"(function(el){{el.closest('.collapsible').classList.toggle('collapsible-open');}})(this)\""
            f' dj-click="{e_event}"{cid_attr}>'
            f'<span class="collapsible-label">{e_trigger}</span>'
            f'<span class="collapsible-icon">▾</span>'
            f"</button>"
            f'<div class="collapsible-content">{content}</div>'
            f"</div>"
        )


# ---------------------------------------------------------------------------
# 39. Sheet / Drawer
# ---------------------------------------------------------------------------


@register.tag("sheet")
def do_sheet(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endsheet",))
    parser.delete_first_token()
    return SheetNode(nodelist, kwargs)


class SheetNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        open_ = kw.get("is_open", kw.get("open", False))
        if isinstance(open_, str):
            open_ = open_.lower() not in ("false", "0", "")
        side = kw.get("side", "right")
        title = kw.get("title", "")
        close_event = kw.get("close_event", "close_sheet")

        e_side = conditional_escape(side)
        e_title = conditional_escape(title)
        e_close = conditional_escape(close_event)
        open_attr = ' data-open="true"' if open_ else ""
        content = self.nodelist.render(context)
        component_id = kw.get("component_id", "")
        cid_attr = (
            f' data-component-id="{conditional_escape(component_id)}"' if component_id else ""
        )

        title_html = (
            f'<div class="sheet-header">'
            f'<h3 class="sheet-title">{e_title}</h3>'
            f'<button class="sheet-close" dj-click="{e_close}"{cid_attr}>&times;</button>'
            f"</div>"
            if title
            else f'<div class="sheet-header-close">'
            f'<button class="sheet-close" dj-click="{e_close}"{cid_attr}>&times;</button>'
            f"</div>"
        )

        return mark_safe(
            f'<div class="sheet-overlay" dj-click="{e_close}"{cid_attr}{open_attr}></div>'
            f'<div class="sheet sheet-{e_side}"{open_attr}>'
            f"{title_html}"
            f'<div class="sheet-body">{content}</div>'
            f"</div>"
        )


# ---------------------------------------------------------------------------
# 40. Notification Center
# ---------------------------------------------------------------------------


@register.simple_tag
def notification_center(
    notifications=None,
    unread_count=0,
    open_event="toggle_notifications",
    mark_read_event="mark_notification_read",
    clear_event="clear_notifications",
):
    """Render a notification bell with dropdown list."""
    if notifications is None:
        notifications = []
    try:
        unread_count = int(unread_count)
    except (ValueError, TypeError):
        unread_count = 0

    e_open = conditional_escape(open_event)
    e_clear = conditional_escape(clear_event)
    e_read = conditional_escape(mark_read_event)

    badge_html = f'<span class="notif-badge">{unread_count}</span>' if unread_count > 0 else ""

    items_html = ""
    for n in notifications:
        if not isinstance(n, dict):
            continue
        nid = conditional_escape(str(n.get("id", "")))
        msg = conditional_escape(str(n.get("message", n.get("msg", ""))))
        time_ = conditional_escape(str(n.get("time", "")))
        unread = n.get("unread", False)
        unread_cls = " notif-item-unread" if unread else ""
        time_html = f'<span class="notif-item-time">{time_}</span>' if time_ else ""
        items_html += (
            f'<div class="notif-item{unread_cls}" '
            f'dj-click="{e_read}" data-value="{nid}">'
            f'<div class="notif-item-msg">{msg}</div>'
            f"{time_html}"
            f"</div>"
        )

    if not items_html:
        items_html = '<div class="notif-empty">No notifications</div>'

    footer_html = (
        f'<div class="notif-footer">'
        f'<button class="btn btn-ghost btn-sm" dj-click="{e_clear}">Clear all</button>'
        f"</div>"
        if notifications
        else ""
    )

    return mark_safe(
        f'<div class="notif-center">'
        f'<button class="notif-trigger" dj-click="{e_open}">'
        f'<span class="notif-bell">&#128276;</span>'
        f"{badge_html}"
        f"</button>"
        f'<div class="notif-dropdown">'
        f'<div class="notif-header"><span class="notif-title">Notifications</span></div>'
        f'<div class="notif-list">{items_html}</div>'
        f"{footer_html}"
        f"</div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 41. Gauge / Donut Chart
# ---------------------------------------------------------------------------


@register.simple_tag
def gauge(value=0, max_value=100, label="", color="primary", size="md", show_value=True):
    """Render an SVG donut/gauge chart."""
    try:
        value = float(value)
        max_value = float(max_value) or 100
    except (ValueError, TypeError):
        value = 0
        max_value = 100
    if isinstance(show_value, str):
        show_value = show_value.lower() not in ("false", "0", "")

    pct = min(max(value / max_value, 0), 1)
    sizes = {"sm": 64, "md": 96, "lg": 128}
    px = sizes.get(str(size), 96)
    r = (px - 12) / 2
    circ = 2 * 3.14159 * r
    dash = pct * circ
    gap = circ - dash
    cx = cy = px / 2

    e_color = conditional_escape(color)
    e_label = conditional_escape(label)
    display_val = f"{int(pct * 100)}%"
    val_html = (
        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" '
        f'class="gauge-value-text" font-size="{px * 0.18:.0f}">{display_val}</text>'
        if show_value
        else ""
    )
    label_html = f'<div class="gauge-label">{e_label}</div>' if e_label else ""

    return mark_safe(
        f'<div class="gauge gauge-{e_color}" style="width:{px}px;height:{px}px;">'
        f'<svg width="{px}" height="{px}" viewBox="0 0 {px} {px}">'
        f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" class="gauge-track" '
        f'stroke-width="8" fill="none"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" class="gauge-fill gauge-fill-{e_color}" '
        f'stroke-width="8" fill="none" '
        f'stroke-dasharray="{dash:.1f} {gap:.1f}" '
        f'stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>'
        f"{val_html}"
        f"</svg>"
        f"{label_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 42. Image Carousel
# ---------------------------------------------------------------------------


@register.simple_tag
def carousel(
    images=None,
    active=0,
    prev_event="carousel_prev",
    next_event="carousel_next",
    go_event="carousel_go",
    component_id="",
):
    """Render an image carousel / slideshow."""
    if images is None:
        images = []
    try:
        active = int(active)
    except (ValueError, TypeError):
        active = 0

    if not images:
        return mark_safe('<div class="carousel carousel-empty"></div>')

    e_prev = conditional_escape(prev_event)
    e_next = conditional_escape(next_event)
    e_go = conditional_escape(go_event)
    cid_attr = f' data-component-id="{conditional_escape(component_id)}"' if component_id else ""

    slides = ""
    dots = ""
    for i, img in enumerate(images):
        if isinstance(img, dict):
            src = conditional_escape(str(img.get("src", img.get("url", ""))))
            alt = conditional_escape(str(img.get("alt", f"Slide {i + 1}")))
            caption = img.get("caption", "")
        else:
            src = conditional_escape(str(img))
            alt = f"Slide {i + 1}"
            caption = ""

        active_cls = " carousel-slide-active" if i == active else ""
        caption_html = (
            f'<div class="carousel-caption">{conditional_escape(caption)}</div>' if caption else ""
        )
        slides += (
            f'<div class="carousel-slide{active_cls}">'
            f'<img src="{src}" alt="{alt}" class="carousel-img">'
            f"{caption_html}"
            f"</div>"
        )
        dot_cls = " carousel-dot-active" if i == active else ""
        dots += (
            f'<button class="carousel-dot{dot_cls}" '
            f'dj-click="{e_go}" data-value="{i}"{cid_attr}></button>'
        )

    total = len(images)
    counter_html = (
        f'<div class="carousel-counter">{active + 1} / {total}</div>' if total > 1 else ""
    )

    return mark_safe(
        f'<div class="carousel">'
        f'<div class="carousel-track">{slides}</div>'
        f'<button class="carousel-btn carousel-btn-prev" dj-click="{e_prev}"{cid_attr}>&#8249;</button>'
        f'<button class="carousel-btn carousel-btn-next" dj-click="{e_next}"{cid_attr}>&#8250;</button>'
        f'<div class="carousel-dots">{dots}</div>'
        f"{counter_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 43. Tree View
# ---------------------------------------------------------------------------


@register.simple_tag
def tree_view(nodes=None, expand_event="tree_expand", select_event="tree_select", selected=""):
    """Render an expandable tree view.

    Args:
        nodes: list of dicts:
            {"id": "n1", "label": "Root", "expanded": True,
             "children": [{"id": "n1a", "label": "Child"}]}
        expand_event: dj-click event fired with node id when expanding
        select_event: dj-click event fired with node id when selected
        selected: currently selected node id
    """
    if nodes is None:
        return mark_safe('<div class="tree"></div>')

    e_expand = conditional_escape(expand_event)
    e_select = conditional_escape(select_event)

    def render_node(node, depth=0):
        if not isinstance(node, dict):
            return ""
        nid = conditional_escape(str(node.get("id", "")))
        label = conditional_escape(str(node.get("label", "")))
        children = node.get("children", [])
        expanded = node.get("expanded", False)
        has_children = bool(children)

        sel_cls = " tree-node-selected" if str(node.get("id", "")) == str(selected) else ""
        exp_cls = " tree-node-expanded" if expanded else ""
        has_cls = " tree-node-has-children" if has_children else " tree-node-leaf"
        indent = depth * 1.25

        toggle_icon = "▾" if expanded else "▸"
        toggle_html = (
            f'<button class="tree-toggle" dj-click="{e_expand}" data-value="{nid}">'
            f"{toggle_icon}</button>"
            if has_children
            else '<span class="tree-toggle-placeholder"></span>'
        )

        children_html = ""
        if has_children and expanded:
            children_html = (
                '<div class="tree-children">'
                + "".join(render_node(c, depth + 1) for c in children)
                + "</div>"
            )

        return (
            f'<div class="tree-node{sel_cls}{exp_cls}{has_cls}" '
            f'style="padding-left:{indent}rem">'
            f'<div class="tree-node-row">'
            f"{toggle_html}"
            f'<button class="tree-node-label" dj-click="{e_select}" data-value="{nid}">'
            f"{label}</button>"
            f"</div>"
            f"{children_html}"
            f"</div>"
        )

    html = "".join(render_node(n) for n in nodes)
    return mark_safe(f'<div class="tree">{html}</div>')


# ---------------------------------------------------------------------------
# 44. Color Picker (swatches + hex input)
# ---------------------------------------------------------------------------


@register.simple_tag
def color_picker(name="", value="#3B82F6", event="", label="", swatches=None):
    """Render a color picker with preset swatches and a hex input."""
    if swatches is None:
        swatches = [
            "#EF4444",
            "#F97316",
            "#EAB308",
            "#22C55E",
            "#3B82F6",
            "#8B5CF6",
            "#EC4899",
            "#6B7280",
        ]
    e_name = conditional_escape(name)
    e_value = conditional_escape(value)
    e_event = conditional_escape(event or name)
    e_label = conditional_escape(label)

    label_html = f'<label class="form-label">{e_label}</label>' if label else ""

    swatch_html = ""
    for sw in swatches:
        e_sw = conditional_escape(sw)
        active_cls = " color-swatch-active" if sw == value else ""
        swatch_html += (
            f'<button class="color-swatch{active_cls}" '
            f'style="background:{e_sw}" title="{e_sw}" '
            f'dj-click="{e_event}" data-value="{e_sw}"></button>'
        )

    return mark_safe(
        f'<div class="form-group">'
        f"{label_html}"
        f'<div class="color-picker">'
        f'<div class="color-preview" style="background:{e_value}"></div>'
        f'<div class="color-swatches">{swatch_html}</div>'
        f'<input class="color-hex-input form-input" type="text" '
        f'name="{e_name}" value="{e_value}" placeholder="#000000" '
        f'maxlength="7" dj-input="{e_event}">'
        f"</div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 45. Command Palette
# ---------------------------------------------------------------------------


@register.tag("command_palette")
def do_command_palette(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endcommand_palette",))
    parser.delete_first_token()
    return CommandPaletteNode(nodelist, kwargs)


class CommandPaletteNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        open_ = kw.get("is_open", kw.get("open", False))
        if isinstance(open_, str):
            open_ = open_.lower() not in ("false", "0", "")
        search_event = kw.get("search_event", "palette_search")
        close_event = kw.get("close_event", "close_palette")
        placeholder = kw.get("placeholder", "Search commands…")

        e_search = conditional_escape(search_event)
        e_close = conditional_escape(close_event)
        e_placeholder = conditional_escape(placeholder)
        open_attr = ' data-open="true"' if open_ else ""
        content = self.nodelist.render(context)

        return mark_safe(
            f'<div class="palette-overlay" dj-click="{e_close}"{open_attr}></div>'
            f'<div class="palette"{open_attr}>'
            f'<div class="palette-search">'
            f'<span class="palette-search-icon">⌕</span>'
            f'<input class="palette-input" type="text" placeholder="{e_placeholder}" '
            f'dj-input="{e_search}" autofocus>'
            f'<button class="palette-close" dj-click="{e_close}">Esc</button>'
            f"</div>"
            f'<div class="palette-results">{content}</div>'
            f"</div>"
        )


@register.simple_tag
def palette_item(label="", shortcut="", description="", event="", icon=""):
    """Render a single command palette result item."""
    e_label = conditional_escape(label)
    e_event = conditional_escape(event)
    e_desc = conditional_escape(description)
    e_icon = conditional_escape(icon)

    icon_html = f'<span class="palette-item-icon">{e_icon}</span>' if icon else ""
    shortcut_html = f'<kbd class="kbd">{conditional_escape(shortcut)}</kbd>' if shortcut else ""
    desc_html = f'<span class="palette-item-desc">{e_desc}</span>' if description else ""

    return mark_safe(
        f'<div class="palette-item" dj-click="{e_event}">'
        f"{icon_html}"
        f'<div class="palette-item-body">'
        f'<span class="palette-item-label">{e_label}</span>'
        f"{desc_html}"
        f"</div>"
        f"{shortcut_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 46. Context Menu
# ---------------------------------------------------------------------------


@register.tag("context_menu")
def do_context_menu(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endcontext_menu",))
    parser.delete_first_token()
    return ContextMenuNode(nodelist, kwargs)


class ContextMenuNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        label = kw.get("label", "Right-click area")
        uid = f"ctx-{uuid.uuid4().hex[:6]}"

        e_uid = conditional_escape(uid)
        e_label = conditional_escape(label)
        content = self.nodelist.render(context)

        return mark_safe(
            f'<div class="ctx-wrapper" id="{e_uid}" '
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


@register.simple_tag
def context_menu_item(label="", event="", icon="", danger=False, divider=False):
    """Render a context menu item."""
    if divider:
        return mark_safe('<div class="ctx-divider"></div>')

    e_label = conditional_escape(label)
    e_event = conditional_escape(event)
    e_icon = conditional_escape(icon)
    danger_cls = " ctx-item-danger" if danger else ""
    icon_html = f'<span class="ctx-item-icon">{e_icon}</span>' if icon else ""

    return mark_safe(
        f'<div class="ctx-item{danger_cls}" role="menuitem" dj-click="{e_event}">'
        f"{icon_html}{e_label}"
        f"</div>"
    )


# ===========================================================================
# TIER 3 REMAINING — v1.3 COMPONENTS
# ===========================================================================

# ---------------------------------------------------------------------------
# 47. Date Picker (server-rendered calendar)
# ---------------------------------------------------------------------------


@register.simple_tag
def date_picker(
    year=None,
    month=None,
    selected="",
    prev_event="date_prev_month",
    next_event="date_next_month",
    select_event="date_select",
    name="date",
    label="",
    required=False,
    error="",
    helper="",
    is_range=False,
    range_start="",
    range_end="",
    **kwargs,
):
    """Render a server-driven calendar date picker.

    The server owns year/month navigation state. On each prev/next click,
    the view re-renders the calendar for the new month.

    Args:
        is_range: when True, enables date range selection mode.
        range_start: start date of the range (YYYY-MM-DD).
        range_end: end date of the range (YYYY-MM-DD).

    Deprecated aliases (still accepted):
        range → is_range
    """
    # Backward-compat: accept deprecated 'range' kwarg
    if "range" in kwargs:
        is_range = kwargs.pop("range")

    if isinstance(is_range, str):
        is_range = is_range.lower() not in ("false", "0", "")

    try:
        today = datetime.date.today()
        year = int(year) if year else today.year
        month = int(month) if month else today.month
        today_str = today.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        today = datetime.date.today()
        year, month = today.year, today.month
        today_str = today.strftime("%Y-%m-%d")

    e_prev = conditional_escape(prev_event)
    e_next = conditional_escape(next_event)
    e_select = conditional_escape(select_event)
    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_selected = conditional_escape(selected)
    e_error = conditional_escape(error)
    e_helper = conditional_escape(helper)
    e_range_start = conditional_escape(range_start)
    e_range_end = conditional_escape(range_end)

    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")

    month_name = _calendar.month_name[month]
    weekdays = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

    # Build day grid
    cal = _calendar.monthcalendar(year, month)
    header_cells = "".join(f'<div class="dp-weekday">{d}</div>' for d in weekdays)

    day_cells = ""
    for week in cal:
        for day in week:
            if day == 0:
                day_cells += '<div class="dp-day dp-day-empty"></div>'
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                cls = "dp-day"
                if date_str == today_str:
                    cls += " dp-day-today"
                if is_range:
                    if range_start and date_str == range_start:
                        cls += " dp-day-range-start"
                    if range_end and date_str == range_end:
                        cls += " dp-day-range-end"
                    if range_start and range_end and range_start < date_str < range_end:
                        cls += " dp-day-in-range"
                else:
                    if date_str == selected:
                        cls += " dp-day-selected"
                day_cells += (
                    f'<button class="{cls}" dj-click="{e_select}" '
                    f'data-value="{date_str}">{day}</button>'
                )

    label_html = f'<label class="form-label">{e_label}</label>' if label else ""
    required_html = ' <span class="form-required">*</span>' if required else ""
    error_html = f'<span class="form-error-message">{e_error}</span>' if error else ""
    helper_html = f'<span class="form-helper">{e_helper}</span>' if helper else ""

    if is_range:
        if range_start and range_end:
            selected_html = (
                f'<div class="dp-selected-value">{e_range_start} &ndash; {e_range_end}</div>'
            )
        elif range_start:
            selected_html = f'<div class="dp-selected-value">{e_range_start} &ndash; ...</div>'
        else:
            selected_html = ""
        hidden_html = (
            f'<input type="hidden" name="{e_name}_start" value="{e_range_start}">'
            f'<input type="hidden" name="{e_name}_end" value="{e_range_end}">'
        )
    else:
        selected_html = f'<div class="dp-selected-value">{e_selected}</div>' if selected else ""
        hidden_html = f'<input type="hidden" name="{e_name}" value="{e_selected}">'

    return mark_safe(
        f'<div class="form-group">'
        f"{label_html}{required_html}"
        f'<div class="date-picker">'
        f'<div class="dp-header">'
        f'<button class="dp-nav-btn" dj-click="{e_prev}" title="Previous month">&#8249;</button>'
        f'<span class="dp-month-label">{month_name} {year}</span>'
        f'<button class="dp-nav-btn" dj-click="{e_next}" title="Next month">&#8250;</button>'
        f"</div>"
        f'<div class="dp-grid">'
        f"{header_cells}"
        f"{day_cells}"
        f"</div>"
        f"{hidden_html}"
        f"{selected_html}"
        f"</div>"
        f"{error_html}{helper_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 48. File Dropzone
# ---------------------------------------------------------------------------


@register.simple_tag
def file_dropzone(
    name="file",
    label="",
    accept="",
    multiple=False,
    max_size_mb=10,
    event="file_selected",
    helper="",
):
    """Render a drag-and-drop file upload zone."""
    if isinstance(multiple, str):
        multiple = multiple.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_accept = conditional_escape(accept)
    e_helper = conditional_escape(helper)
    e_max = conditional_escape(str(max_size_mb))

    multiple_attr = " multiple" if multiple else ""
    accept_attr = f' accept="{e_accept}"' if accept else ""
    label_html = f'<label class="form-label">{e_label}</label>' if label else ""
    helper_html = f'<span class="form-helper">{e_helper}</span>' if helper else ""

    js_id = f"dz-{name}"

    return mark_safe(
        f"{label_html}"
        f'<div class="dropzone" id="{js_id}" '
        f"ondragover=\"event.preventDefault();this.classList.add('dropzone-over')\" "
        f"ondragleave=\"this.classList.remove('dropzone-over')\" "
        f"ondrop=\"(function(e,el){{e.preventDefault();el.classList.remove('dropzone-over');"
        f"var f=e.dataTransfer.files;if(f.length){{var inp=el.querySelector('input[type=file]');"
        f"try{{var dt=new DataTransfer();for(var i=0;i<f.length;i++)dt.items.add(f[i]);"
        f"inp.files=dt.files;}}catch(ex){{}}el.querySelector('.dz-file-count').textContent="
        f"f.length+' file'+(f.length>1?'s':'')+' selected';"
        f"el.classList.add('dropzone-has-file');}}}})( event,this)\">"
        f'<input type="file" name="{e_name}" class="dropzone-input" '
        f"{accept_attr}{multiple_attr} "
        f"onchange=\"(function(el){{var f=el.files;var c=el.closest('.dropzone');"
        f"c.querySelector('.dz-file-count').textContent=f.length+' file'+(f.length>1?'s':'')+' selected';"
        f"c.classList.add('dropzone-has-file');}})( this)\">"
        f'<div class="dz-icon">&#128196;</div>'
        f'<div class="dz-text">Drag files here or <span class="dz-browse">browse</span></div>'
        f'<div class="dz-hint">Max {e_max} MB{(", accepts " + e_accept) if accept else ""}</div>'
        f'<div class="dz-file-count"></div>'
        f"</div>"
        f"{helper_html}"
    )


# ---------------------------------------------------------------------------
# 49. Split Pane
# ---------------------------------------------------------------------------


@register.tag("split_pane")
def do_split_pane(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    # parse two children: {% pane %}...{% endpane %}{% pane %}...{% endpane %}
    pane1 = parser.parse(("pane",))
    parser.delete_first_token()  # consume {% pane %}
    pane2 = parser.parse(("endsplit_pane",))
    parser.delete_first_token()  # consume {% endsplit_pane %}
    return SplitPaneNode(pane1, pane2, kwargs)


class SplitPaneNode(template.Node):
    def __init__(self, pane1, pane2, kwargs):
        self.pane1 = pane1
        self.pane2 = pane2
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        direction = kw.get("direction", "horizontal")
        initial = kw.get("initial", "50")
        uid = f"sp-{uuid.uuid4().hex[:6]}"

        e_uid = conditional_escape(uid)
        e_dir = conditional_escape(direction)
        e_init = conditional_escape(str(initial))

        content1 = self.pane1.render(context)
        content2 = self.pane2.render(context)

        size_prop = "width" if direction == "horizontal" else "height"

        js = (
            f"(function(){{var sp=document.getElementById('{uid}');"
            f"if(!sp)return;"
            f"var h=sp.querySelector('.sp-handle');"
            f"var p1=sp.querySelector('.sp-pane-1');"
            f"var dragging=false;"
            f"h.addEventListener('mousedown',function(e){{dragging=true;e.preventDefault();}});"
            f"document.addEventListener('mousemove',function(e){{"
            f"if(!dragging)return;"
            f"var r=sp.getBoundingClientRect();"
            f"var pct={'((e.clientX-r.left)/r.width*100)' if direction == 'horizontal' else '((e.clientY-r.top)/r.height*100)'};"
            f"pct=Math.max(10,Math.min(90,pct));"
            f"p1.style.{size_prop}=pct+'%';}});"
            f"document.addEventListener('mouseup',function(){{dragging=false;}});"
            f"}})();"
        )

        return mark_safe(
            f'<div class="split-pane split-pane-{e_dir}" id="{e_uid}">'
            f'<div class="sp-pane sp-pane-1" style="{size_prop}:{e_init}%">{content1}</div>'
            f'<div class="sp-handle sp-handle-{e_dir}"></div>'
            f'<div class="sp-pane sp-pane-2" style="flex:1">{content2}</div>'
            f"</div>"
            f"<script>{js}</script>"
        )


# ---------------------------------------------------------------------------
# 50. Table of Contents
# ---------------------------------------------------------------------------


@register.simple_tag
def table_of_contents(items=None, title="Contents", active="", event=""):
    """Render a table of contents from a list of items.

    Args:
        items: list of dicts {"id": "section-1", "label": "Introduction", "level": 1}
        title: TOC heading
        active: currently active section id (highlight)
        event: dj-click event when an item is clicked (sends id as data-value)
    """
    if not items:
        return mark_safe("")

    e_title = conditional_escape(title)
    e_event = conditional_escape(event) if event else ""

    def render_item(item):
        if not isinstance(item, dict):
            return ""
        iid = conditional_escape(str(item.get("id", "")))
        lbl = conditional_escape(str(item.get("label", "")))
        level = int(item.get("level", 1))
        indent = (level - 1) * 1.0
        active_cls = " toc-item-active" if str(item.get("id", "")) == active else ""
        event_attr = f' dj-click="{e_event}" data-value="{iid}"' if e_event else ""
        return (
            f'<a href="#{iid}" class="toc-item toc-level-{level}{active_cls}" '
            f'style="padding-left:{indent + 0.75}rem"{event_attr}>{lbl}</a>'
        )

    items_html = "".join(render_item(i) for i in items)
    title_html = f'<div class="toc-title">{e_title}</div>' if title else ""

    return mark_safe(f'<nav class="toc">{title_html}<div class="toc-list">{items_html}</div></nav>')


# ---------------------------------------------------------------------------
# 51. Virtualized List (server-paginated "virtual" list)
# ---------------------------------------------------------------------------


@register.simple_tag
def virtual_list(
    items=None, total=0, page=1, page_size=20, load_more_event="load_more", item_height=48
):
    """Render a paginated list optimised for large datasets.

    Renders one page of items in a scrollable container. A 'Load more'
    sentinel triggers the server to extend the list.
    """
    if items is None:
        items = []
    try:
        total = int(total)
        page = int(page)
        page_size = int(page_size)
        item_height = int(item_height)
    except (ValueError, TypeError):
        total = len(items)
        page = 1
        page_size = 20
        item_height = 48

    e_load = conditional_escape(load_more_event)
    has_more = (page * page_size) < total

    rows = ""
    for item in items:
        if isinstance(item, dict):
            label = conditional_escape(str(item.get("label", item.get("title", str(item)))))
            sub = conditional_escape(str(item.get("sub", item.get("subtitle", ""))))
            sub_html = f'<span class="vl-item-sub">{sub}</span>' if sub else ""
            rows += (
                f'<div class="vl-item" style="height:{item_height}px">'
                f'<span class="vl-item-label">{label}</span>'
                f"{sub_html}"
                f"</div>"
            )
        else:
            rows += (
                f'<div class="vl-item" style="height:{item_height}px">'
                f'<span class="vl-item-label">{conditional_escape(str(item))}</span>'
                f"</div>"
            )

    shown = min(len(items), page * page_size)
    load_more_html = (
        f'<div class="vl-load-more">'
        f'<button class="btn btn-ghost btn-sm" dj-click="{e_load}">'
        f"Load more ({total - shown} remaining)"
        f"</button>"
        f"</div>"
        if has_more
        else ""
    )

    return mark_safe(
        f'<div class="virtual-list">'
        f'<div class="vl-info">Showing {shown} of {total} items</div>'
        f'<div class="vl-scroll">'
        f"{rows}"
        f"{load_more_html}"
        f"</div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# 52. Kanban Board
# ---------------------------------------------------------------------------


@register.simple_tag
def kanban_board(
    columns=None,
    move_event="kanban_move",
    add_card_event="kanban_add_card",
    add_col_event="kanban_add_column",
):
    """Render a Kanban board.

    Args:
        columns: list of dicts:
            {"id": "todo", "title": "To Do", "color": "#6366F1",
             "cards": [{"id": "c1", "title": "Task A", "label": "bug"}]}
        move_event: event fired on drag-drop with JSON payload {card_id, from_col, to_col}
        add_card_event: event fired when adding a card, passes column id
    """
    if not columns:
        return mark_safe('<div class="kanban"></div>')

    e_move = conditional_escape(move_event)
    e_add_card = conditional_escape(add_card_event)

    cols_html = ""
    for col in columns:
        if not isinstance(col, dict):
            continue
        col_id = conditional_escape(str(col.get("id", "")))
        col_title = conditional_escape(str(col.get("title", "")))
        col_color = conditional_escape(str(col.get("color", "#6366F1")))
        cards = col.get("cards", [])

        cards_html = ""
        for card in cards:
            if not isinstance(card, dict):
                continue
            card_id = conditional_escape(str(card.get("id", "")))
            card_title = conditional_escape(str(card.get("title", "")))
            card_label = card.get("label", "")
            card_sub = card.get("sub", "")
            label_html = (
                f'<span class="kanban-card-label kanban-label-{conditional_escape(card_label)}">'
                f"{conditional_escape(card_label)}</span>"
                if card_label
                else ""
            )
            sub_html = (
                f'<div class="kanban-card-sub">{conditional_escape(card_sub)}</div>'
                if card_sub
                else ""
            )
            cards_html += (
                f'<div class="kanban-card" draggable="true" '
                f'data-card-id="{card_id}" data-col-id="{col_id}" '
                f"ondragstart=\"(function(e,el){{e.dataTransfer.setData('card',el.dataset.cardId);"
                f"e.dataTransfer.setData('from',el.dataset.colId);el.classList.add('dragging');}})( event,this)\" "
                f"ondragend=\"this.classList.remove('dragging')\">"
                f'<div class="kanban-card-title">{card_title}</div>'
                f"{sub_html}"
                f"{label_html}"
                f"</div>"
            )

        add_btn = (
            f'<button class="kanban-add-card" dj-click="{e_add_card}" '
            f'data-value="{col_id}">+ Add card</button>'
        )

        cols_html += (
            f'<div class="kanban-col" '
            f'data-col-id="{col_id}" '
            f"ondragover=\"event.preventDefault();this.classList.add('kanban-col-over')\" "
            f"ondragleave=\"this.classList.remove('kanban-col-over')\" "
            f"ondrop=\"(function(e,el){{e.preventDefault();el.classList.remove('kanban-col-over');"
            f"var cid=e.dataTransfer.getData('card');"
            f"var from=e.dataTransfer.getData('from');"
            f"var to=el.dataset.colId;"
            f"if(from!==to){{window.djust&&window.djust.handleEvent('{e_move}',{{card_id:cid,from_col:from,to_col:to}});}}"
            f'}})( event,this)">'
            f'<div class="kanban-col-header" style="border-top-color:{col_color}">'
            f'<span class="kanban-col-title">{col_title}</span>'
            f'<span class="kanban-col-count">{len(cards)}</span>'
            f"</div>"
            f'<div class="kanban-cards">{cards_html}</div>'
            f"{add_btn}"
            f"</div>"
        )

    return mark_safe(f'<div class="kanban">{cols_html}</div>')


# ---------------------------------------------------------------------------
# 53. Rich Text Editor
# ---------------------------------------------------------------------------


@register.simple_tag
def rich_text_editor(
    name="content",
    value="",
    event="update_content",
    placeholder="Start typing…",
    height="200px",
    label="",
    required=False,
):
    """Render a basic rich text editor (contenteditable + toolbar).

    Toolbar: Bold, Italic, Underline, Strikethrough, | H2, H3, | UL, OL, | Link, Quote, Code
    The content is synced to the server via dj-input on blur.
    """
    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_value = value  # Already HTML — rendered as-is (trust server content)
    e_event = conditional_escape(event)
    e_placeholder = conditional_escape(placeholder)
    e_height = conditional_escape(height)
    e_label = conditional_escape(label)

    uid = f"rte-{uuid.uuid4().hex[:6]}"
    e_uid = conditional_escape(uid)

    label_html = f'<label class="form-label">{e_label}</label>' if label else ""
    req_html = ' <span class="form-required">*</span>' if required else ""

    buttons = [
        ("bold", "B", "bold"),
        ("italic", "I", "italic"),
        ("underline", "U", "underline"),
        ("strikeThrough", "S̶", "strikeThrough"),
        ("|", "", ""),
        ("formatBlock", "H2", "h2"),
        ("formatBlock", "H3", "h3"),
        ("|", "", ""),
        ("insertUnorderedList", "•", "insertUnorderedList"),
        ("insertOrderedList", "1.", "insertOrderedList"),
        ("|", "", ""),
        ("formatBlock", "❝", "blockquote"),
        ("formatBlock", "</>", "pre"),
    ]

    toolbar_html = ""
    for cmd, lbl, arg in buttons:
        if cmd == "|":
            toolbar_html += '<div class="rte-sep"></div>'
        else:
            e_cmd = conditional_escape(cmd)
            e_lbl = conditional_escape(lbl)
            toolbar_html += (
                f'<button class="rte-btn" type="button" title="{e_cmd}" '
                f'onmousedown="event.preventDefault();'
                f"document.execCommand('{e_cmd}',false,{repr(arg)});\">"
                f"{e_lbl}</button>"
            )

    return mark_safe(
        f'<div class="form-group">'
        f"{label_html}{req_html}"
        f'<div class="rte" id="{e_uid}">'
        f'<div class="rte-toolbar">{toolbar_html}</div>'
        f'<div class="rte-editor" id="{e_uid}-editor" '
        f'contenteditable="true" '
        f'style="min-height:{e_height}" '
        f'data-placeholder="{e_placeholder}" '
        f'dj-input="{e_event}" '
        f"oninput=\"(function(el){{var h=document.getElementById('{e_uid}-hidden');"
        f'if(h)h.value=el.innerHTML;}})(this)">'
        f"{e_value}"
        f"</div>"
        f'<input type="hidden" id="{e_uid}-hidden" name="{e_name}" value="">'
        f"</div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Multi-select (#53)
# ---------------------------------------------------------------------------


@register.simple_tag
def multi_select(
    name="",
    label="",
    options=None,
    selected=None,
    event="",
    placeholder="Search...",
    disabled=False,
):
    """Render a multi-select checkbox list with search filtering and tag output.

    Args:
        name: form field name
        label: label text above the control
        options: list of dicts {"value":..., "label":...} or list of 2-tuples
        selected: list of currently selected values
        event: dj-change event name
        placeholder: search input placeholder
        disabled: disables the control
    """
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if options is None:
        options = []
    if selected is None:
        selected = []
    # Normalise selected to list of strings
    selected = [str(s) for s in selected]

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_placeholder = conditional_escape(placeholder)
    dj_event = conditional_escape(event or name)
    disabled_attr = " disabled" if disabled else ""

    uid = f"ms-{uuid.uuid4().hex[:6]}"

    def _opt_pair(opt):
        if isinstance(opt, dict):
            return str(opt.get("value", "")), str(opt.get("label", ""))
        if isinstance(opt, (list, tuple)) and len(opt) >= 2:
            return str(opt[0]), str(opt[1])
        return str(opt), str(opt)

    # Build tag chips for selected values
    tag_parts = []
    for opt in options:
        ov, ol = _opt_pair(opt)
        if ov in selected:
            tag_parts.append(
                f'<span class="multi-select-tag">'
                f"{conditional_escape(ol)}"
                f'<button type="button" class="multi-select-tag-remove" '
                f'dj-click="{dj_event}" data-value="{conditional_escape(ov)}"'
                f"{disabled_attr}>&times;</button>"
                f"</span>"
            )

    tags_html = f'<div class="multi-select-tags">{"".join(tag_parts)}</div>' if tag_parts else ""

    # Build checkbox list
    cb_parts = []
    for opt in options:
        ov, ol = _opt_pair(opt)
        checked_attr = " checked" if ov in selected else ""
        cb_parts.append(
            f'<label class="multi-select-option">'
            f'<input type="checkbox" name="{e_name}" value="{conditional_escape(ov)}"'
            f'{checked_attr}{disabled_attr} dj-change="{dj_event}">'
            f" {conditional_escape(ol)}"
            f"</label>"
        )

    label_html = f'<label class="form-label">{e_label}</label>' if label else ""

    return mark_safe(
        f'<div class="multi-select" id="{uid}">'
        f"{label_html}"
        f"{tags_html}"
        f'<input type="text" class="multi-select-search" '
        f'placeholder="{e_placeholder}"{disabled_attr} '
        f"oninput=\"(function(el){{var items=el.parentElement.querySelectorAll('.multi-select-option');"
        f"var q=el.value.toLowerCase();items.forEach(function(item){{item.style.display="
        f"item.textContent.toLowerCase().indexOf(q)>=0?'':'none';}});}})(this)\">"
        f'<div class="multi-select-options">{"".join(cb_parts)}</div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# OTP Input (#58)
# ---------------------------------------------------------------------------


@register.simple_tag
def otp_input(name="", digits=6, event="", label="", disabled=False):
    """Render a one-time-code input with individual digit boxes.

    Args:
        name: form field name (hidden input holds the full code)
        digits: number of digit boxes (4 or 6 typical)
        event: dj-change event name
        label: optional label above the input
        disabled: disables all boxes
    """
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    try:
        digits = int(digits)
    except (ValueError, TypeError):
        digits = 6
    digits = max(1, min(12, digits))

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    dj_event = conditional_escape(event or name)
    disabled_attr = " disabled" if disabled else ""

    uid = f"otp-{uuid.uuid4().hex[:6]}"

    box_parts = []
    for i in range(digits):
        box_parts.append(
            f'<input type="text" class="otp-digit" maxlength="1" inputmode="numeric" '
            f'pattern="[0-9]" data-index="{i}" autocomplete="one-time-code"'
            f"{disabled_attr}>"
        )

    label_html = f'<label class="form-label">{e_label}</label>' if label else ""

    return mark_safe(
        f'<div class="otp-input" id="{uid}" data-digits="{digits}">'
        f"{label_html}"
        f'<div class="otp-boxes">{"".join(box_parts)}</div>'
        f'<input type="hidden" name="{e_name}" class="otp-hidden" '
        f'dj-change="{dj_event}">'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Number Stepper (#59)
# ---------------------------------------------------------------------------


@register.simple_tag
def number_stepper(
    name="", value=0, min_val=None, max_val=None, step=1, event="", label="", disabled=False
):
    """Render a +/- numeric stepper input.

    Args:
        name: form field name
        value: current value
        min_val: minimum allowed value (None = no minimum)
        max_val: maximum allowed value (None = no maximum)
        step: increment/decrement amount
        event: dj-click event name for +/- buttons
        label: optional label
        disabled: disables the control
    """
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    try:
        value = int(value)
    except (ValueError, TypeError):
        value = 0
    try:
        step = int(step)
    except (ValueError, TypeError):
        step = 1

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    dj_event = conditional_escape(event or name)
    disabled_attr = " disabled" if disabled else ""

    min_attr = f' min="{int(min_val)}"' if min_val is not None else ""
    max_attr = f' max="{int(max_val)}"' if max_val is not None else ""

    label_html = f'<label class="form-label" for="{e_name}">{e_label}</label>' if label else ""

    return mark_safe(
        f'<div class="number-stepper">'
        f"{label_html}"
        f'<div class="number-stepper-controls">'
        f'<button type="button" class="number-stepper-btn number-stepper-dec" '
        f'dj-click="{dj_event}" data-value="dec"{disabled_attr}>&minus;</button>'
        f'<input type="number" class="number-stepper-input" name="{e_name}" '
        f'id="{e_name}" value="{value}" step="{step}"'
        f'{min_attr}{max_attr}{disabled_attr} dj-change="{dj_event}">'
        f'<button type="button" class="number-stepper-btn number-stepper-inc" '
        f'dj-click="{dj_event}" data-value="inc"{disabled_attr}>&plus;</button>'
        f"</div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Tag Input (#63)
# ---------------------------------------------------------------------------


@register.simple_tag
def tag_input(
    name="",
    tags=None,
    suggestions=None,
    event="",
    placeholder="Add tag...",
    disabled=False,
    label="",
):
    """Render an input that creates dismissible tags.

    Args:
        name: form field name
        tags: list of current tag strings
        suggestions: list of suggestion strings
        event: dj-click event name for add/remove
        placeholder: input placeholder text
        disabled: disables the control
        label: optional label
    """
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if tags is None:
        tags = []
    if suggestions is None:
        suggestions = []

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_placeholder = conditional_escape(placeholder)
    dj_event = conditional_escape(event or name)
    disabled_attr = " disabled" if disabled else ""

    uid = f"ti-{uuid.uuid4().hex[:6]}"

    # Build existing tag chips
    tag_parts = []
    for tag in tags:
        e_tag = conditional_escape(str(tag))
        tag_parts.append(
            f'<span class="tag-input-tag">'
            f"{e_tag}"
            f'<button type="button" class="tag-input-remove" '
            f'dj-click="{dj_event}" data-value="remove:{e_tag}"'
            f"{disabled_attr}>&times;</button>"
            f'<input type="hidden" name="{e_name}" value="{e_tag}">'
            f"</span>"
        )

    # Build suggestion datalist
    suggestion_parts = []
    for s in suggestions:
        suggestion_parts.append(f'<option value="{conditional_escape(str(s))}">')

    label_html = f'<label class="form-label">{e_label}</label>' if label else ""

    return mark_safe(
        f'<div class="tag-input" id="{uid}">'
        f"{label_html}"
        f'<div class="tag-input-tags">{"".join(tag_parts)}</div>'
        f'<input type="text" class="tag-input-field" '
        f'placeholder="{e_placeholder}" list="{uid}-suggestions"'
        f"{disabled_attr} "
        f'dj-keydown.enter="{dj_event}">'
        f'<datalist id="{uid}-suggestions">{"".join(suggestion_parts)}</datalist>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Input Group (#64)
# ---------------------------------------------------------------------------


class InputGroupNode(template.Node):
    """Wraps child content (addons + input) in an input-group container."""

    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        size = kw.get("size", "md")
        error = kw.get("error", "")
        content = self.nodelist.render(context)
        size_cls = f" input-group-{conditional_escape(size)}" if size != "md" else ""
        error_cls = " input-group-error" if error else ""
        error_html = (
            f'<span class="form-error-message">{conditional_escape(error)}</span>' if error else ""
        )
        return mark_safe(
            f'<div class="input-group{size_cls}{error_cls}">{content}</div>{error_html}'
        )


class InputAddonNode(template.Node):
    """Renders a prefix/suffix addon inside an input group."""

    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        position = kw.get("position", "prefix")
        content = self.nodelist.render(context)
        return mark_safe(
            f'<span class="input-addon input-addon-{conditional_escape(position)}">{content}</span>'
        )


@register.tag("input_group")
def do_input_group(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endinput_group",))
    parser.delete_first_token()
    return InputGroupNode(nodelist, kwargs)


@register.tag("input_addon")
def do_input_addon(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endinput_addon",))
    parser.delete_first_token()
    return InputAddonNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Label (#66)
# ---------------------------------------------------------------------------


class DjLabelNode(template.Node):
    """Renders an accessible form label element."""

    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        for_input = kw.get("for", "")
        required = kw.get("required", False)
        if isinstance(required, str):
            required = required.lower() not in ("false", "0", "")
        extra_class = kw.get("class", "")

        content = self.nodelist.render(context)
        for_attr = f' for="{conditional_escape(for_input)}"' if for_input else ""
        required_span = ' <span class="form-required">*</span>' if required else ""
        cls = f"form-label {conditional_escape(extra_class)}".strip()

        return mark_safe(f'<label class="{cls}"{for_attr}>{content}{required_span}</label>')


@register.tag("dj_label")
def do_dj_label(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("enddj_label",))
    parser.delete_first_token()
    return DjLabelNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Fieldset (#147)
# ---------------------------------------------------------------------------


class FieldsetNode(template.Node):
    """Renders a styled fieldset with legend."""

    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        legend = kw.get("legend", "")
        disabled = kw.get("disabled", False)
        if isinstance(disabled, str):
            disabled = disabled.lower() not in ("false", "0", "")
        extra_class = kw.get("class", "")

        content = self.nodelist.render(context)
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


@register.tag("fieldset")
def do_fieldset(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endfieldset",))
    parser.delete_first_token()
    return FieldsetNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Toggle Group (#61)
# ---------------------------------------------------------------------------


@register.simple_tag
def toggle_group(
    name="", options=None, value="", event="toggle_select", mode="single", disabled=False, size="md"
):
    """Render a segmented toggle button group (radio-style or multi-select).

    Args:
        name: group name for identification
        options: list of dicts with keys: value, label, icon (optional)
        value: currently selected value (or list of values in multi mode)
        event: dj-click event name
        mode: "single" (radio) or "multi" (checkbox-style)
        disabled: disables all buttons
        size: sm, md, lg
    """
    if options is None:
        options = []
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_event = conditional_escape(event)
    e_mode = conditional_escape(mode)

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

        if mode == "multi" and isinstance(value, (list, tuple)):
            is_active = opt.get("value", "") in value
        else:
            is_active = str(opt.get("value", "")) == str(value)

        active_cls = " toggle-group-btn--active" if is_active else ""
        aria_pressed = "true" if is_active else "false"
        disabled_attr = " disabled" if disabled else ""
        click_attr = "" if disabled else f' dj-click="{e_event}" data-value="{opt_value}"'

        icon_html = ""
        if opt_icon:
            icon_html = (
                f'<span class="toggle-group-icon">{conditional_escape(str(opt_icon))}</span>'
            )

        buttons.append(
            f'<button class="toggle-group-btn{active_cls}" '
            f'aria-pressed="{aria_pressed}" '
            f'data-name="{e_name}"{click_attr}{disabled_attr}>'
            f"{icon_html}"
            f'<span class="toggle-group-label">{opt_label}</span>'
            f"</button>"
        )

    return mark_safe(
        f'<div class="toggle-group{size_cls}{disabled_cls}" '
        f'role="group" data-mode="{e_mode}">'
        f"{''.join(buttons)}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Floating Action Button (#65)
# ---------------------------------------------------------------------------


@register.simple_tag
def fab(
    icon="+",
    event="",
    position="bottom-right",
    label="",
    size="md",
    variant="primary",
    disabled=False,
    actions=None,
):
    """Render a floating action button with optional speed-dial actions.

    Args:
        icon: icon text/emoji for the FAB
        event: dj-click event name
        position: bottom-right, bottom-left, top-right, top-left
        label: accessible label / tooltip text
        size: sm, md, lg
        variant: primary, secondary, danger, success
        disabled: disables the FAB
        actions: list of dicts with keys: icon, event, label (speed-dial)
    """
    if actions is None:
        actions = []
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")

    e_icon = conditional_escape(icon)
    e_event = conditional_escape(event)
    e_label = conditional_escape(label)

    valid_positions = ("bottom-right", "bottom-left", "top-right", "top-left")
    pos_cls = position if position in valid_positions else "bottom-right"
    pos_cls = conditional_escape(pos_cls)

    size_cls = ""
    if size and size != "md":
        size_cls = f" fab-{conditional_escape(size)}"
    variant_cls = f" fab-{conditional_escape(variant)}"
    disabled_attr = " disabled" if disabled else ""
    click_attr = "" if disabled or not event else f' dj-click="{e_event}"'
    aria_label = f' aria-label="{e_label}"' if label else ""

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
        f'<span class="fab-icon">{e_icon}</span>'
        f"</button>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Split Button (#133)
# ---------------------------------------------------------------------------


@register.simple_tag
def split_button(
    label="",
    event="",
    options=None,
    variant="primary",
    size="md",
    disabled=False,
    loading=False,
    is_open=False,
    toggle_event="toggle_split_menu",
    **kwargs,
):
    """Render a split button with primary action and dropdown secondary actions.

    Args:
        label: primary button text
        event: dj-click event for primary action
        options: list of dicts with keys: label, event
        variant: primary, secondary, danger, success
        size: sm, md, lg
        disabled: disables all buttons
        loading: shows spinner on primary, disables all
        is_open: whether the dropdown menu is open
        toggle_event: dj-click event for toggle button

    Deprecated aliases (still accepted):
        open → is_open
    """
    # Backward-compat: accept deprecated 'open' kwarg
    if "open" in kwargs:
        is_open = kwargs.pop("open")

    if options is None:
        options = []
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if isinstance(loading, str):
        loading = loading.lower() not in ("false", "0", "")
    if isinstance(is_open, str):
        is_open = is_open.lower() not in ("false", "0", "")

    e_label = conditional_escape(label)
    e_event = conditional_escape(event)
    e_toggle = conditional_escape(toggle_event)

    variant_cls = f" split-btn-{conditional_escape(variant)}"
    size_cls = ""
    if size and size != "md":
        size_cls = f" split-btn-{conditional_escape(size)}"
    loading_cls = " split-btn-loading" if loading else ""
    disabled_attr = " disabled" if disabled or loading else ""
    click_attr = "" if disabled or loading or not event else f' dj-click="{e_event}"'

    spinner_html = '<span class="split-btn-spinner"></span>' if loading else ""

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
    toggle_click = "" if disabled or loading else f' dj-click="{e_toggle}"'

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
        f'<span class="split-btn-label">{e_label}</span>'
        f"</button>"
        f'<button class="split-btn-toggle"{toggle_click}{toggle_disabled} '
        f'aria-expanded="{open_data}" aria-haspopup="true">'
        f'<span class="split-btn-caret">&#9662;</span>'
        f"</button>"
        f"{menu_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Scroll Area
# ---------------------------------------------------------------------------


class ScrollAreaNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        max_height = kw.get("max_height", "400px")
        custom_class = kw.get("custom_class", "")
        label = kw.get("label", "Scrollable content")

        e_max_height = conditional_escape(str(max_height))
        e_custom_class = conditional_escape(str(custom_class))
        e_label = conditional_escape(str(label))

        content = self.nodelist.render(context)

        cls = "dj-scroll-area"
        if e_custom_class:
            cls += f" {e_custom_class}"

        return mark_safe(
            f'<div class="{cls}" role="region" aria-label="{e_label}" tabindex="0" '
            f'style="--dj-scroll-area-max-height: {e_max_height}; '
            f'max-height: var(--dj-scroll-area-max-height); overflow-y: auto;">'
            f"{content}</div>"
        )


@register.tag("scroll_area")
def do_scroll_area(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endscroll_area",))
    parser.delete_first_token()
    return ScrollAreaNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Callout / Blockquote
# ---------------------------------------------------------------------------


class CalloutNode(template.Node):
    ICONS = {
        "info": "&#9432;",  # circled i
        "warning": "&#9888;",  # warning sign
        "danger": "&#9888;",  # warning sign
        "success": "&#10004;",  # check mark
    }

    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        callout_type = kw.get("variant", kw.get("type", "default"))
        title = kw.get("title", "")
        icon = kw.get("icon", "")
        custom_class = kw.get("custom_class", "")

        e_type = conditional_escape(str(callout_type))
        e_title = conditional_escape(str(title))
        e_icon = conditional_escape(str(icon))
        e_custom_class = conditional_escape(str(custom_class))

        content = self.nodelist.render(context)

        cls = "dj-callout"
        if callout_type != "default":
            cls += f" dj-callout--{e_type}"
        if e_custom_class:
            cls += f" {e_custom_class}"

        icon_html = ""
        if e_icon:
            icon_html = f'<span class="dj-callout__icon">{e_icon}</span>'
        elif callout_type in self.ICONS:
            icon_html = f'<span class="dj-callout__icon">{self.ICONS[callout_type]}</span>'

        title_html = ""
        if e_title:
            title_html = f'<div class="dj-callout__title">{e_title}</div>'

        return mark_safe(
            f'<div class="{cls}">'
            f"{icon_html}"
            f'<div class="dj-callout__body">'
            f"{title_html}"
            f'<div class="dj-callout__content">{content}</div>'
            f"</div>"
            f"</div>"
        )


@register.tag("callout")
def do_callout(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endcallout",))
    parser.delete_first_token()
    return CalloutNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Aspect Ratio
# ---------------------------------------------------------------------------


class AspectRatioNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        ratio = kw.get("ratio", "16/9")
        custom_class = kw.get("custom_class", "")

        e_ratio = conditional_escape(str(ratio))
        e_custom_class = conditional_escape(str(custom_class))

        content = self.nodelist.render(context)

        cls = "dj-aspect-ratio"
        if e_custom_class:
            cls += f" {e_custom_class}"

        return mark_safe(f'<div class="{cls}" style="aspect-ratio: {e_ratio};">{content}</div>')


@register.tag("aspect_ratio")
def do_aspect_ratio(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endaspect_ratio",))
    parser.delete_first_token()
    return AspectRatioNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Description List
# ---------------------------------------------------------------------------


class DescriptionListNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        items = kw.get("items", [])
        layout = kw.get("layout", "vertical")
        custom_class = kw.get("custom_class", "")

        e_custom_class = conditional_escape(str(custom_class))

        cls = "dj-dl"
        if layout == "horizontal":
            cls += " dj-dl--horizontal"
        if e_custom_class:
            cls += f" {e_custom_class}"

        dl_items = []
        if isinstance(items, (list, tuple)):
            for item in items:
                if isinstance(item, dict):
                    term = conditional_escape(str(item.get("term", "")))
                    detail = conditional_escape(str(item.get("detail", "")))
                    dl_items.append(
                        f'<div class="dj-dl__pair">'
                        f'<dt class="dj-dl__term">{term}</dt>'
                        f'<dd class="dj-dl__detail">{detail}</dd>'
                        f"</div>"
                    )

        return mark_safe(f'<dl class="{cls}">{"".join(dl_items)}</dl>')


@register.tag("description_list")
def do_description_list(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return DescriptionListNode(kwargs)


# ---------------------------------------------------------------------------
# Sticky Header
# ---------------------------------------------------------------------------


class StickyHeaderNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        offset = kw.get("offset", "0")
        z_index = kw.get("z_index", "10")
        custom_class = kw.get("custom_class", "")

        e_offset = conditional_escape(str(offset))
        e_z_index = conditional_escape(str(z_index))
        e_custom_class = conditional_escape(str(custom_class))

        content = self.nodelist.render(context)

        cls = "dj-sticky-header"
        if e_custom_class:
            cls += f" {e_custom_class}"

        return mark_safe(
            f'<div class="{cls}" style="position: sticky; top: {e_offset}; '
            f'z-index: {e_z_index};">'
            f"{content}</div>"
        )


@register.tag("sticky_header")
def do_sticky_header(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endsticky_header",))
    parser.delete_first_token()
    return StickyHeaderNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Notification Badge
# ---------------------------------------------------------------------------


class NotificationBadgeNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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
        size = kw.get("size", "md")
        custom_class = kw.get("custom_class", "")

        e_size = conditional_escape(str(size))
        e_custom_class = conditional_escape(str(custom_class))

        cls = f"dj-notification-badge dj-notification-badge--{e_size}"
        if pulse:
            cls += " dj-notification-badge--pulse"
        if e_custom_class:
            cls += f" {e_custom_class}"

        if dot:
            return mark_safe(f'<span class="{cls} dj-notification-badge--dot"></span>')

        display = f"{max_count}+" if count > max_count else str(count)
        if count <= 0:
            return ""

        return mark_safe(f'<span class="{cls}">{display}</span>')


@register.tag("notification_badge")
def do_notification_badge(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return NotificationBadgeNode(kwargs)


# ---------------------------------------------------------------------------
# Segmented Progress
# ---------------------------------------------------------------------------


class SegmentedProgressNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        steps = kw.get("steps", [])
        if not isinstance(steps, (list, tuple)):
            steps = []
        try:
            current = int(kw.get("current", 0))
        except (ValueError, TypeError):
            current = 0
        size = kw.get("size", "md")
        custom_class = kw.get("custom_class", "")

        e_size = conditional_escape(str(size))
        e_custom_class = conditional_escape(str(custom_class))

        cls = f"dj-segmented-progress dj-segmented-progress--{e_size}"
        if e_custom_class:
            cls += f" {e_custom_class}"

        segments = []
        for i, step in enumerate(steps):
            label = (
                conditional_escape(str(step))
                if isinstance(step, str)
                else conditional_escape(str(step.get("label", "")))
                if isinstance(step, dict)
                else conditional_escape(str(step))
            )
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

        # Connector lines between steps
        parts = []
        for i, seg in enumerate(segments):
            parts.append(seg)
            if i < len(segments) - 1:
                step_num = i + 1
                line_state = "completed" if step_num < current else "pending"
                parts.append(
                    f'<div class="dj-segmented-progress__connector dj-segmented-progress__connector--{line_state}"></div>'
                )

        return mark_safe(f'<div class="{cls}">{"".join(parts)}</div>')


@register.tag("segmented_progress")
def do_segmented_progress(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return SegmentedProgressNode(kwargs)


# ---------------------------------------------------------------------------
# Progress Circle
# ---------------------------------------------------------------------------


class ProgressCircleNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        try:
            value = max(0, min(100, int(kw.get("value", 0))))
        except (ValueError, TypeError):
            value = 0
        size = kw.get("size", "md")
        color = kw.get("color", "primary")
        show_value = kw.get("show_value", True)
        custom_class = kw.get("custom_class", "")

        e_size = conditional_escape(str(size))
        e_color = conditional_escape(str(color))
        e_custom_class = conditional_escape(str(custom_class))

        sizes = {"sm": 48, "md": 80, "lg": 120}
        dim = sizes.get(str(size), 80)
        stroke_widths = {"sm": 4, "md": 6, "lg": 8}
        stroke_w = stroke_widths.get(str(size), 6)

        radius = (dim - stroke_w) / 2
        circumference = 2 * 3.14159265 * radius
        dash_offset = circumference * (1 - value / 100)

        cls = f"dj-progress-circle dj-progress-circle--{e_size} dj-progress-circle--{e_color}"
        if e_custom_class:
            cls += f" {e_custom_class}"

        value_html = ""
        if show_value:
            font_sizes = {"sm": "0.625rem", "md": "1rem", "lg": "1.5rem"}
            fs = font_sizes.get(str(size), "1rem")
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


@register.tag("progress_circle")
def do_progress_circle(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ProgressCircleNode(kwargs)


# ---------------------------------------------------------------------------
# Status Indicator
# ---------------------------------------------------------------------------


class StatusIndicatorNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        status = kw.get("status", "offline")
        label = kw.get("label", "")
        pulse = kw.get("pulse", False)
        size = kw.get("size", "md")
        custom_class = kw.get("custom_class", "")

        e_label = conditional_escape(str(label))
        e_size = conditional_escape(str(size))
        e_custom_class = conditional_escape(str(custom_class))

        # Map statuses to colors
        status_colors = {
            "online": "green",
            "degraded": "yellow",
            "offline": "red",
            "maintenance": "blue",
        }
        color = status_colors.get(str(status), "gray")

        cls = f"dj-status-indicator dj-status-indicator--{e_size} dj-status-indicator--{color}"
        if pulse:
            cls += " dj-status-indicator--pulse"
        if e_custom_class:
            cls += f" {e_custom_class}"

        dot_html = '<span class="dj-status-indicator__dot"></span>'
        label_html = f'<span class="dj-status-indicator__label">{e_label}</span>' if label else ""

        return mark_safe(f'<span class="{cls}" role="status">{dot_html}{label_html}</span>')


@register.tag("status_indicator")
def do_status_indicator(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return StatusIndicatorNode(kwargs)


# ---------------------------------------------------------------------------
# Loading Overlay
# ---------------------------------------------------------------------------


class LoadingOverlayNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        active = kw.get("active", False)
        text = kw.get("text", "")
        spinner_size = kw.get("spinner_size", "md")
        custom_class = kw.get("custom_class", "")

        content = self.nodelist.render(context)

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


@register.tag("loading_overlay")
def do_loading_overlay(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endloading_overlay",))
    parser.delete_first_token()
    return LoadingOverlayNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Announcement Bar
# ---------------------------------------------------------------------------


class AnnouncementBarNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        bar_type = kw.get("variant", kw.get("type", "info"))
        dismissible = kw.get("dismissible", False)
        dismiss_event = kw.get("dismiss_event", "dismiss_announcement")
        custom_class = kw.get("custom_class", "")

        content = self.nodelist.render(context)

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


@register.tag("announcement_bar")
def do_announcement_bar(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endannouncement_bar",))
    parser.delete_first_token()
    return AnnouncementBarNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Rich Select (#103)
# ---------------------------------------------------------------------------


@register.simple_tag
def rich_select(
    name="",
    options=None,
    value="",
    event="",
    placeholder="Select...",
    disabled=False,
    searchable=False,
    label="",
):
    """Render a rich select dropdown where each option can include icons, images,
    descriptions, or badges alongside the label.

    Args:
        name: form field name
        options: list of dicts with keys: value, label, and optional icon, image,
                 description, badge
        value: currently selected value
        event: dj-click event name for selection
        placeholder: text shown when nothing is selected
        disabled: disables the control
        searchable: adds a search input to filter options
        label: optional label above the control
    """
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if isinstance(searchable, str):
        searchable = searchable.lower() not in ("false", "0", "")
    if options is None:
        options = []

    value = str(value) if value else ""

    e_name = conditional_escape(name)
    e_placeholder = conditional_escape(placeholder)
    e_label = conditional_escape(label)
    dj_event = conditional_escape(event or name)
    disabled_attr = " disabled" if disabled else ""
    disabled_cls = " rich-select--disabled" if disabled else ""

    uid = f"rs-{uuid.uuid4().hex[:6]}"

    # Build selected display
    selected_opt = None
    for opt in options:
        if isinstance(opt, dict) and str(opt.get("value", "")) == value:
            selected_opt = opt
            break

    if selected_opt:
        selected_html = _rich_select_option_html(selected_opt, is_display=True)
    else:
        selected_html = f'<span class="rich-select-placeholder">{e_placeholder}</span>'

    # Build option list
    opt_parts = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        ov = str(opt.get("value", ""))
        active_cls = " rich-select-option--active" if ov == value else ""
        opt_html = _rich_select_option_html(opt, is_display=False)
        opt_parts.append(
            f'<div class="rich-select-option{active_cls}" '
            f'data-value="{conditional_escape(ov)}" '
            f'dj-click="{dj_event}" '
            f'role="option" aria-selected="{"true" if ov == value else "false"}">'
            f"{opt_html}"
            f"</div>"
        )

    search_html = ""
    if searchable:
        search_html = (
            '<div class="rich-select-search">'
            '<input type="text" class="rich-select-search-input" '
            'placeholder="Search..." '
            "oninput=\"(function(el){var items=el.closest('.rich-select-dropdown')."
            "querySelectorAll('.rich-select-option');"
            "var q=el.value.toLowerCase();items.forEach(function(item){item.style.display="
            "item.textContent.toLowerCase().indexOf(q)>=0?'':'none';});})(this)\">"
            "</div>"
        )

    label_html = f'<label class="form-label">{e_label}</label>' if label else ""

    return mark_safe(
        f'<div class="rich-select{disabled_cls}" id="{uid}">'
        f"{label_html}"
        f'<input type="hidden" name="{e_name}" value="{conditional_escape(value)}">'
        f'<div class="rich-select-trigger" tabindex="0" role="combobox" '
        f'aria-expanded="false" aria-haspopup="listbox"{disabled_attr} '
        f"onclick=\"this.parentElement.classList.toggle('rich-select--open')\" "
        f"onkeydown=\"if(event.key==='Enter'||event.key===' '){{event.preventDefault();"
        f"this.parentElement.classList.toggle('rich-select--open');}}\">"
        f"{selected_html}"
        f'<span class="rich-select-chevron">&#9662;</span>'
        f"</div>"
        f'<div class="rich-select-dropdown" role="listbox">'
        f"{search_html}"
        f"{''.join(opt_parts)}"
        f"</div>"
        f"</div>"
    )


def _rich_select_option_html(opt, is_display=False):
    """Render the inner HTML for a rich select option."""
    parts = []
    icon = opt.get("icon", "")
    image = opt.get("image", "")
    label = conditional_escape(str(opt.get("label", "")))
    description = opt.get("description", "")
    badge_text = opt.get("badge", "")

    if image:
        parts.append(
            f'<img class="rich-select-option-image" src="{conditional_escape(str(image))}" alt="">'
        )
    elif icon:
        parts.append(
            f'<span class="rich-select-option-icon">{conditional_escape(str(icon))}</span>'
        )

    text_parts = [f'<span class="rich-select-option-label">{label}</span>']
    if description:
        text_parts.append(
            f'<span class="rich-select-option-desc">{conditional_escape(str(description))}</span>'
        )

    parts.append(f'<span class="rich-select-option-text">{"".join(text_parts)}</span>')

    if badge_text:
        parts.append(
            f'<span class="rich-select-option-badge">{conditional_escape(str(badge_text))}</span>'
        )

    return "".join(parts)


# ---------------------------------------------------------------------------
# Data Grid (#54)
# ---------------------------------------------------------------------------


@register.simple_tag
def data_grid(
    columns=None,
    rows=None,
    row_key="id",
    edit_event="grid_cell_edit",
    resizable=True,
    frozen_left=0,
    frozen_right=0,
    striped=False,
    compact=False,
    keyboard_nav=True,
    new_row_event="",
    delete_row_event="",
    custom_class="",
):
    """Render an editable data grid — spreadsheet-like component.

    Distinct from data_table: the grid is optimised for cell-level editing with
    keyboard navigation, column resize, and frozen columns.

    Args:
        columns: list of dicts with keys: key, label, width (optional),
                 editable (bool, default True), type (text|number|select),
                 options (for select type), frozen (left|right|None)
        rows: list of dicts keyed by column keys
        row_key: key field for row identity (default "id")
        edit_event: dj-click event fired on cell edit commit
        resizable: enable column resize handles
        frozen_left: number of columns frozen on the left
        frozen_right: number of columns frozen on the right
        striped: alternating row backgrounds
        compact: reduced cell padding
        keyboard_nav: enable arrow-key cell navigation
        new_row_event: event name for Add Row button (hidden if empty)
        delete_row_event: event name for row deletion
        custom_class: additional CSS classes
    """
    if columns is None:
        columns = []
    if rows is None:
        rows = []
    if isinstance(resizable, str):
        resizable = resizable.lower() not in ("false", "0", "")
    if isinstance(striped, str):
        striped = striped.lower() not in ("false", "0", "")
    if isinstance(compact, str):
        compact = compact.lower() not in ("false", "0", "")
    if isinstance(keyboard_nav, str):
        keyboard_nav = keyboard_nav.lower() not in ("false", "0", "")

    e_edit_event = conditional_escape(edit_event)
    e_custom_class = conditional_escape(custom_class)
    e_new_row_event = conditional_escape(new_row_event)
    e_delete_row_event = conditional_escape(delete_row_event)

    wrapper_cls = "data-grid-wrapper"
    if striped:
        wrapper_cls += " data-grid-striped"
    if compact:
        wrapper_cls += " data-grid-compact"
    if e_custom_class:
        wrapper_cls += f" {e_custom_class}"

    wrapper_attrs = f'class="{wrapper_cls}"'
    if resizable:
        wrapper_attrs += ' data-resizable="true"'
    if keyboard_nav:
        wrapper_attrs += ' data-keyboard-nav="true"'
    wrapper_attrs += f' data-edit-event="{e_edit_event}"'

    # --- Header ---
    header_cells = []
    for idx, col in enumerate(columns):
        if not isinstance(col, dict):
            continue
        col_key = conditional_escape(str(col.get("key", "")))
        col_label = conditional_escape(str(col.get("label", col.get("key", ""))))
        width = col.get("width", "")
        style = (
            f' style="width:{conditional_escape(str(width))};min-width:{conditional_escape(str(width))}"'
            if width
            else ""
        )
        frozen_cls = ""
        if idx < frozen_left:
            frozen_cls = " data-grid-frozen-left"
        elif frozen_right and idx >= len(columns) - frozen_right:
            frozen_cls = " data-grid-frozen-right"
        resize_attr = ' data-resizable="true"' if resizable else ""
        header_cells.append(
            f'<th class="data-grid-header-cell{frozen_cls}" '
            f'data-col-key="{col_key}"{style}{resize_attr}>'
            f"{col_label}</th>"
        )

    # Add delete column header if delete_row_event is set
    if delete_row_event:
        header_cells.append('<th class="data-grid-header-cell data-grid-actions-col"></th>')

    # --- Body rows ---
    body_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rk = conditional_escape(str(row.get(row_key, "")))
        cells = []
        for idx, col in enumerate(columns):
            if not isinstance(col, dict):
                continue
            col_key_raw = str(col.get("key", ""))
            col_key = conditional_escape(col_key_raw)
            cell_val = conditional_escape(str(row.get(col_key_raw, "")))
            editable = col.get("editable", True)
            if isinstance(editable, str):
                editable = editable.lower() not in ("false", "0", "")
            col_type = col.get("type", "text")

            frozen_cls = ""
            if idx < frozen_left:
                frozen_cls = " data-grid-frozen-left"
            elif frozen_right and idx >= len(columns) - frozen_right:
                frozen_cls = " data-grid-frozen-right"

            edit_attr = ' data-editable="true"' if editable else ""
            type_attr = f' data-type="{conditional_escape(str(col_type))}"'

            cells.append(
                f'<td class="data-grid-cell{frozen_cls}" '
                f'data-col-key="{col_key}" tabindex="-1"'
                f"{edit_attr}{type_attr}>"
                f"{cell_val}</td>"
            )

        # Delete button cell
        if delete_row_event:
            cells.append(
                f'<td class="data-grid-cell data-grid-actions-col">'
                f'<button class="data-grid-delete-btn" '
                f'dj-click="{e_delete_row_event}" data-value="{rk}" '
                f'title="Delete row">&times;</button>'
                f"</td>"
            )

        body_rows.append(f'<tr class="data-grid-row" data-row-key="{rk}">{"".join(cells)}</tr>')

    # --- Add Row button ---
    add_row_html = ""
    if new_row_event:
        add_row_html = (
            f'<div class="data-grid-toolbar">'
            f'<button class="data-grid-add-btn" dj-click="{e_new_row_event}">+ Add Row</button>'
            f"</div>"
        )

    # Hidden triggers for edit events
    trigger_html = (
        f'<button class="data-grid-edit-trigger" style="display:none" '
        f'dj-click="{e_edit_event}"></button>'
    )

    return mark_safe(
        f"<div {wrapper_attrs}>"
        f'<div class="data-grid-scroll">'
        f'<table class="data-grid" role="grid">'
        f"<thead><tr>{''.join(header_cells)}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        f"</table>"
        f"</div>"
        f"{trigger_html}"
        f"{add_row_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Streaming Text
# ---------------------------------------------------------------------------


class StreamingTextNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        stream_event = kw.get("stream_event", "stream_chunk")
        text = kw.get("text", "")
        markdown = kw.get("markdown", False)
        auto_scroll = kw.get("auto_scroll", True)
        cursor = kw.get("cursor", True)
        custom_class = kw.get("custom_class", "")

        e_stream_event = conditional_escape(str(stream_event))
        e_custom_class = conditional_escape(str(custom_class))

        cls = "dj-streaming-text"
        if cursor:
            cls += " dj-streaming-text--cursor"
        if e_custom_class:
            cls += f" {e_custom_class}"

        attrs = [
            f'class="{cls}"',
            f'data-stream-event="{e_stream_event}"',
        ]
        if auto_scroll:
            attrs.append('data-auto-scroll="true"')
        if markdown:
            attrs.append('data-markdown="true"')

        attrs_str = " ".join(attrs)
        e_text = conditional_escape(str(text))
        return mark_safe(
            f'<div {attrs_str}><div class="dj-streaming-text__content">{e_text}</div></div>'
        )


@register.tag("streaming_text")
def do_streaming_text(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return StreamingTextNode(kwargs)


# ---------------------------------------------------------------------------
# Connection Status Bar
# ---------------------------------------------------------------------------


class ConnectionStatusNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        custom_class = kw.get("custom_class", "")
        reconnecting_text = kw.get("reconnecting_text", "Reconnecting...")
        connected_text = kw.get("connected_text", "Reconnected")

        e_custom_class = conditional_escape(str(custom_class))
        e_reconnecting_text = conditional_escape(str(reconnecting_text))
        e_connected_text = conditional_escape(str(connected_text))

        cls = "dj-connection-status"
        if e_custom_class:
            cls += f" {e_custom_class}"

        return mark_safe(
            f'<div class="{cls}" '
            f'data-reconnecting-text="{e_reconnecting_text}" '
            f'data-connected-text="{e_connected_text}" '
            f'role="status" aria-live="polite" style="display:none">'
            f'<span class="dj-connection-status__text">{e_reconnecting_text}</span>'
            f"</div>"
        )


@register.tag("connection_status")
def do_connection_status(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ConnectionStatusNode(kwargs)


# ---------------------------------------------------------------------------
# Live Counter
# ---------------------------------------------------------------------------


class LiveCounterNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        try:
            value = int(kw.get("value", 0))
        except (ValueError, TypeError):
            value = 0
        label = kw.get("label", "")
        stream_event = kw.get("stream_event", "counter_update")
        custom_class = kw.get("custom_class", "")
        size = kw.get("size", "md")

        e_stream_event = conditional_escape(str(stream_event))
        e_label = conditional_escape(str(label))
        e_custom_class = conditional_escape(str(custom_class))
        e_size = conditional_escape(str(size))

        cls = f"dj-live-counter dj-live-counter--{e_size}"
        if e_custom_class:
            cls += f" {e_custom_class}"

        label_html = ""
        if e_label:
            label_html = f'<span class="dj-live-counter__label">{e_label}</span>'

        return mark_safe(
            f'<div class="{cls}" data-stream-event="{e_stream_event}">'
            f'<span class="dj-live-counter__value" data-value="{value}">{value}</span>'
            f"{label_html}"
            f"</div>"
        )


@register.tag("live_counter")
def do_live_counter(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return LiveCounterNode(kwargs)


# ---------------------------------------------------------------------------
# Toast Container (Server Event Toast)
# ---------------------------------------------------------------------------


class ToastContainerNode(template.Node):
    ALLOWED_POSITIONS = {
        "top-left",
        "top-right",
        "top-center",
        "bottom-left",
        "bottom-right",
        "bottom-center",
    }

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        position = str(kw.get("position", "top-right"))
        if position not in self.ALLOWED_POSITIONS:
            position = "top-right"
        custom_class = kw.get("custom_class", "")
        max_toasts = kw.get("max_toasts", 5)

        e_custom_class = conditional_escape(str(custom_class))

        try:
            max_toasts = int(max_toasts)
        except (ValueError, TypeError):
            max_toasts = 5

        cls = f"dj-toast-container dj-toast-container--{position}"
        if e_custom_class:
            cls += f" {e_custom_class}"

        return mark_safe(
            f'<div class="{cls}" '
            f'data-max-toasts="{max_toasts}" '
            f'role="region" aria-live="polite" aria-label="Notifications">'
            f"</div>"
        )


@register.tag("server_toast_container")
def do_server_toast_container(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ToastContainerNode(kwargs)


# ---------------------------------------------------------------------------
# Scroll to Top (#125)
# ---------------------------------------------------------------------------


@register.simple_tag
def scroll_to_top(threshold="300px", label="Back to top", custom_class=""):
    """Floating button that appears after scrolling past a threshold.

    Args:
        threshold: scroll distance before button appears (default "300px")
        label: accessible button label
        custom_class: additional CSS classes
    """
    e_threshold = conditional_escape(threshold)
    e_label = conditional_escape(label)
    e_cls = conditional_escape(custom_class)

    cls = "dj-scroll-to-top"
    if e_cls:
        cls += f" {e_cls}"

    return mark_safe(
        f'<button class="{cls}" '
        f'data-threshold="{e_threshold}" '
        f'aria-label="{e_label}" '
        f'title="{e_label}" '
        f'style="display:none">'
        f'<svg width="20" height="20" viewBox="0 0 20 20" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">'
        f'<path d="M10 16V4M10 4l-6 6M10 4l6 6"/>'
        f"</svg>"
        f"</button>"
    )


# ---------------------------------------------------------------------------
# Code Snippet (#139)
# ---------------------------------------------------------------------------


@register.simple_tag
def code_snippet(code="", language="", custom_class=""):
    """Code block with copy button and language badge.

    Args:
        code: source code text
        language: programming language label
        custom_class: additional CSS classes
    """
    e_code = conditional_escape(code)
    e_lang = conditional_escape(language)
    e_cls = conditional_escape(custom_class)

    cls = "dj-code-snippet"
    if e_cls:
        cls += f" {e_cls}"

    lang_badge = ""
    if language:
        lang_badge = f'<span class="dj-code-snippet__lang">{e_lang}</span>'

    return mark_safe(
        f'<div class="{cls}">'
        f'<div class="dj-code-snippet__header">'
        f"{lang_badge}"
        f'<button class="dj-code-snippet__copy" aria-label="Copy code" '
        f'type="button">Copy</button>'
        f"</div>"
        f'<pre class="dj-code-snippet__pre">'
        f'<code class="dj-code-snippet__code">{e_code}</code>'
        f"</pre>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Responsive Image (#140)
# ---------------------------------------------------------------------------


@register.simple_tag
def responsive_image(
    src="", alt="", aspect_ratio="", lazy=True, srcset="", sizes="", placeholder="", custom_class=""
):
    """Picture element with srcset, lazy loading, and blur-up placeholder.

    Args:
        src: image URL
        alt: alt text
        aspect_ratio: CSS aspect-ratio (e.g. "16/9")
        lazy: enable native lazy loading (default True)
        srcset: srcset attribute value
        sizes: sizes attribute value
        placeholder: blur-up placeholder image URL
        custom_class: additional CSS classes
    """
    if isinstance(lazy, str):
        lazy = lazy.lower() not in ("false", "0", "")

    e_src = conditional_escape(src)
    e_alt = conditional_escape(alt)
    e_cls = conditional_escape(custom_class)

    cls = "dj-responsive-image"
    if placeholder:
        cls += " dj-responsive-image--blur-up"
    if e_cls:
        cls += f" {e_cls}"

    style = ""
    if aspect_ratio:
        e_ratio = conditional_escape(aspect_ratio)
        style = f' style="aspect-ratio:{e_ratio}"'

    img_attrs = [f'src="{e_src}"', f'alt="{e_alt}"']
    if lazy:
        img_attrs.append('loading="lazy"')
    if srcset:
        img_attrs.append(f'srcset="{conditional_escape(srcset)}"')
    if sizes:
        img_attrs.append(f'sizes="{conditional_escape(sizes)}"')

    img_tag = f'<img {" ".join(img_attrs)} class="dj-responsive-image__img">'

    placeholder_html = ""
    if placeholder:
        e_ph = conditional_escape(placeholder)
        placeholder_html = (
            f'<img src="{e_ph}" alt="" class="dj-responsive-image__placeholder" aria-hidden="true">'
        )

    return mark_safe(f'<div class="{cls}"{style}>{placeholder_html}{img_tag}</div>')


# ---------------------------------------------------------------------------
# Relative Time (#146)
# ---------------------------------------------------------------------------


@register.simple_tag
def relative_time(datetime="", auto_update=True, interval=60, custom_class=""):
    """Display a datetime as relative text ("3 hours ago") with auto-update.

    Args:
        datetime: ISO datetime string or datetime object
        auto_update: enable client-side interval updates (default True)
        interval: update interval in seconds (default 60)
        custom_class: additional CSS classes
    """
    if isinstance(auto_update, str):
        auto_update = auto_update.lower() not in ("false", "0", "")

    e_cls = conditional_escape(custom_class)
    cls = "dj-relative-time"
    if e_cls:
        cls += f" {e_cls}"

    iso_val = ""
    if datetime:
        if hasattr(datetime, "isoformat"):
            iso_val = datetime.isoformat()
        else:
            iso_val = str(datetime)

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


# ---------------------------------------------------------------------------
# Copyable Text (#153)
# ---------------------------------------------------------------------------


class CopyableTextNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        copied_label = kw.get("copied_label", "Copied!")
        custom_class = kw.get("custom_class", "")

        content = self.nodelist.render(context).strip()

        e_content = conditional_escape(content)
        e_label = conditional_escape(copied_label)
        e_cls = conditional_escape(custom_class)

        cls = "dj-copyable-text"
        if e_cls:
            cls += f" {e_cls}"

        return mark_safe(
            f'<span class="{cls}" '
            f'data-copy-text="{e_content}" '
            f'data-copied-label="{e_label}" '
            f'role="button" tabindex="0" '
            f'aria-label="Click to copy">'
            f'<span class="dj-copyable-text__value">{e_content}</span>'
            f'<span class="dj-copyable-text__tooltip" aria-hidden="true">{e_label}</span>'
            f"</span>"
        )


@register.tag("copyable_text")
def do_copyable_text(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endcopyable_text",))
    parser.delete_first_token()
    return CopyableTextNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Icon System (#178)
# ---------------------------------------------------------------------------


@register.simple_tag
def icon(name="", size="md", set="heroicons", **kwargs):
    """Render an SVG icon from a bundled icon set.

    Args:
        name: icon name (e.g. "check", "x-mark", "sun", "moon")
        size: xs (12px), sm (16px), md (20px), lg (24px)
        set: icon set name (default "heroicons"); extensible via
             DJUST_COMPONENTS_ICON_SETS setting
        **kwargs: extra HTML attributes — ``class`` adds CSS classes,
                  ``aria_label`` becomes ``aria-label``, etc.
    """
    from djust.components.icons import render_icon

    custom_class = kwargs.pop("custom_class", "")
    # Also accept 'class' as alias (but 'class' is a Python keyword,
    # so callers from Rust handlers can pass custom_class)
    return render_icon(
        name=name,
        size=size,
        icon_set=conditional_escape(set),
        custom_class=custom_class,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Theme Toggle (#138)
# ---------------------------------------------------------------------------


class ThemeToggleNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        current = kw.get("current", "system")
        event = kw.get("event", "")
        custom_class = kw.get("custom_class", "")

        e_event = conditional_escape(event) if event else ""
        e_cls = conditional_escape(custom_class)
        e_current = conditional_escape(current)

        # CSS classes
        cls = "dj-theme-toggle"
        if e_cls:
            cls += f" {e_cls}"

        # Build dj-click attribute if server-side persistence is desired
        click_attr = f' dj-click="{e_event}"' if e_event else ""

        # Icon SVGs for light/dark/system (rendered inline via render_icon)
        from djust.components.icons import render_icon

        sun_svg = render_icon("sun", size="sm")
        moon_svg = render_icon("moon", size="sm")
        monitor_svg = render_icon("computer-desktop", size="sm")

        # Generate a unique ID for this toggle instance
        toggle_id = f"dj-theme-toggle-{uuid.uuid4().hex[:8]}"

        return mark_safe(
            f'<div class="{cls}" id="{toggle_id}" '
            f'data-current="{e_current}"{click_attr} '
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


@register.tag("theme_toggle")
def do_theme_toggle(parser, token):
    """{% theme_toggle current="system" event="set_theme" %}"""
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ThemeToggleNode(kwargs)


# ---------------------------------------------------------------------------
# Page Header (#179)
# ---------------------------------------------------------------------------

_page_header_actions_key = "__page_header_actions__"


class PageHeaderActionsNode(template.Node):
    """Renders the actions slot inside a page header."""

    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        content = self.nodelist.render(context)
        # Stash the rendered actions content on the context for the parent
        context[_page_header_actions_key] = content
        return ""


class PageHeaderNode(template.Node):
    """Structured page-level header with title, optional subtitle/description,
    optional breadcrumb slot, and right-aligned action buttons area."""

    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        title = kw.get("title", "")
        subtitle = kw.get("subtitle", "")
        description = kw.get("description", "")
        custom_class = kw.get("custom_class", "")

        e_title = conditional_escape(str(title))
        e_subtitle = conditional_escape(str(subtitle))
        e_description = conditional_escape(str(description))
        e_custom_class = conditional_escape(str(custom_class))

        # Render child nodelist — this may include page_header_actions which
        # stashes its content in the context.
        context[_page_header_actions_key] = ""
        breadcrumb_content = self.nodelist.render(context)
        actions_html = context.get(_page_header_actions_key, "")

        cls = "dj-page-header"
        if e_custom_class:
            cls += f" {e_custom_class}"

        # Breadcrumb slot — any direct child content (not actions)
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

        # Actions
        actions_section = ""
        if actions_html.strip():
            actions_section = f'<div class="dj-page-header__actions">{actions_html}</div>'

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


@register.tag("page_header")
def do_page_header(parser, token):
    """{% page_header title="Products" subtitle="Manage inventory" %}...{% endpage_header %}"""
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endpage_header",))
    parser.delete_first_token()
    return PageHeaderNode(nodelist, kwargs)


@register.tag("page_header_actions")
def do_page_header_actions(parser, token):
    """{% page_header_actions %}...{% endpage_header_actions %}"""
    nodelist = parser.parse(("endpage_header_actions",))
    parser.delete_first_token()
    return PageHeaderActionsNode(nodelist)


# ---------------------------------------------------------------------------
# FORM ESSENTIALS (v1.5)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sidebar Nav (#86)
# ---------------------------------------------------------------------------


class SidebarItemNode(template.Node):
    """A single sidebar menu item."""

    def __init__(self, kwargs, nodelist):
        self.kwargs = kwargs
        self.nodelist = nodelist  # sub-items if nested

    def render(self, context):
        return ""  # rendered by parent SidebarNode


class SidebarSectionNode(template.Node):
    """A section header within a sidebar."""

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        return ""  # rendered by parent SidebarNode


class SidebarNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def _render_item(self, item, context, active_path, level=0):
        kw = {k: _resolve(v, context) for k, v in item.kwargs.items()}
        label = kw.get("label", "")
        href = kw.get("href", "#")
        icon = kw.get("icon", "")
        item_id = kw.get("id", "")
        event = kw.get("event", "")

        is_active = item_id == active_path or href == active_path
        active_cls = " dj-sidebar__item--active" if is_active else ""
        level_cls = f" dj-sidebar__item--level-{level}" if level > 0 else ""

        icon_html = ""
        if icon:
            icon_html = f'<span class="dj-sidebar__icon">{conditional_escape(icon)}</span>'

        # Check for nested sub-items
        sub_items = [n for n in item.nodelist if isinstance(n, SidebarItemNode)]

        if event:
            trigger = (
                f'<button class="dj-sidebar__link{active_cls}{level_cls}" '
                f'dj-click="{conditional_escape(event)}">'
                f'{icon_html}<span class="dj-sidebar__label">'
                f"{conditional_escape(label)}</span></button>"
            )
        else:
            trigger = (
                f'<a class="dj-sidebar__link{active_cls}{level_cls}" '
                f'href="{conditional_escape(href)}">'
                f'{icon_html}<span class="dj-sidebar__label">'
                f"{conditional_escape(label)}</span></a>"
            )

        if sub_items:
            children = "".join(
                self._render_item(si, context, active_path, level + 1) for si in sub_items
            )
            return (
                f'<li class="dj-sidebar__item dj-sidebar__item--parent">'
                f"{trigger}"
                f'<ul class="dj-sidebar__submenu">{children}</ul></li>'
            )

        return f'<li class="dj-sidebar__item">{trigger}</li>'

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        sidebar_id = kw.get("id", "sidebar")
        active = kw.get("active", "")
        collapsed = kw.get("collapsed", False)
        title = kw.get("title", "")
        toggle_event = kw.get("toggle_event", "toggle_sidebar")
        custom_class = kw.get("class", "")

        collapsed_cls = " dj-sidebar--collapsed" if collapsed else ""
        cls = f"dj-sidebar{collapsed_cls}"
        if custom_class:
            cls += f" {conditional_escape(custom_class)}"

        header_html = ""
        if title:
            header_html = (
                f'<div class="dj-sidebar__header">'
                f'<span class="dj-sidebar__title">{conditional_escape(title)}</span>'
                f'<button class="dj-sidebar__toggle" dj-click="{conditional_escape(toggle_event)}">'
                f"&#9776;</button></div>"
            )

        # Collect sections and items
        parts = []
        for node in self.nodelist:
            if isinstance(node, SidebarSectionNode):
                skw = {k: _resolve(v, context) for k, v in node.kwargs.items()}
                section_label = skw.get("label", "")
                parts.append(
                    f'<li class="dj-sidebar__section">'
                    f'<span class="dj-sidebar__section-label">'
                    f"{conditional_escape(section_label)}</span></li>"
                )
            elif isinstance(node, SidebarItemNode):
                parts.append(self._render_item(node, context, active))

        menu_html = f'<ul class="dj-sidebar__menu">{"".join(parts)}</ul>'

        # Mobile overlay backdrop
        backdrop = f'<div class="dj-sidebar__backdrop" dj-click="{conditional_escape(toggle_event)}"></div>'

        return mark_safe(
            f'<nav class="{cls}" id="{conditional_escape(sidebar_id)}" role="navigation">'
            f"{header_html}{menu_html}{backdrop}</nav>"
        )


@register.tag("sidebar")
def do_sidebar(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endsidebar",))
    parser.delete_first_token()
    return SidebarNode(nodelist, kwargs)


@register.tag("sidebar_item")
def do_sidebar_item(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endsidebar_item",))
    parser.delete_first_token()
    return SidebarItemNode(kwargs, nodelist)


@register.tag("sidebar_section")
def do_sidebar_section(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return SidebarSectionNode(kwargs)


# ---------------------------------------------------------------------------
# Navigation Menu (#90)
# ---------------------------------------------------------------------------


class NavItemNode(template.Node):
    """A single nav menu item, optionally containing dropdown children."""

    def __init__(self, kwargs, nodelist):
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context):
        return ""  # rendered by parent NavMenuNode


class NavMenuNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def _render_nav_item(self, item, context, active_path):
        kw = {k: _resolve(v, context) for k, v in item.kwargs.items()}
        label = kw.get("label", "")
        href = kw.get("href", "#")
        item_id = kw.get("id", "")
        event = kw.get("event", "")
        mega = kw.get("mega", False)

        is_active = item_id == active_path or href == active_path
        active_cls = " dj-nav__item--active" if is_active else ""

        # Check for sub-items (dropdown children)
        sub_items = [n for n in item.nodelist if isinstance(n, NavItemNode)]

        if sub_items:
            children = "".join(
                self._render_dropdown_item(si, context, active_path) for si in sub_items
            )
            mega_cls = " dj-nav__dropdown--mega" if mega else ""
            return (
                f'<li class="dj-nav__item dj-nav__item--has-dropdown{active_cls}">'
                f'<button class="dj-nav__link">{conditional_escape(label)}'
                f'<span class="dj-nav__caret">&#9662;</span></button>'
                f'<div class="dj-nav__dropdown{mega_cls}">'
                f'<ul class="dj-nav__dropdown-list">{children}</ul></div></li>'
            )

        if event:
            return (
                f'<li class="dj-nav__item{active_cls}">'
                f'<button class="dj-nav__link" dj-click="{conditional_escape(event)}">'
                f"{conditional_escape(label)}</button></li>"
            )

        return (
            f'<li class="dj-nav__item{active_cls}">'
            f'<a class="dj-nav__link" href="{conditional_escape(href)}">'
            f"{conditional_escape(label)}</a></li>"
        )

    def _render_dropdown_item(self, item, context, active_path):
        kw = {k: _resolve(v, context) for k, v in item.kwargs.items()}
        label = kw.get("label", "")
        href = kw.get("href", "#")
        desc = kw.get("description", "")
        event = kw.get("event", "")
        item_id = kw.get("id", "")

        is_active = item_id == active_path or href == active_path
        active_cls = " dj-nav__dropdown-item--active" if is_active else ""

        desc_html = ""
        if desc:
            desc_html = f'<span class="dj-nav__dropdown-desc">{conditional_escape(desc)}</span>'

        if event:
            return (
                f'<li class="dj-nav__dropdown-item{active_cls}">'
                f'<button class="dj-nav__dropdown-link" dj-click="{conditional_escape(event)}">'
                f"{conditional_escape(label)}{desc_html}</button></li>"
            )

        return (
            f'<li class="dj-nav__dropdown-item{active_cls}">'
            f'<a class="dj-nav__dropdown-link" href="{conditional_escape(href)}">'
            f"{conditional_escape(label)}{desc_html}</a></li>"
        )

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        nav_id = kw.get("id", "nav-menu")
        active = kw.get("active", "")
        brand = kw.get("brand", "")
        brand_href = kw.get("brand_href", "/")
        toggle_event = kw.get("toggle_event", "toggle_nav")
        mobile_open = kw.get("mobile_open", False)
        custom_class = kw.get("class", "")

        cls = "dj-nav"
        if custom_class:
            cls += f" {conditional_escape(custom_class)}"

        mobile_cls = " dj-nav__list--open" if mobile_open else ""

        brand_html = ""
        if brand:
            brand_html = (
                f'<a class="dj-nav__brand" href="{conditional_escape(brand_href)}">'
                f"{conditional_escape(brand)}</a>"
            )

        hamburger = (
            f'<button class="dj-nav__hamburger" dj-click="{conditional_escape(toggle_event)}" '
            f'aria-label="Toggle navigation">&#9776;</button>'
        )

        items = [n for n in self.nodelist if isinstance(n, NavItemNode)]
        items_html = "".join(self._render_nav_item(item, context, active) for item in items)

        return mark_safe(
            f'<nav class="{cls}" id="{conditional_escape(nav_id)}" role="navigation">'
            f'<div class="dj-nav__container">'
            f"{brand_html}{hamburger}"
            f'<ul class="dj-nav__list{mobile_cls}">{items_html}</ul>'
            f"</div></nav>"
        )


@register.tag("nav_menu")
def do_nav_menu(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endnav_menu",))
    parser.delete_first_token()
    return NavMenuNode(nodelist, kwargs)


@register.tag("nav_item")
def do_nav_item(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endnav_item",))
    parser.delete_first_token()
    return NavItemNode(kwargs, nodelist)


# ---------------------------------------------------------------------------
# App Shell (#167)
# ---------------------------------------------------------------------------


class AppSidebarNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return ""  # rendered by AppShellNode


class AppHeaderNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return ""  # rendered by AppShellNode


class AppContentNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return ""  # rendered by AppShellNode


class AppShellNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        shell_id = kw.get("id", "app-shell")
        sidebar_collapsed = kw.get("sidebar_collapsed", False)
        custom_class = kw.get("class", "")

        cls = "dj-app-shell"
        if sidebar_collapsed:
            cls += " dj-app-shell--sidebar-collapsed"
        if custom_class:
            cls += f" {conditional_escape(custom_class)}"

        # Find sub-nodes
        sidebar_nodes = [n for n in self.nodelist if isinstance(n, AppSidebarNode)]
        header_nodes = [n for n in self.nodelist if isinstance(n, AppHeaderNode)]
        content_nodes = [n for n in self.nodelist if isinstance(n, AppContentNode)]

        sidebar_html = ""
        if sidebar_nodes:
            sidebar_content = sidebar_nodes[0].nodelist.render(context)
            sidebar_html = f'<aside class="dj-app-shell__sidebar">{sidebar_content}</aside>'

        header_html = ""
        if header_nodes:
            header_content = header_nodes[0].nodelist.render(context)
            header_html = f'<header class="dj-app-shell__header">{header_content}</header>'

        content_html = ""
        if content_nodes:
            main_content = content_nodes[0].nodelist.render(context)
            content_html = f'<main class="dj-app-shell__content">{main_content}</main>'

        return mark_safe(
            f'<div class="{cls}" id="{conditional_escape(shell_id)}">'
            f"{sidebar_html}"
            f'<div class="dj-app-shell__main">'
            f"{header_html}{content_html}"
            f"</div></div>"
        )


@register.tag("app_shell")
def do_app_shell(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endapp_shell",))
    parser.delete_first_token()
    return AppShellNode(nodelist, kwargs)


@register.tag("app_sidebar")
def do_app_sidebar(parser, token):
    nodelist = parser.parse(("endapp_sidebar",))
    parser.delete_first_token()
    return AppSidebarNode(nodelist)


@register.tag("app_header")
def do_app_header(parser, token):
    nodelist = parser.parse(("endapp_header",))
    parser.delete_first_token()
    return AppHeaderNode(nodelist)


@register.tag("app_content")
def do_app_content(parser, token):
    nodelist = parser.parse(("endapp_content",))
    parser.delete_first_token()
    return AppContentNode(nodelist)


# ---------------------------------------------------------------------------
# Toolbar (#87)
# ---------------------------------------------------------------------------


class ToolbarSeparatorNode(template.Node):
    def render(self, context):
        return ""  # rendered by ToolbarNode


class ToolbarOverflowNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return ""  # rendered by ToolbarNode


class ToolbarNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        toolbar_id = kw.get("id", f"toolbar-{uuid.uuid4().hex[:8]}")
        custom_class = kw.get("class", "")
        size = kw.get("size", "md")
        variant = kw.get("variant", "default")

        cls = f"dj-toolbar dj-toolbar--{conditional_escape(size)} dj-toolbar--{conditional_escape(variant)}"
        if custom_class:
            cls += f" {conditional_escape(custom_class)}"

        parts = []
        for node in self.nodelist:
            if isinstance(node, ToolbarSeparatorNode):
                parts.append('<div class="dj-toolbar__separator" role="separator"></div>')
            elif isinstance(node, ToolbarOverflowNode):
                overflow_content = node.nodelist.render(context)
                parts.append(
                    f'<div class="dj-toolbar__overflow">'
                    f'<button class="dj-toolbar__overflow-trigger" aria-label="More actions" '
                    f'aria-expanded="false" aria-haspopup="true">'
                    f'<span class="dj-toolbar__overflow-icon">&#8942;</span></button>'
                    f'<div class="dj-toolbar__overflow-menu">{overflow_content}</div></div>'
                )
            else:
                rendered = node.render(context)
                if rendered.strip():
                    parts.append(f'<div class="dj-toolbar__group">{rendered}</div>')

        return mark_safe(
            f'<div class="{cls}" id="{conditional_escape(toolbar_id)}" role="toolbar">'
            f"{''.join(parts)}</div>"
        )


@register.tag("toolbar")
def do_toolbar(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endtoolbar",))
    parser.delete_first_token()
    return ToolbarNode(nodelist, kwargs)


@register.tag("toolbar_separator")
def do_toolbar_separator(parser, token):
    return ToolbarSeparatorNode()


@register.tag("toolbar_overflow")
def do_toolbar_overflow(parser, token):
    nodelist = parser.parse(("endtoolbar_overflow",))
    parser.delete_first_token()
    return ToolbarOverflowNode(nodelist)


# ---------------------------------------------------------------------------
# Inline Edit (#88)
# ---------------------------------------------------------------------------


class InlineEditNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        value = kw.get("value", "")
        event = kw.get("event", "inline_edit")
        field = kw.get("field", "")
        input_type = kw.get("type", "text")
        placeholder = kw.get("placeholder", "")
        custom_class = kw.get("class", "")
        editing = kw.get("editing", False)

        e_value = conditional_escape(str(value))
        e_event = conditional_escape(event)
        e_field = conditional_escape(field)
        e_placeholder = conditional_escape(placeholder)
        e_input_type = conditional_escape(input_type)

        cls = "dj-inline-edit"
        if editing:
            cls += " dj-inline-edit--editing"
        if custom_class:
            cls += f" {conditional_escape(custom_class)}"

        if editing:
            return mark_safe(
                f'<span class="{cls}">'
                f'<input class="dj-inline-edit__input" type="{e_input_type}" '
                f'value="{e_value}" placeholder="{e_placeholder}" '
                f'data-field="{e_field}" '
                f'dj-keydown.enter="{e_event}" '
                f'dj-blur="{e_event}" '
                f'dj-keydown.escape="inline_edit_cancel" '
                f"autofocus></span>"
            )
        else:
            return mark_safe(
                f'<span class="{cls}" dj-click="inline_edit_start" '
                f'data-field="{e_field}" title="Click to edit">'
                f'<span class="dj-inline-edit__display">{e_value}</span>'
                f'<span class="dj-inline-edit__icon">&#9998;</span></span>'
            )


@register.tag("inline_edit")
def do_inline_edit(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return InlineEditNode(kwargs)


# ---------------------------------------------------------------------------
# Filter Bar (#166)
# ---------------------------------------------------------------------------


class FilterSelectNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        return ""  # rendered by FilterBarNode


class FilterDateRangeNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        return ""  # rendered by FilterBarNode


class FilterSearchNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        return ""  # rendered by FilterBarNode


class FilterBarNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        bar_id = kw.get("id", f"filter-bar-{uuid.uuid4().hex[:8]}")
        event = kw.get("event", "filter_change")
        custom_class = kw.get("class", "")
        clear_event = kw.get("clear_event", "filter_clear")

        cls = "dj-filter-bar"
        if custom_class:
            cls += f" {conditional_escape(custom_class)}"

        e_event = conditional_escape(event)
        e_clear = conditional_escape(clear_event)

        filter_nodes = [
            n
            for n in self.nodelist
            if isinstance(n, (FilterSelectNode, FilterDateRangeNode, FilterSearchNode))
        ]

        parts = []
        has_values = False
        for node in filter_nodes:
            nkw = {k: _resolve(v, context) for k, v in node.kwargs.items()}
            if isinstance(node, FilterSelectNode):
                name = conditional_escape(nkw.get("name", ""))
                label = conditional_escape(nkw.get("label", name))
                options = nkw.get("options", [])
                value = nkw.get("value", "")
                if value:
                    has_values = True
                opt_html = f'<option value="">{label}</option>'
                if isinstance(options, list):
                    for opt in options:
                        if isinstance(opt, dict):
                            ov = conditional_escape(str(opt.get("value", "")))
                            ol = conditional_escape(str(opt.get("label", ov)))
                        else:
                            ov = conditional_escape(str(opt))
                            ol = ov
                        selected = (
                            " selected"
                            if str(opt.get("value", opt) if isinstance(opt, dict) else opt)
                            == str(value)
                            else ""
                        )
                        opt_html += f'<option value="{ov}"{selected}>{ol}</option>'
                parts.append(
                    f'<div class="dj-filter-bar__control dj-filter-bar__select-wrap">'
                    f'<select class="dj-filter-bar__select" name="{name}" '
                    f'dj-change="{e_event}">{opt_html}</select></div>'
                )
            elif isinstance(node, FilterDateRangeNode):
                name = conditional_escape(nkw.get("name", ""))
                label = conditional_escape(nkw.get("label", name))
                value_start = conditional_escape(str(nkw.get("start", "")))
                value_end = conditional_escape(str(nkw.get("end", "")))
                if value_start or value_end:
                    has_values = True
                parts.append(
                    f'<div class="dj-filter-bar__control dj-filter-bar__date-range">'
                    f'<label class="dj-filter-bar__label">{label}</label>'
                    f'<input class="dj-filter-bar__date" type="date" name="{name}_start" '
                    f'value="{value_start}" dj-change="{e_event}">'
                    f'<span class="dj-filter-bar__date-sep">&ndash;</span>'
                    f'<input class="dj-filter-bar__date" type="date" name="{name}_end" '
                    f'value="{value_end}" dj-change="{e_event}"></div>'
                )
            elif isinstance(node, FilterSearchNode):
                name = conditional_escape(nkw.get("name", ""))
                placeholder = conditional_escape(nkw.get("placeholder", "Search\u2026"))
                value = conditional_escape(str(nkw.get("value", "")))
                debounce = nkw.get("debounce", 300)
                if value:
                    has_values = True
                parts.append(
                    f'<div class="dj-filter-bar__control dj-filter-bar__search-wrap">'
                    f'<input class="dj-filter-bar__search" type="search" name="{name}" '
                    f'placeholder="{placeholder}" value="{value}" '
                    f'dj-input="{e_event}" dj-debounce="{int(debounce)}"></div>'
                )

        clear_html = ""
        if has_values:
            clear_html = (
                f'<div class="dj-filter-bar__actions">'
                f'<button class="dj-filter-bar__clear" dj-click="{e_clear}">Clear filters</button></div>'
            )

        return mark_safe(
            f'<div class="{cls}" id="{conditional_escape(bar_id)}" role="search">'
            f'<div class="dj-filter-bar__controls">{"".join(parts)}</div>'
            f"{clear_html}</div>"
        )


@register.tag("filter_bar")
def do_filter_bar(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endfilter_bar",))
    parser.delete_first_token()
    return FilterBarNode(nodelist, kwargs)


@register.tag("filter_select")
def do_filter_select(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return FilterSelectNode(kwargs)


@register.tag("filter_date_range")
def do_filter_date_range(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return FilterDateRangeNode(kwargs)


@register.tag("filter_search")
def do_filter_search(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return FilterSearchNode(kwargs)


# ---------------------------------------------------------------------------
# Avatar Group
# ---------------------------------------------------------------------------


class AvatarGroupNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        users = kw.get("users", [])
        max_display = int(kw.get("max", 5))
        size = kw.get("size", "md")
        custom_class = kw.get("class", "")

        e_size = conditional_escape(str(size))
        e_class = conditional_escape(str(custom_class))

        visible = users[:max_display]
        overflow = len(users) - max_display

        avatars_html = []
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
                avatars_html.append(
                    f'<span class="dj-avatar-group__item" title="{e_name}" style="z-index:{z}">'
                    f'<img src="{e_src}" alt="{e_name}" class="dj-avatar-group__img">'
                    f"</span>"
                )
            else:
                avatars_html.append(
                    f'<span class="dj-avatar-group__item dj-avatar-group__initials" '
                    f'title="{e_name}" style="z-index:{z}">{initials}</span>'
                )

        overflow_html = ""
        if overflow > 0:
            overflow_html = (
                f'<span class="dj-avatar-group__item dj-avatar-group__overflow">+{overflow}</span>'
            )

        cls = f"dj-avatar-group dj-avatar-group--{e_size}"
        if e_class:
            cls += f" {e_class}"
        return mark_safe(f'<div class="{cls}">{"".join(avatars_html)}{overflow_html}</div>')


@register.tag("avatar_group")
def do_avatar_group(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return AvatarGroupNode(kwargs)


# ---------------------------------------------------------------------------
# Hover Card
# ---------------------------------------------------------------------------


class HoverCardNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        trigger = kw.get("trigger", "")
        position = kw.get("position", "bottom")
        delay_in = kw.get("delay_in", 200)
        delay_out = kw.get("delay_out", 300)
        custom_class = kw.get("class", "")

        e_trigger = conditional_escape(str(trigger))
        e_position = conditional_escape(str(position))
        e_class = conditional_escape(str(custom_class))

        content = self.nodelist.render(context)

        cls = f"dj-hover-card dj-hover-card--{e_position}"
        if e_class:
            cls += f" {e_class}"
        return mark_safe(
            f'<span class="{cls}" data-delay-in="{int(delay_in)}" '
            f'data-delay-out="{int(delay_out)}">'
            f'<span class="dj-hover-card__trigger" tabindex="0">{e_trigger}</span>'
            f'<div class="dj-hover-card__content">{content}</div>'
            f"</span>"
        )


@register.tag("hover_card")
def do_hover_card(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endhover_card",))
    parser.delete_first_token()
    return HoverCardNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Notification Popover
# ---------------------------------------------------------------------------


class NotificationPopoverNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        notifications = kw.get("notifications", [])
        unread_count = int(kw.get("unread_count", 0))
        mark_read_event = kw.get("mark_read_event", "mark_read")
        toggle_event = kw.get("toggle_event", "toggle_notifications")
        is_open = kw.get("open", False)
        custom_class = kw.get("class", "")
        title = kw.get("title", "Notifications")

        e_mark_read = conditional_escape(str(mark_read_event))
        e_toggle = conditional_escape(str(toggle_event))
        e_class = conditional_escape(str(custom_class))
        e_title = conditional_escape(str(title))

        badge_html = ""
        if unread_count > 0:
            display = "99+" if unread_count > 99 else str(unread_count)
            badge_html = f'<span class="dj-notif-popover__badge">{display}</span>'

        open_attr = " data-open" if is_open else ""
        cls = "dj-notif-popover"
        if e_class:
            cls += f" {e_class}"

        bell_html = (
            f'<button class="dj-notif-popover__bell" dj-click="{e_toggle}" '
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
                mark_attr = f' dj-click="{e_mark_read}" data-id="{e_n_id}"'
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
                f'<div class="dj-notif-popover__header">{e_title}</div>'
                f"{''.join(items_html)}{empty}"
                f"</div>"
            )

        return mark_safe(f'<div class="{cls}"{open_attr}>{bell_html}{panel_html}</div>')


@register.tag("notification_popover")
def do_notification_popover(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return NotificationPopoverNode(kwargs)


# ---------------------------------------------------------------------------
# AI Chat: Conversation Thread
# ---------------------------------------------------------------------------


class ConversationThreadNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("conversation_thread")
def do_conversation_thread(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ConversationThreadNode(kwargs)


# ---------------------------------------------------------------------------
# AI Chat: Thinking Indicator
# ---------------------------------------------------------------------------


class ThinkingIndicatorNode(template.Node):
    VALID_STATUSES = {"thinking", "searching", "generating", "tool_use", "idle"}

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("thinking_indicator")
def do_thinking_indicator(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ThinkingIndicatorNode(kwargs)


# ---------------------------------------------------------------------------
# AI Chat: Multimodal Input
# ---------------------------------------------------------------------------


class MultimodalInputNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("multimodal_input")
def do_multimodal_input(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return MultimodalInputNode(kwargs)


# ---------------------------------------------------------------------------
# AI Chat: Feedback Widget
# ---------------------------------------------------------------------------


class FeedbackWidgetNode(template.Node):
    VALID_MODES = {"thumbs", "stars", "emoji"}

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("feedback")
def do_feedback(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return FeedbackWidgetNode(kwargs)


# ---------------------------------------------------------------------------
# AI Trust: Approval Gate
# ---------------------------------------------------------------------------


class ApprovalGateNode(template.Node):
    VALID_RISKS = {"low", "medium", "high", "critical"}

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("approval_gate")
def do_approval_gate(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ApprovalGateNode(kwargs)


# ---------------------------------------------------------------------------
# AI Trust: Source Citation
# ---------------------------------------------------------------------------


class SourceCitationNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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
                # Relevance is optional; skip if not coercible to float.
                pass

        popover_html = "".join(popover_parts)

        return mark_safe(
            f'<span class="{cls}" tabindex="0">'
            f'<sup class="dj-citation__marker">[{idx}]</sup>'
            f'<span class="dj-citation__popover">{popover_html}</span>'
            f"</span>"
        )


@register.tag("source_citation")
def do_source_citation(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return SourceCitationNode(kwargs)


# ---------------------------------------------------------------------------
# AI Trust: Model Selector
# ---------------------------------------------------------------------------


class ModelSelectorNode(template.Node):
    TIER_LABELS = {
        "free": "Free",
        "standard": "Standard",
        "premium": "Premium",
        "enterprise": "Enterprise",
    }

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("model_selector")
def do_model_selector(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ModelSelectorNode(kwargs)


# ---------------------------------------------------------------------------
# AI Trust: Token Counter
# ---------------------------------------------------------------------------


class TokenCounterNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("token_counter")
def do_token_counter(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return TokenCounterNode(kwargs)


# ---------------------------------------------------------------------------
# Collaboration: Chat Bubble
# ---------------------------------------------------------------------------


class ChatBubbleNode(template.Node):
    VALID_STATUSES = {"sending", "sent", "delivered", "read", "error"}

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        # Avatar
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

        # Status
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

        # Header
        header_html = ""
        if e_name or e_time:
            name_part = f'<span class="dj-bubble__name">{e_name}</span>' if e_name else ""
            time_part = f'<span class="dj-bubble__time">{e_time}</span>' if e_time else ""
            header_html = f'<div class="dj-bubble__header">{name_part}{time_part}</div>'

        # Footer
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


@register.tag("chat_bubble")
def do_chat_bubble(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ChatBubbleNode(kwargs)


# ---------------------------------------------------------------------------
# Collaboration: Presence Avatars
# ---------------------------------------------------------------------------


class PresenceAvatarsNode(template.Node):
    VALID_STATUSES = {"online", "away", "busy", "offline"}

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("presence_avatars")
def do_presence_avatars(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return PresenceAvatarsNode(kwargs)


# ---------------------------------------------------------------------------
# Collaboration: Mentions Input
# ---------------------------------------------------------------------------


class MentionsInputNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        # Render suggestion items
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


@register.tag("mentions_input")
def do_mentions_input(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return MentionsInputNode(kwargs)


# ---------------------------------------------------------------------------
# Expandable Text (#118)
# ---------------------------------------------------------------------------


class ExpandableTextNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        max_lines = int(kw.get("max_lines", 3))
        expanded = kw.get("expanded", False)
        toggle_event = kw.get("toggle_event", "toggle_expand")
        more_label = kw.get("more_label", "Read more")
        less_label = kw.get("less_label", "Show less")
        custom_class = kw.get("class", "")

        content = self.nodelist.render(context)
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


@register.tag("expandable_text")
def do_expandable_text(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endexpandable_text",))
    parser.delete_first_token()
    return ExpandableTextNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Truncated List (#150)
# ---------------------------------------------------------------------------


class TruncatedListNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("truncated_list")
def do_truncated_list(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return TruncatedListNode(kwargs)


# ---------------------------------------------------------------------------
# Inline Markdown Preview (#169)
# ---------------------------------------------------------------------------


class MarkdownTextareaNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("markdown_textarea")
def do_markdown_textarea(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return MarkdownTextareaNode(kwargs)


# ---------------------------------------------------------------------------
# Skeleton Factory (#144)
# ---------------------------------------------------------------------------


class SkeletonForNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("skeleton_for")
def do_skeleton_for(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return SkeletonForNode(kwargs)


# ---------------------------------------------------------------------------
# Content Loader / Suspense (#152)
# ---------------------------------------------------------------------------


class AwaitNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        # Render child nodes — for loaded state these are the actual content,
        # for loading state they are the placeholder (e.g. skeleton_for)
        inner = self.nodelist.render(context)

        if loaded:
            return mark_safe(
                f'<div class="{cls}" data-loading-event="{e_event}">'
                f'<div class="dj-content-loader__content">{inner}</div>'
                f"</div>"
            )

        return mark_safe(
            f'<div class="{cls}" data-loading-event="{e_event}" '
            f'role="status" aria-label="Loading">'
            f'<div class="dj-content-loader__placeholder">{inner}</div>'
            f"</div>"
        )


@register.tag("await")
def do_await(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endawait",))
    parser.delete_first_token()
    return AwaitNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Time Picker
# ---------------------------------------------------------------------------


class TimePickerNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        # Parse time
        hour, minute = 0, 0
        if value:
            parts = str(value).split(":")
            try:
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                # Malformed time string; fall back to hour=0, minute=0 defaults above.
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

        # Hour select
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

        # Minute select
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

        # AM/PM toggle
        if not format_24h:
            is_pm = hour >= 12
            parts_html.append(
                f'<select class="dj-time-picker__period" aria-label="AM/PM"{disabled_attr}>'
                f'<option value="AM"{"" if is_pm else " selected"}>AM</option>'
                f'<option value="PM"{" selected" if is_pm else ""}>PM</option>'
                f"</select>"
            )

        parts_html.append("</div>")

        return mark_safe(f'<div class="{class_str}">{"".join(parts_html)}</div>')


@register.tag("time_picker")
def do_time_picker(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return TimePickerNode(kwargs)


# ---------------------------------------------------------------------------
# Wizard / Multi-step Form
# ---------------------------------------------------------------------------


class WizardNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        # Find active index
        active_idx = 0
        for i, step in enumerate(steps):
            if isinstance(step, dict) and step.get("id") == active:
                active_idx = i
                break

        # Step indicators
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

            number_html = ""
            if show_numbers:
                number_html = f'<span class="dj-wizard__number">{i + 1}</span>'

            indicators.append(
                f'<button class="{step_cls}" '
                f'dj-click="{e_event}" data-value="{step_id}">'
                f"{number_html}"
                f'<span class="dj-wizard__label">{step_label}</span></button>'
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

        content = self.nodelist.render(context)

        return mark_safe(
            f'<div class="{class_str}">{nav}<div class="dj-wizard__body">{content}</div></div>'
        )


@register.tag("wizard")
def do_wizard(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endwizard",))
    parser.delete_first_token()
    return WizardNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Bottom Sheet
# ---------------------------------------------------------------------------


class BottomSheetNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        content = self.nodelist.render(context)

        title_html = ""
        if title:
            title_html = f'<h3 class="dj-bottom-sheet__title">{e_title}</h3>'

        return mark_safe(
            f'<div class="dj-bottom-sheet__backdrop" dj-click="{e_close}">'
            f'<div class="{class_str}" onclick="event.stopPropagation()">'
            f'<div class="dj-bottom-sheet__handle"><div class="dj-bottom-sheet__handle-bar"></div></div>'
            f'<div class="dj-bottom-sheet__header">'
            f"{title_html}"
            f'<button class="dj-bottom-sheet__close" dj-click="{e_close}">&times;</button>'
            f"</div>"
            f'<div class="dj-bottom-sheet__body">{content}</div>'
            f"</div></div>"
        )


@register.tag("bottom_sheet")
def do_bottom_sheet(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endbottom_sheet",))
    parser.delete_first_token()
    return BottomSheetNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Infinite Scroll
# ---------------------------------------------------------------------------


class InfiniteScrollNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        content = self.nodelist.render(context)

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
            f'<div class="dj-infinite-scroll__content">{content}</div>'
            f"{sentinel}</div>"
        )


@register.tag("infinite_scroll")
def do_infinite_scroll(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endinfinite_scroll",))
    parser.delete_first_token()
    return InfiniteScrollNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Countdown / Timer
# ---------------------------------------------------------------------------


class CountdownNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        target = kw.get("target", "")
        event = kw.get("event", "")
        show_days = kw.get("show_days", True)
        show_seconds = kw.get("show_seconds", True)
        labels = kw.get("labels", {})
        custom_class = kw.get("class", "")

        e_target = conditional_escape(str(target))
        e_event = conditional_escape(str(event)) if event else ""
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-countdown"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        event_attr = f' data-event="{e_event}"' if e_event else ""

        default_labels = {
            "days": "Days",
            "hours": "Hours",
            "minutes": "Minutes",
            "seconds": "Seconds",
        }
        if isinstance(labels, dict):
            merged = {**default_labels, **labels}
        else:
            merged = default_labels

        segments = []
        if show_days:
            segments.append(
                f'<div class="dj-countdown__segment">'
                f'<span class="dj-countdown__value" data-unit="days">00</span>'
                f'<span class="dj-countdown__label">{conditional_escape(merged["days"])}</span></div>'
            )
        segments.append(
            f'<div class="dj-countdown__segment">'
            f'<span class="dj-countdown__value" data-unit="hours">00</span>'
            f'<span class="dj-countdown__label">{conditional_escape(merged["hours"])}</span></div>'
        )
        segments.append(
            f'<div class="dj-countdown__segment">'
            f'<span class="dj-countdown__value" data-unit="minutes">00</span>'
            f'<span class="dj-countdown__label">{conditional_escape(merged["minutes"])}</span></div>'
        )
        if show_seconds:
            segments.append(
                f'<div class="dj-countdown__segment">'
                f'<span class="dj-countdown__value" data-unit="seconds">00</span>'
                f'<span class="dj-countdown__label">{conditional_escape(merged["seconds"])}</span></div>'
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


@register.tag("countdown")
def do_countdown(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return CountdownNode(kwargs)


# ---------------------------------------------------------------------------
# Cookie Consent Banner
# ---------------------------------------------------------------------------


class CookieConsentNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        content = self.nodelist.render(context) if self.nodelist else ""

        msg_text = content.strip() if content.strip() else e_msg

        privacy_html = ""
        if privacy_url:
            e_url = conditional_escape(str(privacy_url))
            privacy_html = f' <a href="{e_url}" class="dj-cookie-consent__link">Privacy Policy</a>'

        buttons = [
            f'<button class="dj-cookie-consent__accept" '
            f'dj-click="{e_accept}">{e_accept_label}</button>'
        ]

        if show_reject and reject_event:
            e_reject = conditional_escape(str(reject_event))
            e_reject_label = conditional_escape(str(reject_label))
            buttons.append(
                f'<button class="dj-cookie-consent__reject" '
                f'dj-click="{e_reject}">{e_reject_label}</button>'
            )

        return mark_safe(
            f'<div class="{class_str}" role="banner" aria-label="Cookie consent">'
            f'<p class="dj-cookie-consent__message">{msg_text}{privacy_html}</p>'
            f'<div class="dj-cookie-consent__actions">{"".join(buttons)}</div>'
            f"</div>"
        )


@register.tag("cookie_consent")
def do_cookie_consent(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endcookie_consent",))
    parser.delete_first_token()
    return CookieConsentNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Form Array
# ---------------------------------------------------------------------------


class FormArrayNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        # Render template content for each row, or default inputs.
        #
        # If the `{% form_array %}...{% endform_array %}` block contains inner
        # template markup, that nodelist is rendered once per row with the
        # loop variables ``row``, ``forloop`` and ``row_index`` pushed onto the
        # context stack. This lets callers customize per-row layout, e.g.::
        #
        #     {% form_array name="items" rows=rows %}
        #       <input name="items[{{ row_index }}][label]" value="{{ row.label }}">
        #       <input name="items[{{ row_index }}][value]" value="{{ row.value }}">
        #     {% endform_array %}
        #
        # If the nodelist is empty (self-closing-style usage), fall back to the
        # single-input-per-row default rendering.
        has_inner = bool(self.nodelist) and any(
            not (isinstance(n, template.base.TextNode) and not n.s.strip()) for n in self.nodelist
        )
        rows_html = []
        for i, row in enumerate(rows):
            remove_html = ""
            if can_remove:
                remove_html = (
                    f'<button class="dj-form-array__remove" type="button" '
                    f'dj-click="{e_remove_event}" data-value="{i}" '
                    f'aria-label="Remove row {i + 1}">&times;</button>'
                )

            if has_inner:
                # Render the inner block with per-row context
                row_ctx = {
                    "row": row,
                    "row_index": i,
                    "forloop": {
                        "counter": i + 1,
                        "counter0": i,
                        "first": i == 0,
                        "last": i == len(rows) - 1,
                    },
                }
                with context.push(**row_ctx):
                    inner_html = self.nodelist.render(context)
                rows_html.append(
                    f'<div class="dj-form-array__row" data-index="{i}">'
                    f"{inner_html}{remove_html}</div>"
                )
            else:
                val = conditional_escape(
                    str(row.get("value", "") if isinstance(row, dict) else row)
                )
                rows_html.append(
                    f'<div class="dj-form-array__row" data-index="{i}">'
                    f'<input type="text" name="{e_name}[{i}]" value="{val}" '
                    f'class="dj-form-array__input">'
                    f"{remove_html}</div>"
                )

        add_disabled = "" if can_add else " disabled"
        add_html = (
            f'<button class="dj-form-array__add" type="button" '
            f'dj-click="{e_add_event}"{add_disabled}>'
            f"{e_add_label}</button>"
        )

        return mark_safe(
            f'<div class="{class_str}" data-min="{min_rows}" data-max="{max_rows}">'
            f'<div class="dj-form-array__rows">{"".join(rows_html)}</div>'
            f"{add_html}</div>"
        )


@register.tag("form_array")
def do_form_array(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endform_array",))
    parser.delete_first_token()
    return FormArrayNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Scroll Spy
# ---------------------------------------------------------------------------


class ScrollSpyNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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
                f'<a href="#{e_id}" '
                f'class="dj-scroll-spy__item{active_cls}" '
                f'data-section="{e_id}">{e_label}</a>'
            )

        return mark_safe(
            f'<nav class="{class_str}" dj-hook="ScrollSpy" '
            f'data-sections="{sections_json}" '
            f'data-event="{e_event}" data-offset="{e_offset}" '
            f'role="navigation" aria-label="Section navigation">'
            f"{''.join(nav_items)}</nav>"
        )


@register.tag("scroll_spy")
def do_scroll_spy(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ScrollSpyNode(kwargs)


# ---------------------------------------------------------------------------
# Page Alert / Banner
# ---------------------------------------------------------------------------


class PageAlertNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        content = self.nodelist.render(context)

        icon_html = ""
        if icon:
            icon_html = f'<span class="dj-page-alert__icon">{conditional_escape(str(icon))}</span>'

        dismiss_html = ""
        if dismissible:
            e_dismiss = conditional_escape(str(dismiss_event))
            dismiss_html = (
                f'<button class="dj-page-alert__dismiss" '
                f'dj-click="{e_dismiss}" aria-label="Dismiss">&times;</button>'
            )

        return mark_safe(
            f'<div class="{class_str}" role="alert">'
            f"{icon_html}"
            f'<span class="dj-page-alert__message">{content}</span>'
            f"{dismiss_html}</div>"
        )


@register.tag("page_alert")
def do_page_alert(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endpage_alert",))
    parser.delete_first_token()
    return PageAlertNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Dropdown Menu
# ---------------------------------------------------------------------------


class DropdownMenuNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)
        dm_open_attr = " data-open" if is_open else ""

        trigger = (
            f'<button class="dj-dropdown-menu__trigger" '
            f'dj-click="{e_toggle}" '
            f'aria-expanded="{"true" if is_open else "false"}" '
            f'aria-haspopup="true">{e_label}</button>'
        )

        if not is_open:
            return mark_safe(f'<div class="{class_str}">{trigger}</div>')
        # When open, dm_open_attr is set above

        if not isinstance(items, list):
            items = []

        # Render nodelist children (menu_item / menu_divider tags)
        menu_child_nodes = [
            n for n in self.nodelist if isinstance(n, (MenuItemNode, MenuDividerNode))
        ]

        menu_items = []
        if menu_child_nodes:
            for node in menu_child_nodes:
                menu_items.append(node.render(context))
        else:
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
                    f'<button class="{item_cls}" role="menuitem"'
                    f"{event_attr}{disabled_attr}>"
                    f"{icon_html}{e_item_label}</button>"
                )

        menu = (
            f'<div class="dj-dropdown-menu__content dj-dropdown-menu--{conditional_escape(str(align))}" '
            f'role="menu">{"".join(menu_items)}</div>'
        )

        return mark_safe(f'<div class="{class_str}"{dm_open_attr}>{trigger}{menu}</div>')


class MenuItemNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        label = kw.get("label", "")
        event = kw.get("event", "")
        danger = kw.get("danger", False)
        disabled = kw.get("disabled", False)
        icon = kw.get("icon", "")

        item_cls = "dj-dropdown-menu__item"
        if danger:
            item_cls += " dj-dropdown-menu__item--danger"
        if disabled:
            item_cls += " dj-dropdown-menu__item--disabled"

        e_label = conditional_escape(str(label))
        e_event = conditional_escape(str(event))
        disabled_attr = " disabled" if disabled else ""
        event_attr = f' dj-click="{e_event}"' if event else ""

        icon_html = ""
        if icon:
            icon_html = (
                f'<span class="dj-dropdown-menu__icon">{conditional_escape(str(icon))}</span>'
            )

        return (
            f'<button class="{item_cls}" role="menuitem"'
            f"{event_attr}{disabled_attr}>"
            f"{icon_html}{e_label}</button>"
        )


class MenuDividerNode(template.Node):
    def render(self, context):
        return '<hr class="dj-dropdown-menu__divider" role="separator">'


@register.tag("dropdown_menu")
def do_dropdown_menu(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("enddropdown_menu",))
    parser.delete_first_token()
    return DropdownMenuNode(nodelist, kwargs)


@register.tag("menu_item")
def do_menu_item(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return MenuItemNode(kwargs)


@register.tag("menu_divider")
def do_menu_divider(parser, token):
    return MenuDividerNode()


# ---------------------------------------------------------------------------
# Meter / Stacked Progress
# ---------------------------------------------------------------------------


class MeterNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        label_html = ""
        if label:
            label_html = f'<div class="dj-meter__label">{conditional_escape(str(label))}</div>'

        bar_parts = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            val = seg.get("value", 0)
            try:
                val = float(val)
            except (ValueError, TypeError):
                val = 0
            if total > 0:
                pct = min(100, max(0, (val / total) * 100))
            else:
                pct = 0
            color = conditional_escape(str(seg.get("color", "")))
            seg_label = conditional_escape(str(seg.get("label", "")))
            style = f"width:{pct:.1f}%"
            if color:
                style += f";background:{color}"
            bar_parts.append(
                f'<div class="dj-meter__segment" style="{style}" '
                f'role="meter" aria-valuenow="{int(val)}" '
                f'aria-valuemin="0" aria-valuemax="{total}" '
                f'aria-label="{seg_label}"></div>'
            )

        bar = f'<div class="dj-meter__bar">{"".join(bar_parts)}</div>'

        legend_html = ""
        if show_legend and segments:
            legend_items = []
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                color = conditional_escape(str(seg.get("color", "")))
                seg_label = conditional_escape(str(seg.get("label", "")))
                val = seg.get("value", 0)
                swatch_style = f"background:{color}" if color else ""
                legend_items.append(
                    f'<div class="dj-meter__legend-item">'
                    f'<span class="dj-meter__legend-swatch" style="{swatch_style}"></span>'
                    f'<span class="dj-meter__legend-label">{seg_label}</span>'
                    f'<span class="dj-meter__legend-value">{val}</span></div>'
                )
            legend_html = f'<div class="dj-meter__legend">{"".join(legend_items)}</div>'

        return mark_safe(f'<div class="{class_str}">{label_html}{bar}{legend_html}</div>')


@register.tag("meter")
def do_meter(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return MeterNode(kwargs)


# ---------------------------------------------------------------------------
# Export Dialog
# ---------------------------------------------------------------------------


class ExportDialogNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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
        format_section = (
            f'<div class="dj-export-dialog__formats">'
            f'<h4 class="dj-export-dialog__section-title">Format</h4>'
            f"{''.join(format_options)}</div>"
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
        col_section = (
            f'<div class="dj-export-dialog__columns">'
            f'<h4 class="dj-export-dialog__section-title">Columns</h4>'
            f"{''.join(col_options)}</div>"
        )

        return mark_safe(
            f'<div class="dj-export-dialog__backdrop" dj-click="{e_close}">'
            f'<div class="{class_str}" onclick="event.stopPropagation()">'
            f'<div class="dj-export-dialog__header">'
            f"<h3>{e_title}</h3>"
            f'<button class="dj-export-dialog__close" dj-click="{e_close}">&times;</button></div>'
            f'<div class="dj-export-dialog__body">{format_section}{col_section}</div>'
            f'<div class="dj-export-dialog__footer">'
            f'<button class="dj-export-dialog__cancel" dj-click="{e_close}">Cancel</button>'
            f'<button class="dj-export-dialog__submit" dj-click="{e_event}">Export</button>'
            f"</div></div></div>"
        )


@register.tag("export_dialog")
def do_export_dialog(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ExportDialogNode(kwargs)


# ---------------------------------------------------------------------------
# Import Wizard
# ---------------------------------------------------------------------------


class ImportWizardNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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
                f'<span class="dj-import-wizard__step-label">'
                f"{step_labels[s]}</span></div>"
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
                f'<p class="dj-import-wizard__formats">Accepted: {e_formats}</p>'
                f"</div></div>"
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
                f'<button class="dj-import-wizard__import-btn" '
                f'dj-click="{e_event}">Import</button></div>'
            )

        return mark_safe(f'<div class="{class_str}">{nav}{body}</div>')


@register.tag("import_wizard")
def do_import_wizard(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ImportWizardNode(kwargs)


# ---------------------------------------------------------------------------
# Audit Log Table
# ---------------------------------------------------------------------------


class AuditLogNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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
            e_stream = conditional_escape(str(stream_event))
            stream_attr = f' data-stream-event="{e_stream}"'

        col_labels = {
            "timestamp": "Timestamp",
            "user": "User",
            "action": "Action",
            "resource": "Resource",
            "detail": "Detail",
        }

        headers = []
        for col in columns:
            label = conditional_escape(col_labels.get(col, col.title()))
            headers.append(f'<th class="dj-audit-log__th">{label}</th>')
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

        if rows:
            tbody = f"<tbody>{''.join(rows)}</tbody>"
        else:
            col_count = len(columns)
            tbody = (
                f'<tbody><tr><td colspan="{col_count}" '
                f'class="dj-audit-log__empty">No entries</td></tr></tbody>'
            )

        return mark_safe(
            f'<div class="{class_str}"{stream_attr}>'
            f'<table class="dj-audit-log__table">{thead}{tbody}</table></div>'
        )


@register.tag("audit_log")
def do_audit_log(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return AuditLogNode(kwargs)


# ---------------------------------------------------------------------------
# Error Boundary
# ---------------------------------------------------------------------------


class ErrorBoundaryNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        fallback = kw.get("fallback", "Something went wrong")
        retry_event = kw.get("retry_event", "")
        custom_class = kw.get("class", "")

        e_fallback = conditional_escape(str(fallback))
        e_class = conditional_escape(str(custom_class))

        classes = ["dj-error-boundary"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        try:
            content = self.nodelist.render(context)
        except Exception:
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


@register.tag("error_boundary")
def do_error_boundary(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("enderror_boundary",))
    parser.delete_first_token()
    return ErrorBoundaryNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Sortable List
# ---------------------------------------------------------------------------


class SortableListNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("sortable_list")
def do_sortable_list(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return SortableListNode(kwargs)


# ---------------------------------------------------------------------------
# Sortable Grid
# ---------------------------------------------------------------------------


class SortableGridNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("sortable_grid")
def do_sortable_grid(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return SortableGridNode(kwargs)


# ---------------------------------------------------------------------------
# Image Cropper
# ---------------------------------------------------------------------------


class ImageCropperNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("image_cropper")
def do_image_cropper(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ImageCropperNode(kwargs)


# ---------------------------------------------------------------------------
# Signature Pad
# ---------------------------------------------------------------------------


class SignaturePadNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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


@register.tag("signature_pad")
def do_signature_pad(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return SignaturePadNode(kwargs)


# ---------------------------------------------------------------------------
# Resizable Panel
# ---------------------------------------------------------------------------


class ResizablePanelNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        content = self.nodelist.render(context)

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


@register.tag("resizable_panel")
def do_resizable_panel(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endresizable_panel",))
    parser.delete_first_token()
    return ResizablePanelNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# Image Lightbox
# ---------------------------------------------------------------------------


class LightboxNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        # Current image
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

        # Navigation
        prev_btn = (
            (
                f'<button class="dj-lightbox__prev" dj-click="{e_nav}" '
                f'data-value="{idx - 1}" aria-label="Previous">'
                f"&#8249;</button>"
            )
            if total > 1
            else ""
        )

        next_btn = (
            (
                f'<button class="dj-lightbox__next" dj-click="{e_nav}" '
                f'data-value="{idx + 1}" aria-label="Next">'
                f"&#8250;</button>"
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


@register.tag("lightbox")
def do_lightbox(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return LightboxNode(kwargs)


# ---------------------------------------------------------------------------
# Dashboard Grid
# ---------------------------------------------------------------------------


class DashboardGridNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
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

        # Render child content (for {% dashboard_grid %}...{% enddashboard_grid %} usage)
        child_content = self.nodelist.render(context) if self.nodelist else ""

        panels_html = []
        for panel in panels:
            if not isinstance(panel, dict):
                continue
            pid = conditional_escape(str(panel.get("id", "")))
            title = conditional_escape(str(panel.get("title", "")))
            content = panel.get("content", "")
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
                f'<div class="dj-dashboard-grid__panel-body">{content}</div>'
                f'<div class="dj-dashboard-grid__panel-resize" role="separator"></div>'
                f"</div>"
            )

        grid_style = (
            f'style="display:grid;grid-template-columns:repeat({cols},1fr);'
            f'grid-auto-rows:minmax({e_row_height},auto);gap:{e_gap}"'
        )

        inner = "".join(panels_html) + child_content

        return mark_safe(
            f'<div class="{class_str}" dj-hook="DashboardGrid" '
            f'data-move-event="{e_move}" data-resize-event="{e_resize}" '
            f'data-columns="{cols}" {grid_style}>{inner}</div>'
        )


@register.tag("dashboard_grid")
def do_dashboard_grid(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("enddashboard_grid",))
    parser.delete_first_token()
    return DashboardGridNode(nodelist, kwargs)
