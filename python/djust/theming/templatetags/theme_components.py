"""
Theme-aware component template tags.

All components automatically use theme CSS variables and adapt to light/dark mode.
Template resolution supports theme-specific overrides via:

    djust_theming/themes/{theme_name}/components/{component}.html

Falling back to:

    djust_theming/components/{component}.html
"""

import uuid

from django import template
from django.utils.safestring import mark_safe
from typing import Any, Optional

from ..manager import get_theme_config
from ..template_resolver import resolve_component_template

register = template.Library()


def _css_prefix() -> str:
    """Return the current css_prefix from theme config."""
    return get_theme_config().get("css_prefix", "")


def _extract_slots(attrs: dict) -> tuple[dict, dict]:
    """Separate slot_* keys from regular attrs.

    Returns:
        (slots_dict, remaining_attrs_dict)
    """
    slots = {}
    remaining = {}
    for k, v in attrs.items():
        if k.startswith("slot_"):
            slots[k] = v
        else:
            remaining[k] = v
    return slots, remaining


@register.simple_tag(takes_context=True)
def theme_button(context, text: str, variant: str = "primary", size: str = "md", **attrs):
    """
    Render a themed button.

    Args:
        text: Button text
        variant: 'primary', 'secondary', 'destructive', 'ghost', 'link'
        size: 'sm', 'md', 'lg'
        **attrs: Additional HTML attributes (class, id, onclick, etc.)

    Usage:
        {% theme_button "Click me" variant="primary" size="md" %}
        {% theme_button "Delete" variant="destructive" onclick="confirmDelete()" %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "button")
    ctx = {
        "text": text,
        "variant": variant,
        "size": size,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_card(context, title: Optional[str] = None, footer: Optional[str] = None, **attrs):
    """
    Render a themed card container.

    Args:
        title: Optional card title
        footer: Optional card footer content
        **attrs: Additional HTML attributes

    Usage:
        {% theme_card title="Card Title" %}
            <p>Card content goes here</p>
        {% end_theme_card %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "card")
    ctx = {
        "title": title,
        "footer": footer,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_badge(context, text: str, variant: str = "default", **attrs):
    """
    Render a themed badge.

    Args:
        text: Badge text
        variant: 'default', 'secondary', 'success', 'warning', 'destructive'
        **attrs: Additional HTML attributes

    Usage:
        {% theme_badge "New" variant="success" %}
        {% theme_badge "Beta" variant="secondary" %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "badge")
    ctx = {
        "text": text,
        "variant": variant,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_alert(
    context,
    message: str,
    title: Optional[str] = None,
    variant: str = "default",
    dismissible: bool = False,
    **attrs,
):
    """
    Render a themed alert.

    Args:
        message: Alert message
        title: Optional alert title
        variant: 'default', 'success', 'warning', 'destructive'
        dismissible: Whether alert can be dismissed
        **attrs: Additional HTML attributes

    Usage:
        {% theme_alert "Operation successful!" variant="success" dismissible=True %}
        {% theme_alert "Error occurred" title="Error" variant="destructive" %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "alert")
    ctx = {
        "message": message,
        "title": title,
        "variant": variant,
        "dismissible": dismissible,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_input(
    context,
    name: str,
    label: Optional[str] = None,
    placeholder: str = "",
    type: str = "text",
    **attrs,
):
    """
    Render a themed input field.

    Args:
        name: Input name attribute
        label: Optional label text
        placeholder: Placeholder text
        type: Input type (text, email, password, etc.)
        **attrs: Additional HTML attributes

    Usage:
        {% theme_input "email" label="Email Address" placeholder="you@example.com" type="email" %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "input")
    ctx = {
        "name": name,
        "label": label,
        "placeholder": placeholder,
        "type": type,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_modal(context, id: str, title: Optional[str] = None, size: str = "md", **attrs):
    """
    Render a themed modal dialog.

    Args:
        id: Unique modal identifier (used for data-theme-modal-open triggers)
        title: Optional modal title
        size: 'sm', 'md', 'lg'
        **attrs: Additional HTML attributes

    Usage:
        {% theme_modal id="confirm" title="Confirm Action" size="md" %}
        <!-- Trigger: <button data-theme-modal-open="confirm">Open</button> -->
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "modal")
    ctx = {
        "id": id,
        "title": title,
        "size": size,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_dropdown(context, id: str, label: str, align: str = "left", **attrs):
    """
    Render a themed dropdown menu.

    Args:
        id: Unique dropdown identifier
        label: Trigger button text
        align: Menu alignment ('left' or 'right')
        **attrs: Additional HTML attributes

    Usage:
        {% theme_dropdown id="actions" label="Actions" align="right" %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "dropdown")
    ctx = {
        "id": id,
        "label": label,
        "align": align,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_tabs(context, id: str, tabs: Any = None, active: int = 0, **attrs):
    """
    Render themed tabs with panels.

    Args:
        id: Unique tabs identifier
        tabs: List of dicts with 'label' and 'content' keys
        active: Zero-based index of the initially active tab
        **attrs: Additional HTML attributes

    Usage:
        {% theme_tabs id="settings" tabs=tab_list active=0 %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "tabs")
    ctx = {
        "id": id,
        "tabs": tabs or [],
        "active": active,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_table(
    context,
    headers: Any = None,
    rows: Any = None,
    variant: str = "default",
    caption: Optional[str] = None,
    **attrs,
):
    """
    Render a themed responsive table.

    Args:
        headers: List of column header strings
        rows: List of row lists (each row is a list of cell values)
        variant: 'default', 'striped', 'hover'
        caption: Optional table caption
        **attrs: Additional HTML attributes

    Usage:
        {% theme_table headers=headers rows=rows variant="striped" caption="Users" %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "table")
    ctx = {
        "headers": headers or [],
        "rows": rows or [],
        "variant": variant,
        "caption": caption,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_pagination(
    context,
    current_page: int = 1,
    total_pages: int = 1,
    url_pattern: str = "?page={}",
    show_edges: bool = True,
    **attrs,
):
    """
    Render themed pagination controls.

    Args:
        current_page: Current page number (1-based)
        total_pages: Total number of pages
        url_pattern: URL pattern with {} placeholder for page number
        show_edges: Whether to show first/last page links
        **attrs: Additional HTML attributes

    Usage:
        {% theme_pagination current_page=page total_pages=total url_pattern="/items/?page={}" %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "pagination")

    # Build page range (show up to 5 pages around current)
    window = 2
    range_start = max(1, current_page - window)
    range_end = min(total_pages, current_page + window)

    page_range = []
    for p in range(range_start, range_end + 1):
        page_range.append({"number": p, "url": url_pattern.format(p)})

    # Edge detection
    first_page = 1 if show_edges and range_start > 1 else None
    first_url = url_pattern.format(1) if first_page else None
    first_ellipsis = range_start > 2

    last_page = total_pages if show_edges and range_end < total_pages else None
    last_url = url_pattern.format(total_pages) if last_page else None
    last_ellipsis = range_end < total_pages - 1

    prev_url = url_pattern.format(current_page - 1) if current_page > 1 else None
    next_url = url_pattern.format(current_page + 1) if current_page < total_pages else None

    ctx = {
        "current_page": current_page,
        "total_pages": total_pages,
        "url_pattern": url_pattern,
        "show_edges": show_edges,
        "page_range": page_range,
        "first_page": first_page,
        "first_url": first_url,
        "first_ellipsis": first_ellipsis,
        "last_page": last_page,
        "last_url": last_url,
        "last_ellipsis": last_ellipsis,
        "prev_url": prev_url,
        "next_url": next_url,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_select(
    context,
    name: str,
    label: Optional[str] = None,
    options: Any = None,
    placeholder: str = "",
    **attrs,
):
    """
    Render a themed select dropdown.

    Args:
        name: Select name attribute
        label: Optional label text
        options: List of dicts with 'value' and 'label' keys
        placeholder: Placeholder option text
        **attrs: Additional HTML attributes (required, disabled, etc.)

    Usage:
        {% theme_select "country" label="Country" options=countries placeholder="Choose..." %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "select")
    ctx = {
        "name": name,
        "label": label,
        "options": options or [],
        "placeholder": placeholder,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_textarea(
    context, name: str, label: Optional[str] = None, placeholder: str = "", rows: int = 4, **attrs
):
    """
    Render a themed textarea.

    Args:
        name: Textarea name attribute
        label: Optional label text
        placeholder: Placeholder text
        rows: Number of visible text rows
        **attrs: Additional HTML attributes (required, disabled, readonly, etc.)

    Usage:
        {% theme_textarea "bio" label="Biography" placeholder="Tell us about yourself..." rows=6 %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "textarea")
    ctx = {
        "name": name,
        "label": label,
        "placeholder": placeholder,
        "rows": rows,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_checkbox(context, name: str, label: str = "", description: Optional[str] = None, **attrs):
    """
    Render a themed checkbox.

    Args:
        name: Checkbox name attribute
        label: Label text displayed next to the checkbox
        description: Optional descriptive text below the label
        **attrs: Additional HTML attributes (checked, required, disabled, value, etc.)

    Usage:
        {% theme_checkbox "agree" label="I agree to terms" required=True %}
        {% theme_checkbox "newsletter" label="Subscribe" description="Get weekly updates" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "checkbox")
    ctx = {
        "name": name,
        "label": label,
        "description": description,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_radio(
    context,
    name: str,
    label: Optional[str] = None,
    options: Any = None,
    selected: str = "",
    **attrs,
):
    """
    Render a themed radio button group.

    Args:
        name: Radio group name attribute
        label: Optional group label (rendered as fieldset legend)
        options: List of dicts with 'value' and 'label' keys
        selected: Value of the initially selected option
        **attrs: Additional HTML attributes (required, disabled, etc.)

    Usage:
        {% theme_radio "size" label="Size" options=sizes selected="md" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "radio")
    ctx = {
        "name": name,
        "label": label,
        "options": options or [],
        "selected": selected,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_breadcrumb(context, items: Any = None, separator: str = "/", **attrs):
    """
    Render a themed breadcrumb navigation.

    Args:
        items: List of dicts with 'label' and 'url' keys
        separator: Separator character between items
        **attrs: Additional HTML attributes

    Usage:
        {% theme_breadcrumb items=breadcrumbs separator=">" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "breadcrumb")
    ctx = {
        "items": items or [],
        "separator": separator,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_avatar(
    context, src: Optional[str] = None, alt: str = "", name: str = "", size: str = "md", **attrs
):
    """
    Render a themed avatar with image or initials fallback.

    Args:
        src: Image URL
        alt: Alt text for the image
        name: Full name (used for initials fallback)
        size: 'sm', 'md', 'lg'
        **attrs: Additional HTML attributes

    Usage:
        {% theme_avatar src="/img/user.jpg" alt="John Doe" size="lg" %}
        {% theme_avatar name="John Doe" size="md" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "avatar")

    # Generate initials from name
    initials = ""
    if name:
        parts = name.strip().split()
        if len(parts) >= 2:
            initials = parts[0][0].upper() + parts[-1][0].upper()
        elif parts:
            initials = parts[0][0].upper()

    ctx = {
        "src": src,
        "alt": alt,
        "name": name,
        "initials": initials,
        "size": size,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_toast(
    context,
    message: str,
    variant: str = "info",
    position: str = "top-right",
    duration: int = 5000,
    **attrs,
):
    """
    Render a themed toast notification.

    Args:
        message: Toast message
        variant: 'success', 'warning', 'error', 'info'
        position: 'top-right', 'top-left', 'bottom-right', 'bottom-left'
        duration: Auto-dismiss duration in milliseconds
        **attrs: Additional HTML attributes

    Usage:
        {% theme_toast "Saved!" variant="success" %}
        {% theme_toast "Error occurred" variant="error" position="bottom-right" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "toast")
    ctx = {
        "message": message,
        "variant": variant,
        "position": position,
        "duration": duration,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_progress(context, value: Any = None, max: int = 100, label: str = "", **attrs):
    """
    Render a themed progress bar.

    Args:
        value: Current value (None for indeterminate)
        max: Maximum value
        label: Accessible label text
        **attrs: Additional HTML attributes

    Usage:
        {% theme_progress value=75 max=100 label="Upload progress" %}
        {% theme_progress label="Loading..." %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "progress")

    is_indeterminate = value is None
    percentage = 0
    if not is_indeterminate and max > 0:
        percentage = min(100, (int(value) / int(max)) * 100)

    ctx = {
        "value": value,
        "max": max,
        "label": label,
        "is_indeterminate": is_indeterminate,
        "percentage": percentage,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_skeleton(
    context, variant: str = "text", width: str = "100%", height: str = "1rem", **attrs
):
    """
    Render a themed skeleton loading placeholder.

    Args:
        variant: 'text', 'circle', 'rect'
        width: CSS width value
        height: CSS height value
        **attrs: Additional HTML attributes

    Usage:
        {% theme_skeleton variant="text" width="200px" %}
        {% theme_skeleton variant="circle" width="3rem" height="3rem" %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "skeleton")
    ctx = {
        "variant": variant,
        "width": width,
        "height": height,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_tooltip(context, text: str, position: str = "top", **attrs):
    """
    Render a CSS-only tooltip.

    Args:
        text: Tooltip text shown on hover
        position: 'top', 'bottom', 'left', 'right'
        **attrs: Additional HTML attributes (slot_content for wrapped content)

    Usage:
        {% theme_tooltip "Help text" position="top" slot_content="<button>Hover me</button>" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "tooltip")
    tooltip_id = remaining_attrs.pop("id", f"tooltip-{uuid.uuid4().hex}")
    ctx = {
        "text": text,
        "position": position,
        "tooltip_id": tooltip_id,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag
def theme_icon(name: str, size: int = 20):
    """
    Render an SVG icon (placeholder - integrate with your icon library).

    Args:
        name: Icon name
        size: Icon size in pixels

    Usage:
        {% theme_icon "check" size=16 %}
    """
    # Placeholder SVG icons
    icons = {
        "check": f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>',
        "x": f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>',
        "alert": f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>',
        "info": f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>',
    }
    return mark_safe(icons.get(name, ""))


@register.simple_tag(takes_context=True)
def theme_nav_item(
    context,
    label: str,
    url: str,
    icon: Optional[str] = None,
    active: Optional[bool] = None,
    badge: Optional[str] = None,
    **attrs,
):
    """
    Render a themed navigation link with active state detection.

    Args:
        label: Link text
        url: Link URL
        icon: Optional icon name/text
        active: Explicit active state; if None, auto-detects from request.path
        badge: Optional badge text (e.g. count)
        **attrs: Additional HTML attributes (slot_icon, slot_badge, class, id, etc.)

    Usage:
        {% theme_nav_item "Home" "/" %}
        {% theme_nav_item "Inbox" "/inbox/" badge="5" %}
        {% theme_nav_item "Dashboard" "/dash/" active=True %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "nav_item")

    # Auto-detect active state from request.path
    is_active = active
    if is_active is None and request is not None:
        request_path = getattr(request, "path", None)
        if request_path is not None:
            if url == "/":
                is_active = request_path == "/"
            else:
                is_active = request_path.startswith(url)

    ctx = {
        "label": label,
        "url": url,
        "icon": icon,
        "is_active": bool(is_active),
        "badge": badge,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_nav_group(
    context,
    label: str,
    items: Any = None,
    icon: Optional[str] = None,
    expanded: bool = True,
    **attrs,
):
    """
    Render a collapsible navigation group with a heading and child items.

    Args:
        label: Group heading text
        items: List of dicts with 'label', 'url', and optional 'icon', 'badge' keys
        icon: Optional icon name/text for the group heading
        expanded: Whether the group is expanded by default
        **attrs: Additional HTML attributes (slot_label, slot_items, class, id, etc.)

    Usage:
        {% theme_nav_group "Admin" items=admin_links %}
        {% theme_nav_group "Settings" items=settings_links expanded=False %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "nav_group")
    ctx = {
        "label": label,
        "items": items or [],
        "icon": icon,
        "expanded": expanded,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_nav(context, brand: Optional[str] = None, items: Any = None, **attrs):
    """
    Render a themed horizontal navigation bar.

    Args:
        brand: Brand text or name
        items: List of dicts with 'label', 'url', and optional 'icon', 'active', 'badge' keys
        **attrs: Additional HTML attributes (slot_brand, slot_items, slot_actions, class, id, etc.)

    Usage:
        {% theme_nav brand="MyApp" items=nav_items %}
        {% theme_nav brand="MyApp" slot_actions="<button>Login</button>" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "nav")
    ctx = {
        "brand": brand,
        "items": items or [],
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_sidebar_nav(context, sections: Any = None, **attrs):
    """
    Render a themed vertical sidebar navigation with sections.

    Args:
        sections: List of dicts with 'title' and 'items' keys.
                  Each item dict has 'label', 'url', and optional 'icon', 'active', 'badge'.
        **attrs: Additional HTML attributes (slot_header, slot_sections, slot_footer, class, id, etc.)

    Usage:
        {% theme_sidebar_nav sections=sidebar_sections %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "sidebar_nav")
    ctx = {
        "sections": sections or [],
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))
