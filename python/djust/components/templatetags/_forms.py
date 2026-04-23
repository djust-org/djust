"""
Form-related template tags — Slider, SearchInput, PasswordInput,
Autocomplete, DependentSelect, CurrencyInput, FormValidation,
Django Form Renderer, Django ModelForm Table.

Extracted from the monolithic djust_components.py for maintainability.
All tags register on the shared ``register`` from ``_registry``.
"""

from django import template

from ._registry import (
    register,
    _resolve,
    _parse_kv_args,
    conditional_escape,
    mark_safe,
    uuid,
    CURRENCY_SYMBOLS,
)

# ---------------------------------------------------------------------------

# --- Slider / Range ---


@register.simple_tag
def slider(
    name="",
    label="",
    min_val=0,
    max_val=100,
    step=1,
    value=None,
    value_end=None,
    event="",
    disabled=False,
    show_ticks=False,
    show_value=True,
    custom_class="",
    **kwargs,
):
    """Render a horizontal slider with optional range mode.

    Args:
        name: Input name attribute.
        label: Optional label text.
        min_val/max_val/step: Range bounds and step increment.
        value: Current value (or start value in range mode).
        value_end: End value — when set, enables dual-handle range mode.
        event: dj-input event name (defaults to name).
        show_ticks: Show tick marks along the track.
        show_value: Show current value output (default True).
        disabled: Disable the input.
        custom_class: Extra CSS class.

    Deprecated aliases (still accepted):
        min → min_val, max → max_val
    """
    # Backward-compat: accept deprecated 'min'/'max' kwargs
    if "min" in kwargs:
        min_val = kwargs.pop("min")
    if "max" in kwargs:
        max_val = kwargs.pop("max")

    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if isinstance(show_ticks, str):
        show_ticks = show_ticks.lower() not in ("false", "0", "")
    if isinstance(show_value, str):
        show_value = show_value.lower() not in ("false", "0", "")

    min_val = int(min_val)
    max_val = int(max_val)
    step_val = int(step)
    if value is None:
        value = min_val
    value = int(value)

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    dj_event = conditional_escape(event or name)
    e_class = conditional_escape(custom_class)

    disabled_attr = " disabled" if disabled else ""
    range_mode = value_end is not None
    if range_mode:
        value_end = int(value_end)

    cls = "dj-slider"
    if range_mode:
        cls += " dj-slider--range"
    if e_class:
        cls += f" {e_class}"

    label_html = (
        f'<label class="dj-slider__label" for="{e_name}">{e_label}</label>' if label else ""
    )

    value_display = ""
    if show_value:
        if range_mode:
            value_display = f'<output class="dj-slider__value">{value} &ndash; {value_end}</output>'
        else:
            value_display = f'<output class="dj-slider__value">{value}</output>'

    ticks_html = ""
    if show_ticks:
        tick_count = max(1, (max_val - min_val) // step_val)
        tick_items = "".join('<span class="dj-slider__tick"></span>' for _ in range(tick_count + 1))
        ticks_html = f'<div class="dj-slider__ticks">{tick_items}</div>'

    input_html = (
        f'<input type="range" class="dj-slider__input" '
        f'name="{e_name}" id="{e_name}" '
        f'min="{min_val}" max="{max_val}" step="{step_val}" '
        f'value="{value}" '
        f'dj-input="{dj_event}"{disabled_attr}>'
    )

    if range_mode:
        input_html += (
            f'<input type="range" class="dj-slider__input dj-slider__input--end" '
            f'name="{e_name}_end" id="{e_name}_end" '
            f'min="{min_val}" max="{max_val}" step="{step_val}" '
            f'value="{value_end}" '
            f'dj-input="{dj_event}"{disabled_attr}>'
        )

    return mark_safe(
        f'<div class="{cls}">'
        f"{label_html}"
        f'<div class="dj-slider__track">{input_html}</div>'
        f"{ticks_html}"
        f"{value_display}"
        f"</div>"
    )


# --- Search Input ---


@register.simple_tag
def search_input(
    name="",
    label="",
    value="",
    placeholder="Search...",
    event="",
    debounce=300,
    loading=False,
    disabled=False,
    custom_class="",
):
    """Render a search input with icon, clear button, and loading spinner.

    Args:
        name: Input name attribute.
        label: Optional label text.
        value: Current value.
        placeholder: Placeholder text (default "Search...").
        event: dj-input event name (defaults to name).
        debounce: Debounce delay in ms (default 300).
        loading: Show loading spinner.
        disabled: Disable the input.
        custom_class: Extra CSS class.
    """
    if isinstance(loading, str):
        loading = loading.lower() not in ("false", "0", "")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_value = conditional_escape(value)
    e_placeholder = conditional_escape(placeholder)
    dj_event = conditional_escape(event or name)
    e_class = conditional_escape(custom_class)
    debounce_val = int(debounce)

    disabled_attr = " disabled" if disabled else ""
    cls = "dj-search-input"
    if loading:
        cls += " dj-search-input--loading"
    if e_class:
        cls += f" {e_class}"

    label_html = (
        f'<label class="dj-search-input__label" for="{e_name}">{e_label}</label>' if label else ""
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
        f'name="{e_name}" id="{e_name}" value="{e_value}" '
        f'placeholder="{e_placeholder}" autocomplete="off" '
        f'dj-input="{dj_event}" data-debounce="{debounce_val}"{disabled_attr}>'
        f"{clear_html}"
        f"{spinner_html}"
        f"</div>"
    )


# --- Password Input ---


@register.simple_tag
def password_input(
    name="",
    label="",
    value="",
    placeholder="",
    event="",
    error="",
    required=False,
    disabled=False,
    show_strength=False,
    strength=0,
    custom_class="",
):
    """Render a password input with show/hide toggle and optional strength meter.

    Args:
        name: Input name attribute.
        label: Optional label text.
        value: Current value.
        placeholder: Placeholder text.
        event: dj-input event name (defaults to name).
        error: Error message text.
        required: Mark as required.
        disabled: Disable the input.
        show_strength: Show strength meter bar.
        strength: Strength value 0-4.
        custom_class: Extra CSS class.
    """
    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if isinstance(show_strength, str):
        show_strength = show_strength.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_value = conditional_escape(value)
    e_placeholder = conditional_escape(placeholder)
    dj_event = conditional_escape(event or name)
    e_error = conditional_escape(error)
    e_class = conditional_escape(custom_class)
    strength_val = max(0, min(4, int(strength)))

    required_attr = " required" if required else ""
    disabled_attr = " disabled" if disabled else ""

    cls = "dj-password-input"
    if error:
        cls += " dj-password-input--error"
    if e_class:
        cls += f" {e_class}"

    label_html = ""
    if label:
        req_span = '<span class="form-required"> *</span>' if required else ""
        label_html = f'<label class="form-label" for="{e_name}">{e_label}{req_span}</label>'

    error_html = f'<span class="form-error-message">{e_error}</span>' if error else ""

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
        s_cls = f"dj-password-strength--{strength_val}"
        strength_html = (
            f'<div class="dj-password-strength {s_cls}" '
            f'role="meter" aria-valuenow="{strength_val}" '
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
        f'name="{e_name}" id="{e_name}" value="{e_value}" '
        f'placeholder="{e_placeholder}" '
        f'dj-input="{dj_event}"{required_attr}{disabled_attr}>'
        f"{toggle_btn}"
        f"</div>"
        f"{strength_html}"
        f"{error_html}"
        f"</div>"
    )


# --- Autocomplete ---


@register.simple_tag
def autocomplete(
    name="",
    label="",
    value="",
    display_value="",
    placeholder="",
    source_event="",
    event="",
    debounce=300,
    min_chars=1,
    suggestions=None,
    loading=False,
    disabled=False,
    error="",
    required=False,
    custom_class="",
):
    """Render an autocomplete input with server-driven suggestions.

    Args:
        name: Input name attribute (hidden input carries the selected value).
        label: Optional label text.
        value: Selected value (submitted in form).
        display_value: Display text for the input (defaults to value).
        placeholder: Placeholder text.
        source_event: dj-input event for fetching suggestions from the server.
        event: dj-change event when a value is selected (defaults to name).
        debounce: Debounce delay in ms (default 300).
        min_chars: Minimum characters before triggering search (default 1).
        suggestions: List of suggestion dicts/tuples for current render.
        loading: Show loading spinner.
        disabled: Disable the input.
        error: Error message text.
        required: Mark as required.
        custom_class: Extra CSS class.
    """
    if isinstance(loading, str):
        loading = loading.lower() not in ("false", "0", "")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")
    if suggestions is None:
        suggestions = []
    if not isinstance(suggestions, (list, tuple)):
        suggestions = []

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_value = conditional_escape(str(value))
    e_display = conditional_escape(str(display_value or value))
    e_placeholder = conditional_escape(placeholder)
    e_source_event = conditional_escape(source_event)
    dj_event = conditional_escape(event or name)
    e_error = conditional_escape(error)
    e_class = conditional_escape(custom_class)
    debounce_val = int(debounce)
    min_chars_val = int(min_chars)

    disabled_attr = " disabled" if disabled else ""
    required_attr = " required" if required else ""

    cls = "dj-autocomplete"
    if loading:
        cls += " dj-autocomplete--loading"
    if error:
        cls += " dj-autocomplete--error"
    if e_class:
        cls += f" {e_class}"

    label_html = ""
    if label:
        req_span = '<span class="form-required"> *</span>' if required else ""
        label_html = f'<label class="form-label" for="{e_name}">{e_label}{req_span}</label>'

    error_html = f'<span class="form-error-message">{e_error}</span>' if error else ""

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

    suggestions_html = f'<ul class="{dropdown_cls}" role="listbox">{"".join(suggestion_items)}</ul>'

    spinner_html = (
        '<span class="dj-autocomplete__spinner" aria-hidden="true"></span>' if loading else ""
    )

    return mark_safe(
        f'<div class="form-group">'
        f"{label_html}"
        f'<div class="{cls}" data-source-event="{e_source_event}" '
        f'data-debounce="{debounce_val}" data-min-chars="{min_chars_val}">'
        f'<input type="text" class="dj-autocomplete__input form-input" '
        f'name="{e_name}_display" id="{e_name}" value="{e_display}" '
        f'placeholder="{e_placeholder}" autocomplete="off" '
        f'role="combobox" aria-autocomplete="list" '
        f'aria-expanded="{"true" if suggestion_items else "false"}" '
        f'dj-input="{e_source_event or dj_event}" '
        f'data-debounce="{debounce_val}"{required_attr}{disabled_attr}>'
        f'<input type="hidden" name="{e_name}" value="{e_value}">'
        f"{spinner_html}"
        f"{suggestions_html}"
        f"</div>"
        f"{error_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Confirmation Dialog
# ---------------------------------------------------------------------------


class ConfirmDialogNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        message = kw.get("message", "Are you sure?")
        confirm_event = kw.get("confirm_event", "confirm")
        cancel_event = kw.get("cancel_event", "cancel")
        title = kw.get("title", "Confirm")
        is_open = kw.get("is_open", kw.get("open", False))
        variant = kw.get("variant", "default")  # default or danger
        confirm_label = kw.get("confirm_label", "Confirm")
        cancel_label = kw.get("cancel_label", "Cancel")
        custom_class = kw.get("custom_class", "")

        if not is_open:
            return ""

        uid = uuid.uuid4().hex[:8]
        title_id = f"dj-confirm-title-{uid}"
        msg_id = f"dj-confirm-msg-{uid}"

        e_confirm_event = conditional_escape(confirm_event)
        e_cancel_event = conditional_escape(cancel_event)
        e_title = conditional_escape(title)
        e_message = conditional_escape(message)
        e_variant = conditional_escape(variant)
        e_confirm_label = conditional_escape(confirm_label)
        e_cancel_label = conditional_escape(cancel_label)
        e_custom_class = conditional_escape(custom_class)

        variant_cls = f" dj-confirm-dialog--{e_variant}" if variant != "default" else ""
        extra_cls = f" {e_custom_class}" if custom_class else ""

        return mark_safe(
            f'<div class="dj-confirm-dialog-backdrop" dj-click="{e_cancel_event}">'
            f'<div class="dj-confirm-dialog{variant_cls}{extra_cls}" '
            f'role="alertdialog" aria-modal="true" aria-labelledby="{title_id}" '
            f'aria-describedby="{msg_id}" onclick="event.stopPropagation()">'
            f'<div class="dj-confirm-dialog__header">'
            f'<h3 class="dj-confirm-dialog__title" id="{title_id}">{e_title}</h3>'
            f'<button class="dj-confirm-dialog__close" dj-click="{e_cancel_event}" '
            f'aria-label="Close">&times;</button>'
            f"</div>"
            f'<div class="dj-confirm-dialog__body" id="{msg_id}">'
            f'<p class="dj-confirm-dialog__message">{e_message}</p>'
            f"</div>"
            f'<div class="dj-confirm-dialog__footer">'
            f'<button class="dj-confirm-dialog__btn dj-confirm-dialog__btn--cancel" '
            f'dj-click="{e_cancel_event}">{e_cancel_label}</button>'
            f'<button class="dj-confirm-dialog__btn dj-confirm-dialog__btn--confirm" '
            f'dj-click="{e_confirm_event}">{e_confirm_label}</button>'
            f"</div>"
            f"</div>"
            f"</div>"
        )


@register.tag("confirm_dialog")
def do_confirm_dialog(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return ConfirmDialogNode(kwargs)


# ---------------------------------------------------------------------------
# Popconfirm
# ---------------------------------------------------------------------------


class PopconfirmNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        message = kw.get("message", "Are you sure?")
        confirm_event = kw.get("confirm_event", "confirm")
        cancel_event = kw.get("cancel_event", "cancel")
        confirm_label = kw.get("confirm_label", "Yes")
        cancel_label = kw.get("cancel_label", "No")
        placement = kw.get("placement", "top")
        variant = kw.get("variant", "default")  # default or danger
        custom_class = kw.get("custom_class", "")
        uid = kw.get("id", f"popconfirm-{uuid.uuid4().hex[:6]}")

        content = self.nodelist.render(context)

        e_message = conditional_escape(message)
        e_confirm_event = conditional_escape(confirm_event)
        e_cancel_event = conditional_escape(cancel_event)
        e_confirm_label = conditional_escape(confirm_label)
        e_cancel_label = conditional_escape(cancel_label)
        e_placement = conditional_escape(placement)
        e_variant = conditional_escape(variant)
        e_custom_class = conditional_escape(custom_class)
        e_uid = conditional_escape(uid)

        variant_cls = f" dj-popconfirm--{e_variant}" if variant != "default" else ""
        extra_cls = f" {e_custom_class}" if custom_class else ""

        js_toggle = (
            "(function(el){"
            "var w=el.closest('.dj-popconfirm-wrapper');"
            "if(w.hasAttribute('data-open')){w.removeAttribute('data-open');}else{w.setAttribute('data-open','');"
            "document.addEventListener('click',function h(e){"
            "if(!w.contains(e.target)){"
            "w.removeAttribute('data-open');"
            "document.removeEventListener('click',h);"
            "}},true);"
            "}})(this)"
        )

        js_close = (
            "(function(el){"
            "el.closest('.dj-popconfirm-wrapper').removeAttribute('data-open');"
            "})(this)"
        )

        return mark_safe(
            f'<div class="dj-popconfirm-wrapper{variant_cls}{extra_cls}" id="{e_uid}">'
            f'<div class="dj-popconfirm-trigger" onclick="{js_toggle}" '
            f'aria-expanded="false" aria-haspopup="true">'
            f"{content}"
            f"</div>"
            f'<div class="dj-popconfirm dj-popconfirm-{e_placement}" role="tooltip">'
            f'<p class="dj-popconfirm__message">{e_message}</p>'
            f'<div class="dj-popconfirm__actions">'
            f'<button class="dj-popconfirm__btn dj-popconfirm__btn--cancel" '
            f'onclick="{js_close}" dj-click="{e_cancel_event}">{e_cancel_label}</button>'
            f'<button class="dj-popconfirm__btn dj-popconfirm__btn--confirm" '
            f'onclick="{js_close}" dj-click="{e_confirm_event}">{e_confirm_label}</button>'
            f"</div>"
            f"</div>"
            f"</div>"
        )


@register.tag("popconfirm")
def do_popconfirm(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    nodelist = parser.parse(("endpopconfirm",))
    parser.delete_first_token()
    return PopconfirmNode(nodelist, kwargs)


# ---------------------------------------------------------------------------
# CASCADING FORM COMPONENTS
# ---------------------------------------------------------------------------

# --- Dependent Select (#108) ---


@register.simple_tag
def dependent_select(
    name="",
    parent="",
    source_event="",
    label="",
    placeholder="Select...",
    value="",
    options=None,
    loading=False,
    disabled=False,
    required=False,
    error="",
    custom_class="",
):
    """Cascading dropdown that reloads options when parent field changes.

    Args:
        name: Input name attribute.
        parent: Name of the parent field this select depends on.
        source_event: djust event name to fire when parent changes (loads new options).
        label: Optional label text.
        placeholder: Placeholder text when nothing selected.
        value: Currently selected value.
        options: List of dicts with 'value' and 'label' keys, or list of strings.
        loading: Show spinner while loading options.
        disabled: Disable the select.
        required: Mark field as required.
        error: Error message to display.
        custom_class: Extra CSS class.
    """
    if isinstance(loading, str):
        loading = loading.lower() not in ("false", "0", "")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_parent = conditional_escape(parent)
    e_source_event = conditional_escape(source_event or name)
    e_label = conditional_escape(label)
    e_placeholder = conditional_escape(placeholder)
    e_value = conditional_escape(str(value))
    e_error = conditional_escape(error)
    e_class = conditional_escape(custom_class)

    if options is None:
        options = []

    disabled_attr = " disabled" if disabled else ""
    required_attr = " required" if required else ""

    cls = "dj-dependent-select"
    if loading:
        cls += " dj-dependent-select--loading"
    if error:
        cls += " dj-dependent-select--error"
    if e_class:
        cls += f" {e_class}"

    label_html = ""
    if label:
        req_mark = ' <span class="form-required">*</span>' if required else ""
        label_html = f'<label class="form-label" for="{e_name}">{e_label}{req_mark}</label>'

    # Build options
    opt_parts = [f'<option value="">{e_placeholder}</option>']
    for opt in options:
        if isinstance(opt, dict):
            ov = conditional_escape(str(opt.get("value", "")))
            ol = conditional_escape(str(opt.get("label", ov)))
        else:
            ov = conditional_escape(str(opt))
            ol = ov
        selected = " selected" if ov == e_value else ""
        opt_parts.append(f'<option value="{ov}"{selected}>{ol}</option>')

    spinner_html = (
        '<span class="dj-dependent-select__spinner" aria-hidden="true"></span>' if loading else ""
    )

    error_html = f'<span class="form-error-message" role="alert">{e_error}</span>' if error else ""

    return mark_safe(
        f'<div class="{cls}">'
        f"{label_html}"
        f'<div class="dj-dependent-select__control">'
        f'<select name="{e_name}" id="{e_name}" '
        f'data-parent="{e_parent}" '
        f'data-source-event="{e_source_event}" '
        f'dj-change="{e_source_event}"'
        f"{disabled_attr}{required_attr}>"
        f"{''.join(opt_parts)}"
        f"</select>"
        f"{spinner_html}"
        f"</div>"
        f"{error_html}"
        f"</div>"
    )


# --- Currency Input (#109) ---

# CURRENCY_SYMBOLS imported from djust.components.utils at module top


@register.simple_tag
def currency_input(
    name="",
    currency="USD",
    value="",
    label="",
    min_val=None,
    max_val=None,
    step="0.01",
    placeholder="0.00",
    event="",
    disabled=False,
    required=False,
    error="",
    custom_class="",
    **kwargs,
):
    """Numeric input with currency symbol prefix and formatting hints.

    Args:
        name: Input name attribute.
        currency: Currency code (e.g. USD, EUR, GBP). Determines prefix symbol.
        value: Current numeric value.
        label: Optional label text.
        min_val: Minimum value.
        max_val: Maximum value.
        step: Step increment (default 0.01 for cents).
        placeholder: Placeholder text.
        event: dj-input event name (defaults to name).
        disabled: Disable the input.
        required: Mark field as required.
        error: Error message to display.
        custom_class: Extra CSS class.

    Deprecated aliases (still accepted):
        min → min_val, max → max_val
    """
    # Backward-compat: accept deprecated 'min'/'max' kwargs
    if "min" in kwargs:
        min_val = kwargs.pop("min")
    if "max" in kwargs:
        max_val = kwargs.pop("max")
    if isinstance(disabled, str):
        disabled = disabled.lower() not in ("false", "0", "")
    if isinstance(required, str):
        required = required.lower() not in ("false", "0", "")

    e_name = conditional_escape(name)
    e_currency = conditional_escape(str(currency).upper())
    e_value = conditional_escape(str(value))
    e_label = conditional_escape(label)
    e_placeholder = conditional_escape(placeholder)
    e_step = conditional_escape(str(step))
    e_event = conditional_escape(event or name)
    e_error = conditional_escape(error)
    e_class = conditional_escape(custom_class)

    symbol = CURRENCY_SYMBOLS.get(str(currency).upper(), str(currency).upper())
    e_symbol = conditional_escape(symbol)

    disabled_attr = " disabled" if disabled else ""
    required_attr = " required" if required else ""
    min_attr = f' min="{conditional_escape(str(min_val))}"' if min_val is not None else ""
    max_attr = f' max="{conditional_escape(str(max_val))}"' if max_val is not None else ""

    cls = "dj-currency-input"
    if error:
        cls += " dj-currency-input--error"
    if e_class:
        cls += f" {e_class}"

    label_html = ""
    if label:
        req_mark = ' <span class="form-required">*</span>' if required else ""
        label_html = f'<label class="form-label" for="{e_name}">{e_label}{req_mark}</label>'

    error_html = f'<span class="form-error-message" role="alert">{e_error}</span>' if error else ""

    return mark_safe(
        f'<div class="{cls}">'
        f"{label_html}"
        f'<div class="dj-currency-input__control">'
        f'<span class="dj-currency-input__symbol">{e_symbol}</span>'
        f'<input type="number" name="{e_name}" id="{e_name}" '
        f'value="{e_value}" placeholder="{e_placeholder}" '
        f'step="{e_step}"{min_attr}{max_attr} '
        f'data-currency="{e_currency}" '
        f'dj-input="{e_event}" '
        f'class="dj-currency-input__field"'
        f"{disabled_attr}{required_attr}>"
        f'<span class="dj-currency-input__code">{e_currency}</span>'
        f"</div>"
        f"{error_html}"
        f"</div>"
    )


# --- Form Validation Display (#110) ---


@register.simple_tag
def form_errors(form=None, custom_class=""):
    """Render all form-level (non-field) validation errors.

    Args:
        form: A Django form instance.
        custom_class: Extra CSS class.
    """
    if form is None or not hasattr(form, "non_field_errors"):
        return ""

    errors = form.non_field_errors()
    if not errors:
        return ""

    e_class = conditional_escape(custom_class)
    cls = "dj-form-errors"
    if e_class:
        cls += f" {e_class}"

    items = []
    for err in errors:
        items.append(f'<li class="dj-form-errors__item">{conditional_escape(str(err))}</li>')

    return mark_safe(
        f'<div class="{cls}" role="alert">'
        f'<ul class="dj-form-errors__list">{"".join(items)}</ul>'
        f"</div>"
    )


@register.simple_tag
def field_error(field=None, custom_class=""):
    """Render inline validation error for a single form field.

    Args:
        field: A Django BoundField instance (e.g. form.email).
        custom_class: Extra CSS class.
    """
    if field is None:
        return ""

    # Support both BoundField and a raw errors list
    if hasattr(field, "errors"):
        errors = field.errors
    else:
        return ""

    if not errors:
        return ""

    e_class = conditional_escape(custom_class)
    cls = "dj-field-error"
    if e_class:
        cls += f" {e_class}"

    items = []
    for err in errors:
        items.append(f'<span class="dj-field-error__message">{conditional_escape(str(err))}</span>')

    return mark_safe(f'<div class="{cls}" role="alert">{"".join(items)}</div>')


# ---------------------------------------------------------------------------
# DJANGO INTEGRATION COMPONENTS
# ---------------------------------------------------------------------------

# --- Django Form Renderer (#73) ---

# Mapping of Django form field class names to djust component renderers.
_FIELD_TYPE_MAP = {
    "CharField": "text",
    "EmailField": "email",
    "URLField": "url",
    "IntegerField": "number",
    "FloatField": "number",
    "DecimalField": "number",
    "DateField": "date",
    "DateTimeField": "datetime-local",
    "TimeField": "time",
    "SlugField": "text",
    "UUIDField": "text",
    "GenericIPAddressField": "text",
    "FilePathField": "text",
    "TypedChoiceField": "select",
    "ChoiceField": "select",
    "ModelChoiceField": "select",
    "BooleanField": "checkbox",
    "NullBooleanField": "checkbox",
    "FileField": "file",
    "ImageField": "file",
    "TypedMultipleChoiceField": "select_multiple",
    "MultipleChoiceField": "select_multiple",
    "ModelMultipleChoiceField": "select_multiple",
}


def _get_field_type(bound_field):
    """Determine the djust component type for a Django BoundField."""
    field = bound_field.field
    cls_name = type(field).__name__

    # Check widget override — textarea widget means textarea
    widget_cls = type(field.widget).__name__ if hasattr(field, "widget") else ""
    if widget_cls in ("Textarea", "AdminTextareaWidget"):
        return "textarea"
    if widget_cls in ("CheckboxInput",):
        return "checkbox"
    if widget_cls in ("RadioSelect",):
        return "radio_group"
    if widget_cls in ("CheckboxSelectMultiple",):
        return "checkbox_group"
    if widget_cls in ("Select", "NullBooleanSelect"):
        if cls_name not in ("BooleanField", "NullBooleanField"):
            return "select"
    if widget_cls in ("SelectMultiple",):
        return "select_multiple"
    if widget_cls in ("PasswordInput",):
        return "password"
    if widget_cls in ("HiddenInput", "MultipleHiddenInput"):
        return "hidden"
    if widget_cls in ("FileInput", "ClearableFileInput"):
        return "file"

    return _FIELD_TYPE_MAP.get(cls_name, "text")


def _get_choices(bound_field):
    """Extract choices from a Django BoundField as list of (value, label) tuples."""
    field = bound_field.field
    if hasattr(field, "choices"):
        choices = field.choices
        # choices can be a callable
        if callable(choices):
            choices = choices()
        return [(str(v), str(l)) for v, l in choices]
    return []


def _render_field(bound_field, event_prefix=""):
    """Render a single Django BoundField as the appropriate djust component HTML."""
    field_type = _get_field_type(bound_field)
    name = bound_field.html_name if hasattr(bound_field, "html_name") else bound_field.name
    label = bound_field.label or ""
    help_text = (
        str(bound_field.help_text)
        if hasattr(bound_field, "help_text") and bound_field.help_text
        else ""
    )
    required = bound_field.field.required if hasattr(bound_field, "field") else False
    disabled = (
        getattr(bound_field.field, "disabled", False) if hasattr(bound_field, "field") else False
    )
    errors = (
        list(bound_field.errors) if hasattr(bound_field, "errors") and bound_field.errors else []
    )
    error_msg = errors[0] if errors else ""

    # Get current value
    value = ""
    if hasattr(bound_field, "value"):
        v = bound_field.value()
        if v is not None:
            value = str(v)

    e_name = conditional_escape(name)
    e_label = conditional_escape(label)
    e_value = conditional_escape(value)
    e_helper = conditional_escape(help_text)
    e_error = conditional_escape(error_msg)
    dj_event = conditional_escape(event_prefix + name if event_prefix else name)

    required_attr = " required" if required else ""
    disabled_attr = " disabled" if disabled else ""

    required_span = '<span class="form-required"> *</span>' if required else ""
    error_cls = " form-input-error" if error_msg else ""
    label_html = (
        f'<label class="form-label" for="{e_name}">{e_label}{required_span}</label>'
        if label
        else ""
    )
    error_html = f'<span class="form-error-message">{e_error}</span>' if error_msg else ""
    helper_html = f'<span class="form-helper">{e_helper}</span>' if help_text else ""

    # Render all field errors (multiple) below the first
    extra_errors_html = ""
    if len(errors) > 1:
        extra_items = "".join(
            f'<span class="form-error-message">{conditional_escape(str(e))}</span>'
            for e in errors[1:]
        )
        extra_errors_html = extra_items

    if field_type == "hidden":
        return f'<input type="hidden" name="{e_name}" id="{e_name}" value="{e_value}">'

    if field_type == "checkbox":
        checked_attr = " checked" if value and value not in ("False", "false", "0", "") else ""
        return (
            f'<div class="form-group">'
            f'<div class="form-checkbox-wrapper">'
            f'<input class="form-checkbox" type="checkbox" '
            f'name="{e_name}" id="{e_name}" value="on" '
            f'dj-change="{dj_event}"{checked_attr}{required_attr}{disabled_attr}>'
            f'<label class="form-checkbox-label" for="{e_name}">{e_label}</label>'
            f"</div>"
            f"{error_html}{extra_errors_html}{helper_html}"
            f"</div>"
        )

    if field_type == "textarea":
        return (
            f'<div class="form-group">'
            f"{label_html}"
            f'<textarea class="form-input{error_cls}" name="{e_name}" id="{e_name}" '
            f'rows="4" dj-input="{dj_event}"{required_attr}{disabled_attr}>'
            f"{e_value}</textarea>"
            f"{error_html}{extra_errors_html}{helper_html}"
            f"</div>"
        )

    if field_type in ("select", "select_multiple"):
        choices = _get_choices(bound_field)
        multiple_attr = " multiple" if field_type == "select_multiple" else ""
        select_error_cls = " form-select-error" if error_msg else ""
        options_html = ""
        for ov, ol in choices:
            selected_attr = " selected" if str(ov) == str(value) else ""
            options_html += (
                f'<option value="{conditional_escape(ov)}"{selected_attr}>'
                f"{conditional_escape(ol)}</option>"
            )
        return (
            f'<div class="form-group">'
            f"{label_html}"
            f'<select class="form-select{select_error_cls}" name="{e_name}" id="{e_name}" '
            f'dj-change="{dj_event}"{required_attr}{disabled_attr}{multiple_attr}>'
            f"{options_html}"
            f"</select>"
            f"{error_html}{extra_errors_html}{helper_html}"
            f"</div>"
        )

    if field_type == "radio_group":
        choices = _get_choices(bound_field)
        radios = ""
        for ov, ol in choices:
            checked_attr = " checked" if str(ov) == str(value) else ""
            radio_id = conditional_escape(f"{name}_{ov}")
            radios += (
                f'<div class="form-radio-wrapper">'
                f'<input class="form-radio" type="radio" '
                f'name="{e_name}" id="{radio_id}" value="{conditional_escape(ov)}" '
                f'dj-change="{dj_event}"{checked_attr}{disabled_attr}>'
                f'<label class="form-radio-label" for="{radio_id}">{conditional_escape(ol)}</label>'
                f"</div>"
            )
        return (
            f'<div class="form-group">'
            f"{label_html}"
            f"{radios}"
            f"{error_html}{extra_errors_html}{helper_html}"
            f"</div>"
        )

    if field_type == "checkbox_group":
        choices = _get_choices(bound_field)
        # value might be a list for multiple checkboxes
        selected_values = value.split(",") if value else []
        checks = ""
        for ov, ol in choices:
            checked_attr = " checked" if ov in selected_values else ""
            cb_id = conditional_escape(f"{name}_{ov}")
            checks += (
                f'<div class="form-checkbox-wrapper">'
                f'<input class="form-checkbox" type="checkbox" '
                f'name="{e_name}" id="{cb_id}" value="{conditional_escape(ov)}" '
                f'dj-change="{dj_event}"{checked_attr}{disabled_attr}>'
                f'<label class="form-checkbox-label" for="{cb_id}">{conditional_escape(ol)}</label>'
                f"</div>"
            )
        return (
            f'<div class="form-group">'
            f"{label_html}"
            f"{checks}"
            f"{error_html}{extra_errors_html}{helper_html}"
            f"</div>"
        )

    if field_type == "file":
        return (
            f'<div class="form-group">'
            f"{label_html}"
            f'<input class="form-input" type="file" '
            f'name="{e_name}" id="{e_name}"{required_attr}{disabled_attr}>'
            f"{error_html}{extra_errors_html}{helper_html}"
            f"</div>"
        )

    if field_type == "password":
        return (
            f'<div class="form-group">'
            f"{label_html}"
            f'<input class="form-input{error_cls}" type="password" '
            f'name="{e_name}" id="{e_name}" '
            f'dj-input="{dj_event}"{required_attr}{disabled_attr}>'
            f"{error_html}{extra_errors_html}{helper_html}"
            f"</div>"
        )

    # Default: text-like input (text, email, url, number, date, etc.)
    input_type = (
        field_type
        if field_type in ("email", "url", "number", "date", "datetime-local", "time")
        else "text"
    )
    e_type = conditional_escape(input_type)
    placeholder = ""
    if hasattr(bound_field.field, "widget") and hasattr(bound_field.field.widget, "attrs"):
        placeholder = bound_field.field.widget.attrs.get("placeholder", "")
    e_placeholder = conditional_escape(placeholder)

    return (
        f'<div class="form-group">'
        f"{label_html}"
        f'<input class="form-input{error_cls}" type="{e_type}" '
        f'name="{e_name}" id="{e_name}" value="{e_value}" '
        f'placeholder="{e_placeholder}" '
        f'dj-input="{dj_event}"{required_attr}{disabled_attr}>'
        f"{error_html}{extra_errors_html}{helper_html}"
        f"</div>"
    )


@register.simple_tag
def dj_form(
    form=None,
    event_prefix="",
    action="",
    method="post",
    submit_label="Submit",
    submit_event="",
    custom_class="",
    show_errors=True,
):
    """Auto-render a Django Form or ModelForm using djust-components.

    Maps Django field types to djust input components:
      CharField -> dj_input (text)
      EmailField -> dj_input (email)
      ChoiceField -> dj_select
      BooleanField -> dj_checkbox
      TextField/Textarea widget -> dj_textarea
      etc.

    Args:
        form: A Django Form or ModelForm instance.
        event_prefix: Prefix for dj-input/dj-change event names (e.g. "myform_").
        action: Form action URL (empty = no action attribute).
        method: Form method (default "post").
        submit_label: Label for the submit button.
        submit_event: djust event for the submit button (if empty, uses standard form submit).
        custom_class: Extra CSS class for the form wrapper.
        show_errors: Show non-field errors at the top (default True).
    """
    if form is None:
        return ""

    e_class = conditional_escape(custom_class)
    e_action = conditional_escape(action)
    e_method = conditional_escape(method)
    e_submit_label = conditional_escape(submit_label)
    e_submit_event = conditional_escape(submit_event)

    cls = "dj-form"
    if e_class:
        cls += f" {e_class}"

    # Non-field errors
    errors_html = ""
    if show_errors and hasattr(form, "non_field_errors"):
        non_field = form.non_field_errors()
        if non_field:
            items = "".join(
                f'<li class="dj-form-errors__item">{conditional_escape(str(e))}</li>'
                for e in non_field
            )
            errors_html = (
                f'<div class="dj-form-errors" role="alert">'
                f'<ul class="dj-form-errors__list">{items}</ul>'
                f"</div>"
            )

    # Render each visible field
    fields_html = ""
    visible_fields = form.visible_fields() if hasattr(form, "visible_fields") else []
    for bf in visible_fields:
        fields_html += _render_field(bf, event_prefix=event_prefix)

    # Hidden fields
    hidden_html = ""
    hidden_fields = form.hidden_fields() if hasattr(form, "hidden_fields") else []
    for bf in hidden_fields:
        h_name = bf.html_name if hasattr(bf, "html_name") else bf.name
        h_value = ""
        if hasattr(bf, "value"):
            v = bf.value()
            if v is not None:
                h_value = str(v)
        hidden_html += (
            f'<input type="hidden" name="{conditional_escape(h_name)}" '
            f'id="{conditional_escape(h_name)}" value="{conditional_escape(h_value)}">'
        )

    # Action/method attributes
    action_attr = f' action="{e_action}"' if action else ""
    method_attr = f' method="{e_method}"'

    # Submit button
    if e_submit_event:
        submit_html = (
            f'<div class="form-group dj-form__actions">'
            f'<button class="dj-btn dj-btn--primary" type="button" '
            f'dj-click="{e_submit_event}">{e_submit_label}</button>'
            f"</div>"
        )
    else:
        submit_html = (
            f'<div class="form-group dj-form__actions">'
            f'<button class="dj-btn dj-btn--primary" type="submit">'
            f"{e_submit_label}</button>"
            f"</div>"
        )

    return mark_safe(
        f'<form class="{cls}"{action_attr}{method_attr}>'
        f"{errors_html}"
        f"{fields_html}"
        f"{hidden_html}"
        f"{submit_html}"
        f"</form>"
    )


# --- Django ModelForm Table (#74) ---


def _get_verbose_name(field):
    """Get verbose name from a Django model field."""
    if hasattr(field, "verbose_name"):
        return str(field.verbose_name).title()
    return str(field.name).replace("_", " ").title()


def _is_sortable_field(field):
    """Determine if a model field should be sortable."""
    cls_name = type(field).__name__
    # Most concrete fields are sortable; relations and file fields are not
    non_sortable = {
        "ManyToManyField",
        "ManyToManyRel",
        "ManyToOneRel",
        "FileField",
        "ImageField",
        "JSONField",
    }
    return cls_name not in non_sortable


def _is_filterable_field(field):
    """Determine if a model field should be filterable."""
    cls_name = type(field).__name__
    filterable = {
        "CharField",
        "TextField",
        "SlugField",
        "EmailField",
        "URLField",
        "BooleanField",
        "NullBooleanField",
        "IntegerField",
        "FloatField",
        "DecimalField",
        "ChoiceField",
        "ForeignKey",
    }
    return cls_name in filterable


def _get_filter_type(field):
    """Get appropriate filter type for a model field."""
    cls_name = type(field).__name__
    if cls_name in ("BooleanField", "NullBooleanField"):
        return "select"
    if cls_name in ("IntegerField", "FloatField", "DecimalField"):
        return "number"
    if cls_name == "ForeignKey":
        return "select"
    if hasattr(field, "choices") and field.choices:
        return "select"
    return "text"


def _get_filter_options(field):
    """Get filter options for fields with choices."""
    if hasattr(field, "choices") and field.choices:
        return [{"value": str(v), "label": str(l)} for v, l in field.choices]
    cls_name = type(field).__name__
    if cls_name in ("BooleanField",):
        return [{"value": "true", "label": "Yes"}, {"value": "false", "label": "No"}]
    if cls_name in ("NullBooleanField",):
        return [
            {"value": "true", "label": "Yes"},
            {"value": "false", "label": "No"},
            {"value": "null", "label": "Unknown"},
        ]
    return []


def _infer_columns(model_meta, exclude=None, include=None):
    """Infer data_table columns from a Django model's _meta."""
    exclude = set(exclude or [])
    fields = model_meta.get_fields() if hasattr(model_meta, "get_fields") else []

    columns = []
    for field in fields:
        # Skip reverse relations
        if hasattr(field, "related_model") and not hasattr(field, "column"):
            continue
        name = field.name if hasattr(field, "name") else str(field)
        if name in exclude:
            continue
        if include and name not in include:
            continue

        col = {
            "key": name,
            "label": _get_verbose_name(field),
            "sortable": _is_sortable_field(field),
        }
        if _is_filterable_field(field):
            col["filterable"] = True
            col["filter_type"] = _get_filter_type(field)
            options = _get_filter_options(field)
            if options:
                col["filter_options"] = options

        columns.append(col)

    return columns


def _queryset_to_rows(queryset, columns, row_key="id"):
    """Convert a Django QuerySet to a list of row dicts for data_table."""
    rows = []
    col_keys = [c["key"] for c in columns]
    for obj in queryset:
        row = {}
        for key in col_keys:
            val = getattr(obj, key, "")
            # Handle ForeignKey — use str() for display
            if hasattr(val, "pk"):
                val = str(val)
            elif callable(val) and not isinstance(val, str):
                try:
                    val = val()
                except Exception:
                    val = ""
            row[key] = val if val is not None else ""
        # Ensure row_key is present
        if row_key not in row:
            pk = getattr(obj, "pk", None) or getattr(obj, "id", None) or ""
            row[row_key] = pk
        rows.append(row)
    return rows


@register.simple_tag
def model_table(
    queryset=None,
    exclude=None,
    include=None,
    sort_by="",
    sort_desc=False,
    sort_event="table_sort",
    page=1,
    total_pages=1,
    paginate=False,
    page_event="table_page",
    prev_event="table_prev",
    next_event="table_next",
    search=False,
    search_query="",
    search_event="table_search",
    filters=None,
    filter_event="table_filter",
    selectable=False,
    selected_rows=None,
    select_event="table_select",
    row_key="id",
    loading=False,
    empty_title="No data",
    empty_description="",
    striped=True,
    compact=False,
    custom_class="",
):
    """Auto-generate a Data Table Pro from a Django QuerySet.

    Introspects model fields to infer columns. Supports sorting, filtering,
    pagination, search, and selection — all delegated to the existing data_table
    component.

    Args:
        queryset: A Django QuerySet instance.
        exclude: List of field names to exclude from the table.
        include: List of field names to include (if set, only these are shown).
        sort_by: Current sort column key.
        sort_desc: Sort descending?
        sort_event: djust event for sorting.
        page: Current page number.
        total_pages: Total pages.
        paginate: Show pagination controls.
        page_event: Pagination event name.
        prev_event: Previous page event.
        next_event: Next page event.
        search: Show global search box.
        search_query: Current search value.
        search_event: Search event name.
        filters: Per-column filter values dict.
        filter_event: Filter event name.
        selectable: Enable row selection.
        selected_rows: List of selected row IDs.
        select_event: Selection event name.
        row_key: Key field for row identity.
        loading: Show loading state.
        empty_title: Empty state title.
        empty_description: Empty state description.
        striped: Alternating row backgrounds (default True for model tables).
        compact: Reduced padding.
        custom_class: Extra CSS class for the wrapper div.
    """
    if queryset is None:
        return ""

    # Get model metadata
    model = None
    if hasattr(queryset, "model"):
        model = queryset.model
    elif hasattr(queryset, "_meta"):
        model = queryset

    if model is None:
        return "<!-- model_table: queryset has no model -->"

    meta = model._meta if hasattr(model, "_meta") else None
    if meta is None:
        return "<!-- model_table: model has no _meta -->"

    # Infer columns
    columns = _infer_columns(meta, exclude=exclude, include=include)
    if not columns:
        return "<!-- model_table: no columns inferred -->"

    # Convert queryset to rows
    rows = _queryset_to_rows(queryset, columns, row_key=row_key)

    # Escape values in rows for safe rendering
    safe_rows = []
    for row in rows:
        safe_row = {}
        for k, v in row.items():
            safe_row[k] = conditional_escape(str(v)) if v is not None else ""
        safe_rows.append(safe_row)

    # Build the table HTML directly (composing the data_table pattern)
    e_class = conditional_escape(custom_class)
    e_sort_event = conditional_escape(sort_event)
    e_search_event = conditional_escape(search_event)
    e_filter_event = conditional_escape(filter_event)
    e_prev_event = conditional_escape(prev_event)
    e_next_event = conditional_escape(next_event)
    e_select_event = conditional_escape(select_event)
    e_empty_title = conditional_escape(empty_title)
    e_empty_desc = conditional_escape(empty_description)
    e_search_query = conditional_escape(search_query)

    if selected_rows is None:
        selected_rows = []
    if filters is None:
        filters = {}

    try:
        page = int(page)
    except (ValueError, TypeError):
        page = 1
    try:
        total_pages = int(total_pages)
    except (ValueError, TypeError):
        total_pages = 1

    wrapper_cls = "dj-model-table"
    if e_class:
        wrapper_cls += f" {e_class}"

    density_cls = " data-table--compact" if compact else ""
    striped_cls = " data-table--striped" if striped else ""

    # Search bar
    search_html = ""
    if search:
        search_html = (
            f'<div class="data-table__search">'
            f'<input type="text" class="data-table__search-input" '
            f'placeholder="Search..." value="{e_search_query}" '
            f'dj-input="{e_search_event}">'
            f"</div>"
        )

    # Table header
    header_cells = ""
    if selectable:
        header_cells += (
            '<th class="data-table__th data-table__th--select"><input type="checkbox"></th>'
        )
    for col in columns:
        col_key = conditional_escape(col["key"])
        col_label = conditional_escape(col["label"])
        sort_cls = ""
        sort_attr = ""
        if col.get("sortable"):
            sort_cls = " data-table__th--sortable"
            sort_attr = f' dj-click="{e_sort_event}" dj-value-column="{col_key}"'
            if sort_by == col["key"]:
                arrow = " &#9660;" if sort_desc else " &#9650;"
                col_label += arrow
                sort_cls += " data-table__th--sorted"

        # Filter row is separate; just header for now
        filter_attr = ""
        header_cells += (
            f'<th class="data-table__th{sort_cls}"{sort_attr}{filter_attr}>{col_label}</th>'
        )

    # Filter row
    filter_row = ""
    has_filters = any(col.get("filterable") for col in columns)
    if has_filters:
        filter_cells = ""
        if selectable:
            filter_cells += '<td class="data-table__filter-cell"></td>'
        for col in columns:
            if col.get("filterable"):
                col_key = conditional_escape(col["key"])
                fval = conditional_escape(str(filters.get(col["key"], "")))
                ft = col.get("filter_type", "text")
                if ft == "select" and col.get("filter_options"):
                    opts = '<option value="">All</option>'
                    for fo in col["filter_options"]:
                        fov = conditional_escape(str(fo.get("value", "")))
                        fol = conditional_escape(str(fo.get("label", "")))
                        sel = " selected" if fov == fval else ""
                        opts += f'<option value="{fov}"{sel}>{fol}</option>'
                    filter_cells += (
                        f'<td class="data-table__filter-cell">'
                        f'<select class="data-table__filter-select" '
                        f'dj-change="{e_filter_event}" dj-value-column="{col_key}">'
                        f"{opts}</select></td>"
                    )
                else:
                    filter_cells += (
                        f'<td class="data-table__filter-cell">'
                        f'<input type="{conditional_escape(ft)}" '
                        f'class="data-table__filter-input" value="{fval}" '
                        f'dj-input="{e_filter_event}" dj-value-column="{col_key}">'
                        f"</td>"
                    )
            else:
                filter_cells += '<td class="data-table__filter-cell"></td>'
        filter_row = f'<tr class="data-table__filter-row">{filter_cells}</tr>'

    # Table body
    body_rows = ""
    if loading:
        col_count = len(columns) + (1 if selectable else 0)
        body_rows = (
            f'<tr class="data-table__loading-row">'
            f'<td colspan="{col_count}" class="data-table__loading-cell">'
            f'<div class="dj-spinner"></div></td></tr>'
        )
    elif not safe_rows:
        col_count = len(columns) + (1 if selectable else 0)
        body_rows = (
            f'<tr class="data-table__empty-row">'
            f'<td colspan="{col_count}" class="data-table__empty-cell">'
            f'<div class="data-table__empty-title">{e_empty_title}</div>'
            f'<div class="data-table__empty-desc">{e_empty_desc}</div>'
            f"</td></tr>"
        )
    else:
        for row in safe_rows:
            row_id = row.get(row_key, "")
            row_selected = str(row_id) in [str(s) for s in selected_rows]
            selected_cls = " data-table__tr--selected" if row_selected else ""
            cells = ""
            if selectable:
                chk = " checked" if row_selected else ""
                cells += (
                    f'<td class="data-table__td data-table__td--select">'
                    f'<input type="checkbox" dj-change="{e_select_event}" '
                    f'dj-value-row="{conditional_escape(str(row_id))}"{chk}></td>'
                )
            for col in columns:
                cell_val = row.get(col["key"], "")
                cells += f'<td class="data-table__td">{cell_val}</td>'
            body_rows += f'<tr class="data-table__tr{selected_cls}">{cells}</tr>'

    # Pagination
    pagination_html = ""
    if paginate:
        prev_disabled = " disabled" if page <= 1 else ""
        next_disabled = " disabled" if page >= total_pages else ""
        pagination_html = (
            f'<div class="data-table__pagination">'
            f'<button class="data-table__page-btn" dj-click="{e_prev_event}"{prev_disabled}>'
            f"&laquo; Prev</button>"
            f'<span class="data-table__page-info">Page {page} of {total_pages}</span>'
            f'<button class="data-table__page-btn" dj-click="{e_next_event}"{next_disabled}>'
            f"Next &raquo;</button>"
            f"</div>"
        )

    return mark_safe(
        f'<div class="{wrapper_cls}">'
        f"{search_html}"
        f'<div class="data-table__wrapper">'
        f'<table class="data-table{striped_cls}{density_cls}">'
        f'<thead><tr class="data-table__header-row">{header_cells}</tr>'
        f"{filter_row}</thead>"
        f"<tbody>{body_rows}</tbody>"
        f"</table></div>"
        f"{pagination_html}"
        f"</div>"
    )
