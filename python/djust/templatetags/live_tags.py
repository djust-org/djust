"""
Django template tags for LiveView forms.

These tags provide a cleaner syntax for rendering LiveView forms with
automatic validation, error display, and framework-specific styling.

Usage:
    {% load live_tags %}

    <!-- Render entire form -->
    {% live_form view %}

    <!-- Render single field -->
    {% live_field view "field_name" %}

    <!-- Render a standalone state-bound input (no Form class needed) -->
    {% live_input "text" handler="set_subject" value=subject %}
"""

import logging
import re
from typing import Any, Dict, Optional

from django import template
from django.conf import settings
from django.template import Node, TemplateSyntaxError
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .._html import build_tag
from ..config import config

register = template.Library()
logger = logging.getLogger(__name__)

# Matches </script> with any letter casing. Used by the `{% colocated_hook %}`
# body-escape defense to prevent a template-author typo from prematurely
# closing the <script> block that carries the hook body.
_SCRIPT_CLOSE_RE = re.compile(r"</(script)>", re.IGNORECASE)


@register.simple_tag
def live_form(view, **kwargs):
    """
    Render an entire form automatically using the configured CSS framework.

    Args:
        view: The LiveView instance (must use FormMixin)
        **kwargs: Rendering options passed to as_live()
            - framework: Override the configured CSS framework
            - render_labels: Whether to render field labels (default: True)
            - render_help_text: Whether to render help text (default: True)
            - render_errors: Whether to render errors (default: True)
            - auto_validate: Whether to add validation on change (default: True)
            - wrapper_class: Custom wrapper class for each field

    Returns:
        HTML string for the entire form

    Example:
        {% load live_tags %}
        <form @submit="submit_form">
            {% live_form view %}
            <button type="submit">Submit</button>
        </form>
    """
    if not hasattr(view, "as_live"):
        return "<!-- ERROR: View does not have as_live() method. Did you use FormMixin? -->"

    return view.as_live(**kwargs)


@register.simple_tag
def live_field(view, field_name: str, **kwargs):
    """
    Render a single form field automatically using the configured CSS framework.

    Args:
        view: The LiveView instance (must use FormMixin)
        field_name: Name of the field to render
        **kwargs: Rendering options passed to as_live_field()
            - framework: Override the configured CSS framework
            - render_labels: Whether to render field labels (default: True)
            - render_help_text: Whether to render help text (default: True)
            - render_errors: Whether to render errors (default: True)
            - auto_validate: Whether to add validation on change (default: True)
            - wrapper_class: Custom wrapper class for the field
            - label: Custom label text

    Returns:
        HTML string for the field

    Example:
        {% load live_tags %}
        {% live_field view "email" %}
        {% live_field view "password" label="Custom Password Label" %}
    """
    if not hasattr(view, "as_live_field"):
        return "<!-- ERROR: View does not have as_live_field() method. Did you use FormMixin? -->"

    return view.as_live_field(field_name, **kwargs)


@register.simple_tag
def live_errors(view, field_name: str = None):
    """
    Render form errors for a specific field or all non-field errors.

    Args:
        view: The LiveView instance (must use FormMixin)
        field_name: Optional field name. If None, renders non-field errors.

    Returns:
        HTML string for the errors

    Example:
        {% load live_tags %}
        {% live_errors view "email" %}
        {% live_errors view %}  <!-- non-field errors -->
    """
    if field_name:
        if hasattr(view, "get_field_errors"):
            errors = view.get_field_errors(field_name)
            if errors:
                html = '<div class="invalid-feedback d-block">'
                for error in errors:
                    html += f"<div>{error}</div>"
                html += "</div>"
                return html
    else:
        if hasattr(view, "form_errors") and view.form_errors:
            html = '<div class="alert alert-danger">'
            for error in view.form_errors:
                html += f"<div>{error}</div>"
            html += "</div>"
            return html

    return ""


@register.filter
def field_value(view, field_name: str) -> Any:
    """
    Get the current value of a form field.

    Args:
        view: The LiveView instance (must use FormMixin)
        field_name: Name of the field

    Returns:
        Current field value

    Example:
        {% load live_tags %}
        <input type="text" value="{{ view|field_value:'email' }}">
    """
    if hasattr(view, "get_field_value"):
        return view.get_field_value(field_name)
    return ""


@register.filter
def has_errors(view, field_name: str) -> bool:
    """
    Check if a field has validation errors.

    Args:
        view: The LiveView instance (must use FormMixin)
        field_name: Name of the field

    Returns:
        True if field has errors, False otherwise

    Example:
        {% load live_tags %}
        <input class="{% if view|has_errors:'email' %}is-invalid{% endif %}">
    """
    if hasattr(view, "has_field_errors"):
        return view.has_field_errors(field_name)
    return False


# ---------------------------------------------------------------------------
# {% live_input %} — standalone state-bound form field (#650)
# ---------------------------------------------------------------------------


# Default dj-* event per field type. Callers can override via ``event=``.
# text-like fields default to per-keystroke dj-input; select/radio/checkbox
# default to dj-change; hidden has no interactive event.
_DEFAULT_EVENT_BY_TYPE: Dict[str, str] = {
    "text": "dj-input",
    "textarea": "dj-input",
    "password": "dj-input",
    "email": "dj-input",
    "url": "dj-input",
    "tel": "dj-input",
    "search": "dj-input",
    "number": "dj-input",
    "select": "dj-change",
    "checkbox": "dj-change",
    "radio": "dj-change",
    "hidden": None,  # no interactive event
}


def _resolve_css_class(explicit: Optional[str]) -> str:
    """Resolve the field CSS class via explicit kwarg or framework config."""
    if explicit:
        return explicit
    try:
        cls = config.get_framework_class("field_class")
        if cls:
            return cls
    except (ImportError, AttributeError) as exc:
        logger.debug("config.get_framework_class lookup failed: %s", exc)
    return "form-input"


def _collect_passthrough_attrs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Extract HTML attributes to forward from ``**kwargs``.

    Known configuration kwargs are stripped; everything else passes through
    to the rendered tag as an attribute, with ``_`` in keys converted to
    ``-`` (so ``aria_label="Search"`` becomes ``aria-label="Search"``).
    """
    _KNOWN = {
        "handler",
        "event",
        "value",
        "name",
        "css_class",
        "debounce",
        "throttle",
        "choices",
        "checked",
        "label",  # reserved for radio label text, not an HTML attribute
    }
    out: Dict[str, Any] = {}
    for k, v in kwargs.items():
        if k in _KNOWN:
            continue
        # Normalize underscores to dashes for HTML attributes
        attr = k.replace("_", "-")
        out[attr] = v
    return out


def _render_text_like(
    field_type: str,
    handler: str,
    value: Any,
    name: Optional[str],
    css_class: str,
    event: Optional[str],
    debounce: Optional[str],
    throttle: Optional[str],
    passthrough: Dict[str, Any],
) -> str:
    """Render ``<input type="...">`` for text/password/email/url/tel/number/search/hidden."""
    attrs: Dict[str, Any] = {
        "type": field_type,
        "class": css_class,
        "value": "" if value is None else str(value),
        "name": name or handler,
    }
    if event and handler:
        attrs[event] = handler
    if debounce:
        attrs["dj-debounce"] = debounce
    if throttle:
        attrs["dj-throttle"] = throttle
    attrs.update(passthrough)
    return build_tag("input", attrs)


def _render_textarea(
    handler: str,
    value: Any,
    name: Optional[str],
    css_class: str,
    event: Optional[str],
    debounce: Optional[str],
    throttle: Optional[str],
    passthrough: Dict[str, Any],
) -> str:
    """Render ``<textarea>...</textarea>``."""
    attrs: Dict[str, Any] = {
        "class": css_class,
        "name": name or handler,
    }
    if event and handler:
        attrs[event] = handler
    if debounce:
        attrs["dj-debounce"] = debounce
    if throttle:
        attrs["dj-throttle"] = throttle
    attrs.update(passthrough)
    return build_tag("textarea", attrs, "" if value is None else str(value))


def _render_select(
    handler: str,
    value: Any,
    name: Optional[str],
    css_class: str,
    event: Optional[str],
    choices: Any,
    passthrough: Dict[str, Any],
) -> str:
    """Render ``<select><option>...</option></select>``.

    ``choices`` may be:
      * a list of ``(value, label)`` tuples/lists
      * a list of strings (each used as both value and label)
      * an empty iterable → renders an empty select
    """
    from django.utils.html import escape

    if choices is None:
        choices = []

    options_html = ""
    for choice in choices:
        if isinstance(choice, (tuple, list)) and len(choice) == 2:
            cv, cl = choice
        else:
            cv = cl = choice
        selected = 'selected="selected"' if str(value) == str(cv) else ""
        options_html += f'<option value="{escape(str(cv))}" {selected}>{escape(str(cl))}</option>'

    attrs: Dict[str, Any] = {
        "class": css_class,
        "name": name or handler,
    }
    if event and handler:
        attrs[event] = handler
    attrs.update(passthrough)
    return build_tag("select", attrs, options_html, content_is_safe=True)


def _render_checkbox(
    handler: str,
    value: Any,
    name: Optional[str],
    checked: bool,
    css_class: str,
    event: Optional[str],
    passthrough: Dict[str, Any],
) -> str:
    """Render a single checkbox ``<input type="checkbox">``."""
    attrs: Dict[str, Any] = {
        "type": "checkbox",
        "class": css_class,
        "name": name or handler,
        "value": "" if value is None else str(value),
        "checked": bool(checked),
    }
    if event and handler:
        attrs[event] = handler
    attrs.update(passthrough)
    return build_tag("input", attrs)


def _render_radio(
    handler: str,
    value: Any,
    name: Optional[str],
    css_class: str,
    event: Optional[str],
    choices: Any,
    passthrough: Dict[str, Any],
) -> str:
    """Render a set of ``<label><input type="radio">...</label>`` rows.

    ``choices`` may be:
      * a list of ``(value, label)`` tuples/lists
      * a list of strings (value == label)
    """
    from django.utils.html import escape

    if choices is None:
        choices = []

    html_parts = []
    for choice in choices:
        if isinstance(choice, (tuple, list)) and len(choice) == 2:
            cv, cl = choice
        else:
            cv = cl = choice
        attrs: Dict[str, Any] = {
            "type": "radio",
            "class": css_class,
            "name": name or handler,
            "value": str(cv),
            "checked": str(value) == str(cv),
        }
        if event and handler:
            attrs[event] = handler
        attrs.update(passthrough)
        input_html = build_tag("input", attrs)
        html_parts.append(f"<label>{input_html}{escape(str(cl))}</label>")

    return "".join(html_parts)


# Field-type registry — each entry is a callable accepting the normalized
# kwargs and returning an HTML string. Adding a new type is a one-line
# registration here plus a supporting renderer above.
_FIELD_RENDERERS = {
    "text": lambda **kw: _render_text_like("text", **kw),
    "password": lambda **kw: _render_text_like("password", **kw),
    "email": lambda **kw: _render_text_like("email", **kw),
    "url": lambda **kw: _render_text_like("url", **kw),
    "tel": lambda **kw: _render_text_like("tel", **kw),
    "search": lambda **kw: _render_text_like("search", **kw),
    "number": lambda **kw: _render_text_like("number", **kw),
    "hidden": lambda **kw: _render_text_like("hidden", **kw),
}


@register.simple_tag
def live_input(field_type: str = "text", **kwargs) -> Any:
    """Render a standalone state-bound form field.

    This is the lightweight alternative to ``{% live_field %}`` for state
    that lives directly on view attributes (modals, inline panels, search
    boxes, settings forms — any UI that doesn't justify a full
    ``forms.Form``). It provides the same conveniences ``FormMixin``'s
    ``as_live_field()`` offers — framework CSS class, correct
    ``dj-input``/``dj-change`` binding, optional debounce/throttle — without
    requiring a Form class or ``WizardMixin``.

    Args:
        field_type: One of ``text``, ``textarea``, ``select``, ``password``,
            ``email``, ``number``, ``url``, ``tel``, ``search``, ``hidden``,
            ``checkbox``, or ``radio``. Default ``text``.
        handler: The event handler name to bind (e.g. ``"set_subject"``).
            Required for every type except ``hidden``.
        value: Current value of the field (typically a view attribute
            like ``subject``).
        name: HTML ``name`` attribute. Defaults to the handler name.
        event: Override the default event binding. One of ``dj-input``,
            ``dj-change``, ``dj-blur``. Defaults sensibly per type.
        css_class: Override the framework CSS class (e.g. ``form-control``
            for Bootstrap, ``input input-bordered`` for daisyUI). Defaults
            to ``config.get_framework_class('field_class')``.
        debounce: Forward as ``dj-debounce="..."``.
        throttle: Forward as ``dj-throttle="..."``.
        choices: For ``select`` and ``radio`` — list of ``(value, label)``
            tuples or plain strings.
        checked: For ``checkbox`` — boolean.
        **kwargs: Any other kwargs are forwarded as HTML attributes. Keys
            with ``_`` are normalised to ``-`` (e.g. ``aria_label="x"`` →
            ``aria-label="x"``).

    Returns:
        Rendered HTML as a ``SafeString``.

    Examples::

        {% load live_tags %}

        <!-- Search input with debounce -->
        {% live_input "text" handler="search" value=query placeholder="Search..." debounce="300" %}

        <!-- Note textarea -->
        {% live_input "textarea" handler="set_body" value=body placeholder="Your note..." rows=5 %}

        <!-- Status select -->
        {% live_input "select" handler="set_status" value=status choices=status_choices %}

        <!-- Toggle -->
        {% live_input "checkbox" handler="toggle_notifications" checked=notifications_enabled %}

    See issue #650 for the full design notes.
    """
    handler: str = kwargs.pop("handler", "")
    value = kwargs.pop("value", None)
    name: Optional[str] = kwargs.pop("name", None)
    explicit_css: Optional[str] = kwargs.pop("css_class", None)
    explicit_event: Optional[str] = kwargs.pop("event", None)
    debounce: Optional[str] = kwargs.pop("debounce", None)
    throttle: Optional[str] = kwargs.pop("throttle", None)
    choices = kwargs.pop("choices", None)
    checked: bool = bool(kwargs.pop("checked", False))

    if field_type not in _FIELD_RENDERERS and field_type not in (
        "textarea",
        "select",
        "checkbox",
        "radio",
    ):
        return mark_safe(
            f"<!-- ERROR: {{% live_input %}} unknown field_type "
            f"{field_type!r} — supported: text, textarea, select, password, "
            f"email, number, url, tel, search, hidden, checkbox, radio -->"
        )

    if not handler and field_type != "hidden":
        return mark_safe(
            "<!-- ERROR: {% live_input %} requires handler= kwarg (the dj-* event handler name) -->"
        )

    css_class = _resolve_css_class(explicit_css)
    event = (
        explicit_event
        if explicit_event is not None
        else _DEFAULT_EVENT_BY_TYPE.get(field_type, "dj-input")
    )
    # Normalize 'input'/'change'/'blur' → 'dj-input'/'dj-change'/'dj-blur'
    if event and not event.startswith("dj-"):
        event = f"dj-{event}"
    passthrough = _collect_passthrough_attrs(kwargs)

    if field_type == "textarea":
        html = _render_textarea(
            handler=handler,
            value=value,
            name=name,
            css_class=css_class,
            event=event,
            debounce=debounce,
            throttle=throttle,
            passthrough=passthrough,
        )
    elif field_type == "select":
        html = _render_select(
            handler=handler,
            value=value,
            name=name,
            css_class=css_class,
            event=event,
            choices=choices,
            passthrough=passthrough,
        )
    elif field_type == "checkbox":
        html = _render_checkbox(
            handler=handler,
            value=value,
            name=name,
            checked=checked,
            css_class=css_class,
            event=event,
            passthrough=passthrough,
        )
    elif field_type == "radio":
        html = _render_radio(
            handler=handler,
            value=value,
            name=name,
            css_class=css_class,
            event=event,
            choices=choices,
            passthrough=passthrough,
        )
    else:
        # text-like types dispatched through the lambda registry
        html = _FIELD_RENDERERS[field_type](
            handler=handler,
            value=value,
            name=name,
            css_class=css_class,
            event=event,
            debounce=debounce,
            throttle=throttle,
            passthrough=passthrough,
        )

    return mark_safe(html)


# ---------------------------------------------------------------------------
# {% colocated_hook %} — Phoenix 1.1 parity
# ---------------------------------------------------------------------------


class ColocatedHookNode(Node):
    """
    Emit a ``<script type="djust/hook" data-hook="NAME">...</script>`` tag
    carrying the body JS. The client runtime
    (``python/djust/static/djust/src/32-colocated-hooks.js``) walks the DOM
    on init and after each VDOM morph, extracts these scripts, and registers
    each body as ``window.djust.hooks[NAME]``.

    With ``DJUST_CONFIG = {"hook_namespacing": "strict"}`` in settings, the
    emitted ``data-hook`` attribute is prefixed with
    ``<view_module>.<view_qualname>.`` so two views can each define ``Chart``
    without colliding.  Per-tag opt-out: ``{% colocated_hook "X" global %}``
    always emits the bare name.

    SECURITY: the body is template-author JS, not user input.  We escape
    ``</script>`` to prevent premature tag close.  mark_safe is used on the
    final string because every interpolation is either a template-author hook
    name or the body (which has been ``</script>``-escaped) — no
    request/POST data is interpolated.  The client uses ``new Function(...)``
    to evaluate the body; strict-CSP apps without ``'unsafe-eval'`` should
    avoid this tag and register hooks via a nonce-bearing script instead.
    """

    def __init__(self, name, nodelist, force_global=False):
        self.name = name
        self.nodelist = nodelist
        self.force_global = force_global

    def _namespace(self, context):
        if self.force_global:
            return self.name
        cfg = getattr(settings, "DJUST_CONFIG", {}) or {}
        if cfg.get("hook_namespacing") != "strict":
            return self.name
        view = context.get("view")
        if view is None:
            return self.name
        try:
            prefix = f"{type(view).__module__}.{type(view).__qualname__}"
        except AttributeError:
            return self.name
        return f"{prefix}.{self.name}"

    def render(self, context):
        body = self.nodelist.render(context)
        namespaced = self._namespace(context)

        # Defense: escape </script> in the body to prevent premature tag close.
        # HTML tokenizers treat tag names case-insensitively, so a mixed-case
        # </Script> or </sCrIpT> would still terminate the <script> block.
        # Use a case-insensitive regex that preserves the original casing of
        # the matched text inside the escaped form so the body remains
        # readable to a human auditor.
        safe_body = _SCRIPT_CLOSE_RE.sub(r"<\\/\1>", body)
        # Escape the namespaced name for the data-hook attribute as
        # defense-in-depth: names are developer-controlled (not user input),
        # but a stray quote or HTML-special char would break the attribute.
        # The banner uses the raw name — it's inside a JS comment, not markup.
        safe_hook_name = escape(namespaced)
        banner = "/* COLOCATED HOOK: " + namespaced + " */"
        # Build the tag via concatenation (no f-string interpolation with
        # mark_safe per CLAUDE.md rules). `safe_hook_name` is HTML-escaped
        # above; `safe_body` has been </script>-escaped above.
        html = (
            '<script type="djust/hook" data-hook="'
            + safe_hook_name
            + '">'
            + banner
            + "\n"
            + safe_body
            + "</script>"
        )
        return mark_safe(html)


@register.tag("colocated_hook")
def do_colocated_hook(parser, token):
    """
    ``{% colocated_hook "HookName" [global] %}js body{% endcolocated_hook %}``

    Emits a colocated JS hook definition alongside the template that uses it.
    The optional ``global`` keyword opts out of namespacing when
    ``DJUST_CONFIG["hook_namespacing"] = "strict"`` is set.
    """
    bits = token.split_contents()
    if len(bits) < 2:
        raise TemplateSyntaxError("{% colocated_hook %} requires a hook name argument")
    name = bits[1].strip("\"'")
    if not name:
        raise TemplateSyntaxError("{% colocated_hook %} name must be non-empty")
    force_global = len(bits) >= 3 and bits[2] == "global"
    nodelist = parser.parse(("endcolocated_hook",))
    parser.delete_first_token()
    return ColocatedHookNode(name, nodelist, force_global)


# Shimmer CSS emitted once per render. Small enough to inline; deduped via
# ``context.render_context`` so a page that calls ``{% djust_skeleton %}`` in
# a {% for %} loop still only writes one <style> block to the DOM.
_SKELETON_STYLE_KEY = "_djust_skeleton_style_emitted"
_SKELETON_STYLE_BLOCK = (
    "<style>"
    "@keyframes djust-skeleton-shimmer{"
    "from{background-position:200% 0}"
    "to{background-position:-200% 0}"
    "}"
    ".djust-skeleton{"
    "display:inline-block;"
    "background:linear-gradient(90deg,#e5e7eb 25%,#f3f4f6 50%,#e5e7eb 75%);"
    "background-size:200% 100%;"
    "animation:djust-skeleton-shimmer 1.5s ease-in-out infinite;"
    "border-radius:4px;"
    "vertical-align:middle;"
    "}"
    ".djust-skeleton-line{display:block;margin-bottom:0.5em}"
    ".djust-skeleton-circle{border-radius:50%}"
    "@media (prefers-reduced-motion: reduce){"
    ".djust-skeleton{animation:none}"
    "}"
    "</style>"
)

# Deterministic small width variation for stacked line skeletons: it looks
# more like real copy than a column of identical 100 %-wide bars while
# staying fully deterministic (no randomness, no per-render drift).
_SKELETON_LINE_WIDTHS = ("100%", "92%", "85%")

_SKELETON_VALID_SHAPES = frozenset({"line", "circle", "rect"})

# Per-shape default (width, height) if the caller omits them.
_SKELETON_SHAPE_DEFAULTS = {
    "line": ("100%", "1em"),
    "circle": ("40px", "40px"),
    "rect": ("100%", "120px"),
}

# CSS length whitelist for skeleton width/height. ``build_tag`` already
# HTML-escapes attribute values, so a value like ``100%;background:red``
# cannot break out of the ``style="..."`` attribute — but it DOES compose
# freely into the inline CSS, letting a caller stuff arbitrary declarations
# into the emitted ``style=`` string. The whitelist rejects anything that
# isn't a single CSS length literal (digits, optional decimal, optional
# unit suffix), falling back to the shape default.
_SKELETON_SIZE_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?(?:px|em|rem|%|vh|vw|ch)?$")


def _validate_skeleton_size(value: Any, default: str) -> str:
    """Return ``value`` if it matches the CSS length whitelist, else ``default``."""
    if value is None:
        return default
    if not isinstance(value, str):
        value = str(value)
    return value if _SKELETON_SIZE_RE.match(value) else default


@register.simple_tag(takes_context=True)
def djust_skeleton(context, shape="line", width=None, height=None, count=1, class_=None):
    """Emit a shimmering skeleton placeholder block (v0.6.0).

    Phoenix / Vercel / Shadcn-ui parity for loading placeholders. Renders
    a ``<div>`` (or several, when ``shape="line"`` and ``count > 1``) with
    a shimmering background gradient keyed off a single inline
    ``@keyframes`` block that is deduped via Django's ``render_context``.

    All attribute values pass through :func:`._html.build_tag`, which
    HTML-escapes every value, so caller-controlled ``width`` / ``height`` /
    ``class_`` cannot inject script content.

    Args:
        shape: One of ``"line"``, ``"circle"``, ``"rect"``. Anything else
            falls back to ``"line"``.
        width: CSS width. Defaults by shape: ``100%`` (line/rect),
            ``40px`` (circle).
        height: CSS height. Defaults by shape: ``1em`` (line), ``40px``
            (circle), ``120px`` (rect).
        count: Number of line blocks to emit. Ignored for ``circle`` and
            ``rect`` (they always render exactly one block). Clamped to
            ``[1, 100]`` — an unbounded ``count`` from an untrusted
            template context could inflate a page to megabytes.
        class_: Optional extra CSS class to append after the default
            ``djust-skeleton djust-skeleton-<shape>`` classes.

    Returns:
        Marked-safe HTML string containing the skeleton block(s) plus
        (on the first call within a single render) the shimmer
        ``<style>`` block.

    Examples:
        ``{% djust_skeleton %}`` — single 100 %-wide text line.
        ``{% djust_skeleton shape="circle" width="48px" height="48px" %}``
        ``{% djust_skeleton count=4 %}`` — four stacked lines with
        subtly varying widths.
    """
    # Whitelist validation — prevents arbitrary class-name injection via
    # the shape argument even though build_tag escapes attribute values.
    if shape not in _SKELETON_VALID_SHAPES:
        shape = "line"

    # Coerce + clamp count. A non-integer count from a template var should
    # degrade gracefully to 1 rather than raising TypeError.
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        count_int = 1
    count_int = max(1, min(100, count_int))

    # Resolve per-shape default dimensions. ``width`` and ``height`` are
    # run through a CSS-length whitelist so a caller can't smuggle extra
    # declarations (e.g. ``100%;background:red``) into the inline
    # ``style=`` string. Unsafe / non-matching values silently fall back
    # to the shape default.
    default_w, default_h = _SKELETON_SHAPE_DEFAULTS[shape]
    resolved_w = _validate_skeleton_size(width, default_w)
    resolved_h = _validate_skeleton_size(height, default_h)

    # Class string: default classes + any user-supplied class.
    base_class = f"djust-skeleton djust-skeleton-{shape}"
    css_class = f"{base_class} {class_}" if class_ else base_class

    # Dedupe the shimmer <style> block across repeated invocations in the
    # same render. Django's render_context is a ChainMap-based per-render
    # scratch space and is the idiomatic home for this kind of dedupe.
    style_prefix = ""
    if not context.render_context.get(_SKELETON_STYLE_KEY):
        context.render_context[_SKELETON_STYLE_KEY] = True
        style_prefix = _SKELETON_STYLE_BLOCK

    blocks = []
    # ``count`` is line-only: circle / rect always render one block.
    effective_count = count_int if shape == "line" else 1
    for i in range(effective_count):
        # For stacked lines, rotate through a small width palette so the
        # block looks more like real copy. Deterministic (no RNG).
        if shape == "line" and effective_count > 1 and width is None:
            w = _SKELETON_LINE_WIDTHS[i % len(_SKELETON_LINE_WIDTHS)]
        else:
            w = resolved_w
        style = f"width:{w};height:{resolved_h}"
        if shape == "circle":
            style += ";border-radius:50%"
        blocks.append(
            build_tag(
                "div",
                {
                    "class": css_class,
                    "style": style,
                    "aria-hidden": "true",
                },
                content="",
                content_is_safe=True,
            )
        )
    return mark_safe(style_prefix + "".join(blocks))


@register.simple_tag
def djust_track_static():
    """Emit the ``dj-track-static`` attribute marker (v0.6.0).

    Convenience tag so template authors don't have to remember the exact
    attribute spelling. Intended for ``<script>`` / ``<link>`` tags that
    should be monitored for asset-hash changes across WebSocket
    reconnects — see ``dj-track-static`` in
    ``static/djust/src/39-dj-track-static.js``.

    Usage::

        {% load live_tags %}
        <script {% djust_track_static %} src="{% static 'js/app.abc.js' %}"></script>
        <link {% djust_track_static %} rel="stylesheet" href="...">

    To force an automatic ``window.location.reload()`` when the asset
    changes (instead of the default ``dj:stale-assets`` CustomEvent),
    write the attribute literally: ``dj-track-static="reload"``.
    """
    return mark_safe("dj-track-static")
