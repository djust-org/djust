"""
Theme-aware component template tags.

All components automatically use theme CSS variables and adapt to light/dark mode.
Template resolution supports theme-specific overrides via:

    djust_theming/themes/{theme_name}/components/{component}.html

Falling back to:

    djust_theming/components/{component}.html
"""

import logging
import re
import uuid

from typing import Any, Optional
from urllib.parse import unquote

from django import template
from django.template import Context
from django.utils.safestring import SafeString, mark_safe

from ...components.utils import safe_url, url_attr
from ..manager import get_theme_config
from ..template_resolver import resolve_component_template

logger = logging.getLogger(__name__)

register = template.Library()


def _css_prefix() -> str:
    """Return the current css_prefix from theme config."""
    return str(get_theme_config().get("css_prefix", ""))


def _extract_slots(attrs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate slot_* keys from regular attrs.

    Returns:
        (slots_dict, remaining_attrs_dict)
    """
    slots: dict[str, Any] = {}
    remaining: dict[str, Any] = {}
    for k, v in attrs.items():
        if k.startswith("slot_"):
            slots[k] = v
        else:
            remaining[k] = v
    return slots, remaining


@register.simple_tag(takes_context=True)
def theme_button(
    context: Context,
    text: str,
    variant: str = "primary",
    size: str = "md",
    href: Optional[str] = None,
    element: Optional[str] = None,
    **attrs: Any,
) -> SafeString:
    """
    Render a themed button.

    Args:
        text: Button text
        variant: 'primary', 'secondary', 'destructive', 'ghost', 'link'
        size: 'sm', 'md', 'lg'
        href: Render an ``<a class="btn ...">`` pointing here instead of a
            ``<button>``. ``javascript:``, ``vbscript:`` and ``data:`` URLs
            are refused.
        element: 'button' or 'a'. Defaults to 'a' when ``href`` is given and
            'button' otherwise.
        **attrs: Additional HTML attributes. ``class``, ``id``, ``onclick``
            and ``type`` (``<button>`` only) are handled by the template;
            everything else (``dj_click``, ``dj_value_id``, ``name``,
            ``value``, ``disabled``, ``data_*``, ``aria_*`` ...) is emitted on
            the element, escaped per attribute, underscores becoming hyphens.
            Other ``on*`` handler attributes are refused. On an ``<a>``,
            ``type=`` raises, and ``disabled=True`` becomes
            ``aria-disabled="true" tabindex="-1"`` with no ``href``.

            A project-level ``components/button.html`` that predates this must
            render ``{{ extra_attrs }}``, ``{{ href }}`` and branch on ``tag``
            to honour them; ``djust_theme check-compat`` flags one that does not.

    Usage:
        {% theme_button "Click me" variant="primary" size="md" %}
        {% theme_button "Delete" variant="destructive" onclick="confirmDelete()" %}
        {% theme_button "Advance" dj_click="advance" dj_value_id=item.id %}
        {% theme_button "Open record" href=record.url variant="secondary" %}
    """
    if element is None:
        element = "a" if href else "button"
    if element not in ("button", "a"):
        raise ValueError(f"theme_button: element must be 'button' or 'a', got {element!r}")
    if href and element != "a":
        raise ValueError(
            "theme_button: href= renders an <a>; it cannot be used with element='button'"
        )
    if href:
        _check_url("href", href)
    if element == "a":
        if attrs.get("type") is not None:
            raise ValueError("theme_button: type= applies to a <button>, not an <a>")
        if attrs.get("disabled"):
            # `disabled` does nothing on an anchor: the link stays clickable. Say
            # it is disabled to assistive tech, take it out of the tab order and
            # drop the destination.
            attrs = {k: v for k, v in attrs.items() if k != "disabled"}
            attrs.update(aria_disabled="true", tabindex="-1")
            href = None
    request = context.get("request")
    tmpl = resolve_component_template(request, "button")
    # `slot_*` keywords are context, not attributes — the template
    # reads them by name. Without this they stay in `attrs`, which
    # templates only ever read as `attrs.class` / `attrs.id`, so a
    # caller-supplied slot rendered as nothing at all.
    slots, remaining_attrs = _extract_slots(attrs)
    ctx = {
        "text": text,
        "variant": variant,
        "size": size,
        "tag": element,
        "href": href,
        "attrs": remaining_attrs,
        "extra_attrs": _passthrough_attrs(
            remaining_attrs, skip=("class", "id", "onclick", "type", "href")
        ),
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_card(
    context: Context,
    title: Optional[str] = None,
    footer: Optional[str] = None,
    body: Optional[str] = None,
    **attrs: Any,
) -> SafeString:
    """
    Render a themed card container.

    Args:
        title: Optional card title
        body: Optional card body. Passed through as `slot_body`, which is the
            name `card.html` reads, so callers have one body argument rather
            than two spellings of it.
        footer: Optional card footer content
        **attrs: Additional HTML attributes

    Usage:
        {% theme_card title="Card Title" body="Card content goes here" %}

    Each part is optional and each is dropped when absent, so a card with only
    a body is as valid as one with all three.

    A card body here is a string — this is a `simple_tag`, so it takes no
    closing tag. For a body of other template tags use the block form,
    `{% theme_card_block %}…{% end_theme_card_block %}` (#2894).
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "card")
    slots, remaining_attrs = _extract_slots(attrs)
    if body is not None:
        slots.setdefault("slot_body", body)
    ctx = {
        "title": title,
        "footer": footer,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_badge(context: Context, text: str, variant: str = "default", **attrs: Any) -> SafeString:
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
    # `slot_*` keywords are context, not attributes — the template
    # reads them by name. Without this they stay in `attrs`, which
    # templates only ever read as `attrs.class` / `attrs.id`, so a
    # caller-supplied slot rendered as nothing at all.
    slots, remaining_attrs = _extract_slots(attrs)
    ctx = {
        "text": text,
        "variant": variant,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


_ASSERTIVE_ALERT_VARIANTS = frozenset({"destructive", "warning"})
_ALERT_ROLES = frozenset({"alert", "status", "log"})


@register.simple_tag(takes_context=True)
def theme_alert(
    context: Context,
    message: str,
    title: Optional[str] = None,
    variant: str = "default",
    dismissible: bool = False,
    role: Optional[str] = None,
    **attrs: Any,
) -> SafeString:
    """
    Render a themed alert.

    Args:
        message: Alert message
        title: Optional alert title
        variant: 'default', 'info', 'success', 'warning', 'destructive'
        dismissible: Whether alert can be dismissed
        role: ARIA live-region role: 'alert' (assertive), 'status' (polite)
            or 'log'. Defaults to 'alert' for the 'destructive' and 'warning'
            variants and 'status' for the rest, so a persistent banner does
            not interrupt a screen reader the way an error should.
        **attrs: Additional HTML attributes

    Usage:
        {% theme_alert "Operation successful!" variant="success" dismissible=True %}
        {% theme_alert "Error occurred" title="Error" variant="destructive" %}
        {% theme_alert "Viewing the record as of 2026-01-01" variant="info" %}
        {% theme_alert "Saved" variant="success" role="alert" %}
        {% theme_alert_block variant="info" %}Rich <a href="/x">content</a>{% end_theme_alert_block %}
    """
    if role is None:
        role = "alert" if variant in _ASSERTIVE_ALERT_VARIANTS else "status"
    elif role not in _ALERT_ROLES:
        raise ValueError(f"theme_alert: role must be one of {sorted(_ALERT_ROLES)}, got {role!r}")
    request = context.get("request")
    tmpl = resolve_component_template(request, "alert")
    # `slot_*` keywords are context, not attributes — the template
    # reads them by name. Without this they stay in `attrs`, which
    # templates only ever read as `attrs.class` / `attrs.id`, so a
    # caller-supplied slot rendered as nothing at all.
    slots, remaining_attrs = _extract_slots(attrs)
    ctx = {
        "message": message,
        "title": title,
        "variant": variant,
        "role": role,
        "dismissible": dismissible,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_input(
    context: Context,
    name: str,
    label: Optional[str] = None,
    placeholder: str = "",
    type: str = "text",
    id: Optional[str] = None,
    **attrs: Any,
) -> SafeString:
    """
    Render a themed input field.

    Args:
        name: Input name attribute
        label: Optional label text
        placeholder: Placeholder text
        type: Input type (text, email, password, etc.)
        id: The ``<input>``'s id, which the label's ``for`` points at. Defaults
            to ``name``; pass one when the same field repeats on a page (one
            note field per row) so ids stay unique.
        **attrs: Additional HTML attributes

    Usage:
        {% theme_input "email" label="Email Address" placeholder="you@example.com" type="email" %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "input")
    # `slot_*` keywords are context, not attributes — the template
    # reads them by name. Without this they stay in `attrs`, which
    # templates only ever read as `attrs.class` / `attrs.id`, so a
    # caller-supplied slot rendered as nothing at all.
    slots, remaining_attrs = _extract_slots(attrs)
    ctx = {
        "name": name,
        "field_id": id or name,
        "label": label,
        "placeholder": placeholder,
        "type": type,
        "attrs": remaining_attrs,
        # Everything the template does not read by name — `dj_input`,
        # `dj_debounce`, `autocomplete`, `aria_label` … — reaches the
        # `<input>` as attributes (underscores become hyphens), so a live
        # search box can be this component rather than a hand-written input.
        "extra_attrs": _passthrough_attrs(
            remaining_attrs, skip=("class", "id", "value", "required", "disabled", "readonly")
        ),
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


# What an attribute name may look like once ``_`` has become ``-``: a letter
# first, then letters, digits and hyphens. Template kwargs are already
# ``\w+``, but these helpers are plain functions too, so the name is checked
# rather than trusted — it is written into the tag unescaped.
_ATTR_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*")

# Attributes whose value is a URL the browser will navigate to or load.
_URL_ATTRS = frozenset({"href", "src", "action", "formaction"})

_UNSAFE_URL_SCHEMES = ("javascript:", "vbscript:", "data:")


def _check_url(name: str, value: Any) -> None:
    """Refuse a script-bearing URL scheme in ``name``.

    Browsers drop tabs, newlines and other control characters inside a URL
    scheme and ignore leading whitespace, so ``"java\tscript:..."`` is still
    ``javascript:``. Strip those before looking at the scheme.
    """
    compact = "".join(ch for ch in str(value) if ord(ch) > 32 and ord(ch) != 127).lower()
    if compact.startswith(_UNSAFE_URL_SCHEMES):
        raise ValueError(f"{name}= refuses javascript:, vbscript: and data: URLs")


def _neutralise_url(name: str, value: Any, *, image: bool = False) -> Any:
    """``value``, or ``"#"`` when its scheme is not safe for a link.

    For URLs that come from app data (nav items, page links, an avatar
    ``src``): a stored ``javascript:`` link must not reach the page, but it
    must not take the whole render down for every viewer either. The policy is
    the component library's (:func:`djust.components.utils.safe_url`, and
    :func:`~djust.components.utils.url_attr` with ``image=True`` for ``src``,
    which also keeps ``data:image/*``). A safe value is returned unchanged, so
    the template escapes it exactly once. Developer-supplied literals
    (``theme_button href=``, the ``**attrs`` passthrough) still raise via
    :func:`_check_url`.
    """
    if value is None:
        return value
    text = str(value)
    if not text.strip():
        return value
    checked = url_attr(value, image=True) if image else safe_url(value)
    if checked == "#" and text.strip() != "#":
        logger.debug("theming: %s= URL with a disallowed scheme rendered as '#'", name)
        return "#"
    return value


def _neutralise_item_urls(items: Any, name: str = "url") -> Any:
    """*items* with :func:`_neutralise_url` applied to ``name`` on every dict.

    The navigation components (``theme_nav`` / ``theme_nav_group`` /
    ``theme_sidebar_nav`` / ``theme_breadcrumb``) take their links as a list of
    ``{"label": ..., "url": ...}`` dicts, which the component template renders
    into ``<a href>``. A list an app fills from its own data (a CMS page, a
    user-supplied link) can carry a script-bearing scheme; that item's link
    becomes ``"#"`` and the rest of the list renders as before. The caller's
    dicts are never modified: an item that changes is a copy.
    """
    if not items:
        return items
    if isinstance(items, dict):
        return _neutralise_item_urls([items], name)[0]
    if not isinstance(items, (list, tuple)):
        return items
    result = []
    for item in items:
        if isinstance(item, dict):
            value = item.get(name)
            if value is not None:
                safe = _neutralise_url(name, value)
                if safe is not value:
                    item = {**item, name: safe}
        result.append(item)
    return result


def _passthrough_attrs(attrs: dict[str, Any], skip: tuple[str, ...]) -> SafeString:
    """``key="value"`` pairs for the attrs a component template does not
    handle itself. ``dj_input`` → ``dj-input``; values are escaped; ``True``
    emits a bare attribute, ``False``/``None`` nothing.

    Names are validated, because they are written into the tag as given: a
    name that is not a plain attribute identifier raises ``ValueError``, as
    does an ``on*`` event-handler attribute (inline script — use a ``dj-*``
    binding) and a script-bearing ``href`` / ``src`` / ``action`` URL.
    """
    from django.utils.html import escape

    parts = []
    for key, value in attrs.items():
        if key in skip or value is None or value is False:
            continue
        name = key.replace("_", "-")
        if not _ATTR_NAME_RE.fullmatch(name):
            raise ValueError(f"{key!r} is not a valid HTML attribute name")
        if name.lower().startswith("on"):
            raise ValueError(
                f"{key!r}: inline event-handler attributes are not passed through; "
                "use a dj-* binding (dj_click, dj_change ...)"
            )
        if name.lower() in _URL_ATTRS and value is not True:
            _check_url(key, value)
        parts.append(name if value is True else f'{name}="{escape(value)}"')
    return mark_safe(" ".join(parts))


@register.simple_tag(takes_context=True)
def theme_modal(
    context: Context,
    id: str,
    title: Optional[str] = None,
    size: str = "md",
    is_open: bool = False,
    component_id: str = "",
    **attrs: Any,
) -> SafeString:
    """
    Render a themed modal dialog.

    Args:
        id: Unique modal identifier
        title: Optional modal title
        size: 'sm', 'md', 'lg'
        component_id: Name of the descriptor this instance belongs to, emitted
            as `data-component-id` on the close control. Required when a page
            declares more than one modal descriptor — the framework wires one
            `toggle_modal` handler and cannot auto-resolve which instance an
            event belongs to. A page with a single modal can omit it.
        is_open: Whether the dialog renders open. Server-driven: the host
            LiveView's `Modal` descriptor owns this, and the close control
            dispatches `toggle_modal`. Defaults to closed, which is what a
            static page gets — and a static page has no server to dispatch to,
            so a modal needs a LiveView host to open at all.
        **attrs: Additional HTML attributes

    Usage:
        {% theme_modal id="confirm" title="Confirm Action" size="md" is_open=modal.is_open %}
        {% theme_modal_block id="confirm" title="Confirm" is_open=modal.is_open %}…{% end_theme_modal_block %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "modal")
    # `slot_*` keywords are context, not attributes — the template
    # reads them by name. Without this they stay in `attrs`, which
    # templates only ever read as `attrs.class` / `attrs.id`, so a
    # caller-supplied slot rendered as nothing at all.
    slots, remaining_attrs = _extract_slots(attrs)
    ctx = {
        "id": id,
        "title": title,
        "size": size,
        "is_open": is_open,
        "component_id": component_id,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_dropdown(
    context: Context,
    id: str,
    label: str,
    align: str = "left",
    is_open: bool = False,
    component_id: str = "",
    **attrs: Any,
) -> SafeString:
    """
    Render a themed dropdown menu.

    Args:
        id: Unique dropdown identifier
        label: Trigger button text
        align: Menu alignment ('left' or 'right')
        is_open: Whether the menu renders open. Server-driven: the host
            LiveView's `Dropdown` descriptor owns this and the trigger
            dispatches `toggle_dropdown`.
        component_id: Name of the descriptor this instance belongs to, emitted
            as `data-component-id`. Required when a page declares more than one
            descriptor of the same type: the framework wires one
            `toggle_dropdown` handler and cannot auto-resolve which instance an
            event belongs to, so the trigger must say. A page with a single
            dropdown can omit it.
        **attrs: Additional HTML attributes

    Usage:
        {% theme_dropdown id="actions" label="Actions" align="right" is_open=menu.is_open %}
        {% theme_dropdown_block id="actions" label="Actions" is_open=menu.is_open %}…{% end_theme_dropdown_block %}
    """
    request = context.get("request")
    tmpl = resolve_component_template(request, "dropdown")
    # `slot_*` keywords are context, not attributes — the template
    # reads them by name. Without this they stay in `attrs`, which
    # templates only ever read as `attrs.class` / `attrs.id`, so a
    # caller-supplied slot rendered as nothing at all.
    slots, remaining_attrs = _extract_slots(attrs)
    ctx = {
        "id": id,
        "label": label,
        "align": align,
        "is_open": is_open,
        "component_id": component_id,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_tabs(
    context: Context,
    id: str,
    tabs: Any = None,
    active: int = 0,
    component_id: str = "",
    **attrs: Any,
) -> SafeString:
    """
    Render themed tabs with panels.

    Args:
        id: Unique tabs identifier
        tabs: List of dicts with 'label' and 'content' keys
        active: Zero-based index of the active tab. Server-driven: the host
            LiveView's `Tabs` descriptor owns this and the tab buttons dispatch
            `set_tab` with their index.
        component_id: Name of the descriptor this instance belongs to, emitted
            as `data-component-id`. Required when a page declares more than one
            tabs descriptor; a page with a single tab set can omit it.
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
        "component_id": component_id,
        "attrs": attrs,
        "css_prefix": _css_prefix(),
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_table(
    context: Context,
    headers: Any = None,
    rows: Any = None,
    variant: str = "default",
    caption: Optional[str] = None,
    **attrs: Any,
) -> SafeString:
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
    # `slot_*` keywords are context, not attributes — the template
    # reads them by name. Without this they stay in `attrs`, which
    # templates only ever read as `attrs.class` / `attrs.id`, so a
    # caller-supplied slot rendered as nothing at all.
    slots, remaining_attrs = _extract_slots(attrs)
    ctx = {
        "headers": headers or [],
        "rows": rows or [],
        "variant": variant,
        "caption": caption,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_pagination(
    context: Context,
    current_page: int = 1,
    total_pages: int = 1,
    url_pattern: str = "?page={}",
    show_edges: bool = True,
    **attrs: Any,
) -> SafeString:
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
    # `slot_*` keywords are context, not attributes — the template
    # reads them by name. Without this they stay in `attrs`, which
    # templates only ever read as `attrs.class` / `attrs.id`, so a
    # caller-supplied slot rendered as nothing at all.
    slots, remaining_attrs = _extract_slots(attrs)
    # Every page link is built from ``url_pattern`` by ``.format(p)`` and rendered
    # into an ``<a href>``; a script-bearing scheme in the pattern would reach all
    # of them (#F1), so such a pattern makes every link ``"#"``.
    url_pattern = _neutralise_url("url_pattern", url_pattern)

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
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


def _option_parts(opt: Any) -> tuple[Any, Any, bool]:
    """``(value, label, selected)`` of one option: a dict, a ``(value, label)``
    pair, or an object with ``value`` / ``label`` attributes. Missing parts are
    ``""`` (what the template printed before) rather than ``"None"``."""
    if isinstance(opt, dict):
        value, label, selected = opt.get("value"), opt.get("label"), opt.get("selected")
    elif isinstance(opt, (tuple, list)):
        value = opt[0] if len(opt) > 0 else None
        label = opt[1] if len(opt) > 1 else value
        selected = None
    else:
        value, label, selected = (getattr(opt, key, None) for key in ("value", "label", "selected"))
    return ("" if value is None else value, "" if label is None else label, bool(selected))


def _mark_selected(options: Any, value: Any) -> list[dict[str, Any]]:
    """Copy ``options`` as ``{value, label, selected}`` dicts, selecting those
    whose value equals ``value`` as a string. A list, tuple or set ``value``
    (a ``multiple`` select) selects every match. The caller's option dicts are
    shared view state, so none is mutated."""
    if value is None:
        wanted: set[str] = set()
    elif isinstance(value, (list, tuple, set, frozenset)):
        wanted = {str(v) for v in value}
    else:
        wanted = {str(value)}
    resolved = []
    for opt in options:
        opt_value, label, selected = _option_parts(opt)
        resolved.append(
            {
                "value": opt_value,
                "label": label,
                "selected": selected or str(opt_value) in wanted,
            }
        )
    return resolved


@register.simple_tag(takes_context=True)
def theme_select(
    context: Context,
    name: str,
    label: Optional[str] = None,
    options: Any = None,
    placeholder: str = "",
    value: Any = None,
    id: Optional[str] = None,
    **attrs: Any,
) -> SafeString:
    """
    Render a themed select dropdown.

    Args:
        name: Select name attribute
        label: Optional label text
        options: List of dicts with 'value' and 'label' keys. An option dict
            carrying a truthy 'selected' is selected, as before.
        placeholder: Placeholder option text
        value: The current value. The option whose 'value' equals it
            (compared as strings, so ``3`` matches ``"3"``) is rendered
            ``selected``, which is what an edit form needs to open on the
            stored choice. The placeholder is selected only when nothing else is.
        id: The ``<select>``'s id, which the label's ``for`` points at.
            Defaults to ``name``.
        **attrs: Additional HTML attributes. ``required`` and ``disabled`` are
            handled by the template; everything else (``dj_change``,
            ``data_*``, ``aria_*`` ...) is emitted on the ``<select>``, escaped
            per attribute, underscores becoming hyphens.

    Usage:
        {% theme_select "country" label="Country" options=countries placeholder="Choose..." %}
        {% theme_select "category" options=opts value=doc.category dj_change="set_category" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "select")
    resolved_options = _mark_selected(options or [], value)
    ctx = {
        "name": name,
        "field_id": id or name,
        "label": label,
        "options": resolved_options,
        "placeholder": placeholder,
        "has_selected_option": any(opt["selected"] for opt in resolved_options),
        "attrs": remaining_attrs,
        "extra_attrs": _passthrough_attrs(remaining_attrs, skip=("class", "required", "disabled")),
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_textarea(
    context: Context,
    name: str,
    label: Optional[str] = None,
    placeholder: str = "",
    rows: int = 4,
    id: Optional[str] = None,
    **attrs: Any,
) -> SafeString:
    """
    Render a themed textarea.

    Args:
        name: Textarea name attribute
        label: Optional label text
        placeholder: Placeholder text
        rows: Number of visible text rows
        id: The ``<textarea>``'s id, which the label's ``for`` points at.
            Defaults to ``name``.
        **attrs: Additional HTML attributes (required, disabled, readonly, etc.)

    Usage:
        {% theme_textarea "bio" label="Biography" placeholder="Tell us about yourself..." rows=6 %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "textarea")
    ctx = {
        "name": name,
        "field_id": id or name,
        "label": label,
        "placeholder": placeholder,
        "rows": rows,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_checkbox(
    context: Context, name: str, label: str = "", description: Optional[str] = None, **attrs: Any
) -> SafeString:
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
    context: Context,
    name: str,
    label: Optional[str] = None,
    options: Any = None,
    selected: str = "",
    **attrs: Any,
) -> SafeString:
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
def theme_breadcrumb(
    context: Context, items: Any = None, separator: str = "/", **attrs: Any
) -> SafeString:
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
    items = _neutralise_item_urls(items)
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
    context: Context,
    src: Optional[str] = None,
    alt: str = "",
    name: str = "",
    size: str = "md",
    **attrs: Any,
) -> SafeString:
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
    src = _neutralise_url("src", src, image=True)

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
    context: Context,
    message: str,
    variant: str = "info",
    position: str = "top-right",
    duration: int = 5000,
    **attrs: Any,
) -> SafeString:
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
def theme_progress(
    context: Context, value: Any = None, max: int = 100, label: str = "", **attrs: Any
) -> SafeString:
    """
    Render a themed progress bar.

    Args:
        value: Current value (None for indeterminate)
        max: Maximum value
        label: Accessible label text
        **attrs: Additional HTML attributes. ``class`` and ``id`` land on the
            wrapper; everything else (``dj_upload_progress``, ``dj_hook``,
            ``data_*`` ...) is emitted on the wrapper too, underscores becoming
            hyphens.

    Usage:
        {% theme_progress value=75 max=100 label="Upload progress" %}
        {% theme_progress label="Loading..." %}
        {% theme_progress value=0 dj_upload_progress="avatar" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "progress")

    is_indeterminate = value is None
    percentage: float = 0
    if not is_indeterminate and max > 0:
        percentage = min(100, (int(value) / int(max)) * 100)

    ctx = {
        "value": value,
        "max": max,
        "label": label,
        "is_indeterminate": is_indeterminate,
        "percentage": percentage,
        "attrs": remaining_attrs,
        # `dj_upload_progress="slot"` makes the client drive this bar from
        # that upload slot's progress (#3289).
        "extra_attrs": _passthrough_attrs(remaining_attrs, skip=("class", "id")),
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_skeleton(
    context: Context, variant: str = "text", width: str = "100%", height: str = "1rem", **attrs: Any
) -> SafeString:
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
def theme_tooltip(context: Context, text: str, position: str = "top", **attrs: Any) -> SafeString:
    """
    Render a CSS-only tooltip.

    Args:
        text: Tooltip text shown on hover
        position: 'top', 'bottom', 'left', 'right'
        **attrs: Additional HTML attributes (slot_content for wrapped content)

    Usage:
        {% theme_tooltip "Help text" position="top" slot_content="<button>Hover me</button>" %}
        {% theme_tooltip_block "Help text" position="top" %}<button>Hover me</button>{% end_theme_tooltip_block %}
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
def theme_icon(name: str, size: int = 20) -> SafeString:
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


# A query string or fragment: such a url is not a plain path match.
_URL_TAIL_RE = re.compile(r"[?#]")


def _nav_url_is_plain_path(url: str) -> bool:
    return url.startswith("/") and not url.startswith("//") and not _URL_TAIL_RE.search(url)


def _nav_url_matches(url: str, path: str) -> bool:
    """Whether a nav link to ``url`` is active on ``path``.

    ``/`` matches only itself. Any other url matches its own path and what is
    under it, on a SEGMENT boundary (``/docs`` is active on ``/docs/x/`` and
    ``/docs``, not on ``/docs-old/``), with or without a trailing slash. Both
    sides are percent-decoded, because ``request.path`` is decoded while a
    reversed url is not. ``components.js`` (``updateNavActive``) is the same
    rule: change one, change the other, and the ``PARITY_CASES`` tables in both
    tests (#3318).
    """
    if not _nav_url_is_plain_path(url):
        return False
    url, path = unquote(url), unquote(path)
    if url == "/":
        return path == "/"
    base = url.rstrip("/")
    return path == base or path.startswith(base + "/")


@register.simple_tag(takes_context=True)
def theme_nav_item(
    context: Context,
    label: str,
    url: str,
    icon: Optional[str] = None,
    active: Optional[bool] = None,
    badge: Optional[str] = None,
    **attrs: Any,
) -> SafeString:
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
    url = "" if url is None else str(url)
    url = _neutralise_url("url", url)

    # Auto-detect active state from request.path
    is_active = active
    if is_active is None and request is not None:
        request_path = getattr(request, "path", None)
        if request_path is not None:
            is_active = _nav_url_matches(url, request_path)

    ctx = {
        "label": label,
        "url": url,
        "icon": icon,
        "is_active": bool(is_active),
        # Marker for the client (#3318). Only a link whose state was
        # auto-detected from the path above carries it: components.js re-runs
        # that same rule on navigation, for a nav that sits outside dj-root and
        # is never re-rendered. An explicit ``active=`` is the app's call, and a
        # url that is not a plain path (``?``, ``#``, ``//``, a scheme) is not
        # a path match.
        "track_active": active is None and _nav_url_is_plain_path(url),
        "badge": badge,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_nav_group(
    context: Context,
    label: str,
    items: Any = None,
    icon: Optional[str] = None,
    expanded: bool = True,
    badge: Optional[str] = None,
    toggle_event: Optional[str] = None,
    **attrs: Any,
) -> SafeString:
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
        {% theme_nav_group_block "Settings" %}{% theme_nav_item "Home" "/" %}{% end_theme_nav_group_block %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_component_template(request, "nav_group")
    items = _neutralise_item_urls(items)
    ctx = {
        "label": label,
        "items": items or [],
        "icon": icon,
        "expanded": expanded,
        # A count on the heading, and an optional server event the heading
        # click also sends (``data-value`` = the label) so the expanded state
        # can live on the server and survive a re-render.
        "badge": badge,
        "toggle_event": toggle_event,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_nav(
    context: Context, brand: Optional[str] = None, items: Any = None, **attrs: Any
) -> SafeString:
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
    items = _neutralise_item_urls(items)
    ctx = {
        "brand": brand,
        "items": items or [],
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_sidebar_nav(context: Context, sections: Any = None, **attrs: Any) -> SafeString:
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
    # Sections wrap their own items: ``[{"title": ..., "items": [{"url": ...}]}]``.
    if isinstance(sections, (list, tuple)):
        sections = [
            {**section, "items": _neutralise_item_urls(section["items"])}
            if isinstance(section, dict) and section.get("items")
            else section
            for section in sections
        ]
    ctx = {
        "sections": sections or [],
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


# ---------------------------------------------------------------------------
# Block forms (#2894, #3279)
#
# `{% theme_card %}` and its siblings are `simple_tag`s: their body is an
# argument, so a body made of other template tags has to be pre-rendered in
# Python and passed through a `|safe` slot. The block forms below are the same
# components with the body written between an opening and a closing tag:
#
#     {% theme_card_block title="Welcome" %}
#         {% theme_button "Save" variant="primary" %}
#     {% end_theme_card_block %}
#
# Convention: block form = inline name + `_block`, closing tag
# `end_<name>_block`. The inline tags are unchanged. A block form takes the
# inline tag's arguments EXCEPT the one its body replaces, and it calls the
# inline function with the rendered body, so the context, the (per-theme)
# template and the escaping are the inline form's — there is one copy of each
# component's markup.
#
# The body is rendered by Django's own nodelist in the caller's context, so
# autoescaping applies to a variable inside it exactly as anywhere else in the
# template, and the rendered result is already safe when it reaches the
# component's `|safe` slot. These are raw `@register.tag` wrappers rather than
# `simple_block_tag` (Django 5.2+) because djust supports Django 4.2; a wrapper
# of this shape — one `parser.parse((end,))`, body rendered as-is — is bridged
# to djust's Rust engine by `djust.template_libraries` (ADR-030).
# ---------------------------------------------------------------------------


class _ThemeBlockNode(template.Node):
    """Render the body, then call the inline tag function with it."""

    def __init__(
        self,
        inline: Any,
        body_param: str,
        names: list[str],
        args: list[Any],
        kwargs: dict[str, Any],
        nodelist: Any,
    ) -> None:
        self.inline = inline
        self.body_param = body_param
        self.names = names
        self.args = args
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context: Context) -> str:
        # Strip the whitespace the tag's own line breaks add around the body:
        # it is markup noise in a block-level card and visible spacing inside
        # an inline wrapper such as the tooltip.
        body = mark_safe(self.nodelist.render(context).strip())
        call = {name: value.resolve(context) for name, value in zip(self.names, self.args)}
        call.update({key: value.resolve(context) for key, value in self.kwargs.items()})
        call[self.body_param] = body
        return str(self.inline(context, **call))


def _register_block_form(inline: Any, body_param: str, reserved: tuple[str, ...]) -> None:
    """Register ``<inline>_block`` as the block form of ``inline``.

    ``body_param`` is the keyword the rendered body is passed as; ``reserved``
    are the names a caller may not also pass, because the body already fills
    them (``body=`` and ``slot_body=`` on a card). Passing one is a
    ``TemplateSyntaxError`` at compile time rather than a body that silently
    wins or loses.
    """
    from inspect import getfullargspec, unwrap

    from django.template import TemplateSyntaxError
    from django.template.library import parse_bits

    name = f"{inline.__name__}_block"
    end_name = f"end_{name}"
    spec = getfullargspec(unwrap(inline))
    params = list(spec.args)
    defaults = list(spec.defaults or ())
    # Where the body parameter sat among the inline tag's positional arguments,
    # when it was one (alert's `message` is the first). Positional arguments at
    # or past that position would bind to different names than they do inline.
    body_position = params[1:].index(body_param) if body_param in params else None
    if body_param in params:
        # A required body parameter (alert's `message`) is filled by the block,
        # so it must not be asked for by the tag.
        index = params.index(body_param)
        first_default = len(params) - len(defaults)
        del params[index]
        if index >= first_default:
            del defaults[index - first_default]
    # `parse_bits` strips the leading `context` itself; the names positional
    # arguments bind to are what follows it.
    positional = params[1:]

    def compile_block(parser: Any, token: Any) -> _ThemeBlockNode:
        bits = token.split_contents()[1:]
        if len(bits) >= 2 and bits[-2] == "as":
            raise TemplateSyntaxError(
                f"'{name}' does not support 'as <variable>'; it renders in place"
            )
        args, kwargs = parse_bits(
            parser,
            bits,
            params,
            spec.varargs,
            spec.varkw,
            tuple(defaults) or None,
            spec.kwonlyargs,
            spec.kwonlydefaults,
            True,
            name,
        )
        if body_position is not None and len(args) > body_position:
            raise TemplateSyntaxError(
                f"'{name}' takes its {body_param} from the block between the tags, so "
                f"positional arguments would bind differently than in '{inline.__name__}': "
                "pass them as keywords"
            )
        clash = sorted(set(kwargs) & set(reserved))
        if clash:
            raise TemplateSyntaxError(
                f"'{name}' takes its {body_param} from the block between the tags, so it "
                f"cannot also be given {', '.join(clash)}="
            )
        nodelist = parser.parse((end_name,))
        parser.delete_first_token()
        return _ThemeBlockNode(inline, body_param, positional, args, kwargs, nodelist)

    register.tag(name, compile_block)


# (inline tag, keyword the block's body fills, names it may not also be given)
_register_block_form(theme_card, "body", ("body", "slot_body"))
_register_block_form(theme_alert, "message", ("message", "slot_message"))
_register_block_form(theme_modal, "slot_body", ("slot_body",))
_register_block_form(theme_dropdown, "slot_menu", ("slot_menu",))
_register_block_form(theme_tooltip, "slot_content", ("slot_content",))
_register_block_form(theme_nav_group, "slot_items", ("slot_items", "items"))


def _register_misspelt_end_tag(inline: Any) -> None:
    """``{% end_<inline> %}`` is an error that says where the block form is.

    ``{% theme_card %}…{% end_theme_card %}`` is the spelling the scaffold and
    the old docs showed (#2894). The inline tags stay inline tags, so the
    closing tag is NOT accepted; this only replaces Django's generic "Invalid
    block tag" with a pointer to the block form.
    """
    from django.template import TemplateSyntaxError

    end_name = f"end_{inline.__name__}"
    block = f"{inline.__name__}_block"

    def refuse(parser: Any, token: Any) -> Any:
        raise TemplateSyntaxError(
            f"'{end_name}' is not a closing tag: '{inline.__name__}' is an inline tag with "
            f"no body. To put template tags in the body use "
            f"{{% {block} %}}...{{% end_{block} %}}."
        )

    register.tag(end_name, refuse)


for _inline in (
    theme_card,
    theme_alert,
    theme_modal,
    theme_dropdown,
    theme_tooltip,
    theme_nav_group,
):
    _register_misspelt_end_tag(_inline)
del _inline
