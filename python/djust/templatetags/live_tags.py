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
from django.template import Context, Node, Template, TemplateSyntaxError
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


# ---------------------------------------------------------------------------
# {% dj_activity %} — React 19.2 <Activity> parity (v0.7.0)
# ---------------------------------------------------------------------------
#
# Pre-renders a hidden region of a LiveView and preserves its local DOM state
# across show/hide cycles. Distinct from sticky (survives ``live_redirect``),
# ``{% live_render %}`` (full child view), and ``dj-prefetch`` (fetches future
# page). The tag renders a wrapper ``<div>`` with ``data-djust-activity`` and,
# when ``visible=False``, the HTML ``hidden`` attribute + ``aria-hidden``.
#
# The companion ``ActivityMixin`` (``python/djust/mixins/activity.py``) stores
# the server-side visibility state. Client-side event-dispatch gating lives in
# ``static/djust/src/11-event-handler.js`` and VDOM subtree skipping in
# ``static/djust/src/12-vdom-patch.js``. See ``docs/website/guides/activity.md``.


class DjActivityNode(Node):
    """Render a ``{% dj_activity %}`` block with show/hide + eager semantics.

    Emits a wrapper ``<div>`` carrying the activity ``name``, a stable
    ``data-djust-activity=<name>`` attribute, the ``hidden``/``aria-hidden``
    pair when declared not visible, and an optional ``data-djust-eager="true"``
    opt-in for activities that continue to run (dispatch events, receive
    patches) while hidden.

    The inner body is rendered unconditionally — children exist in the DOM
    in every branch so local state (form values, scroll, transient JS) is
    preserved across show/hide cycles. Hiding is done via the HTML ``hidden``
    attribute, which the browser treats as ``display: none`` without
    removing the subtree.
    """

    def __init__(self, name_expr, visible_expr, eager_expr, nodelist):
        self.name_expr = name_expr
        self.visible_expr = visible_expr
        self.eager_expr = eager_expr
        self.nodelist = nodelist

    def _resolve_bool(self, expr, context, default):
        if expr is None:
            return default
        try:
            value = expr.resolve(context)
        except Exception:  # noqa: BLE001 — defensive: unresolvable var → default
            return default
        if value is None:
            return default
        return bool(value)

    def _resolve_name(self, context):
        try:
            raw = self.name_expr.resolve(context)
        except Exception:  # noqa: BLE001
            raw = None
        if raw is None:
            return ""
        name = str(raw).strip()
        return name

    def render(self, context):
        body = self.nodelist.render(context)
        name = self._resolve_name(context)
        visible = self._resolve_bool(self.visible_expr, context, True)
        eager = self._resolve_bool(self.eager_expr, context, False)

        # Register declared state on the current view so the server-side
        # mixin can authoritatively gate events. ``ActivityMixin`` defines
        # ``_register_activity``; guarded for non-LiveView contexts (e.g.
        # unit tests that render the tag against a plain Context).
        view = context.get("view")
        if view is not None and hasattr(view, "_register_activity"):
            try:
                view._register_activity(name, visible=visible, eager=eager)
            except Exception:  # noqa: BLE001 — never break template rendering
                logger.exception("dj_activity: _register_activity failed for %s", name)

        # Attribute set. ``build_tag`` escapes every value; ``name`` is
        # developer-controlled but defense-in-depth matters for custom tags
        # that might pass an unsanitized variable.
        attrs: Dict[str, Any] = {
            "data-djust-activity": name,
            "data-djust-visible": "true" if visible else "false",
        }
        if eager:
            attrs["data-djust-eager"] = "true"
        if not visible:
            # HTML boolean attribute. ``build_tag`` emits True-valued
            # attributes as ``name="name"`` (canonical HTML serialization).
            # The client-side observer checks for the presence of ``hidden``
            # via ``hasAttribute``, which works with either form.
            attrs["hidden"] = True
            attrs["aria-hidden"] = "true"

        return build_tag("div", attrs, content=body, content_is_safe=True)


@register.tag("dj_activity")
def do_dj_activity(parser, token):
    """Parse ``{% dj_activity "name" visible=expr eager=expr %} ... {% enddj_activity %}``.

    The ``name`` argument is required and must be non-empty; duplicates in
    the same template are flagged by the ``A071`` system check. ``visible``
    defaults to ``True``, ``eager`` defaults to ``False``.
    """
    bits = token.split_contents()
    if len(bits) < 2:
        raise TemplateSyntaxError(
            '{% dj_activity %} requires a name argument: {% dj_activity "my-panel" visible=expr %}'
        )
    # First positional arg is the activity name (a template expression so
    # string literals + variable names both work).
    name_expr = parser.compile_filter(bits[1])

    # Remaining args may be kwargs (visible=expr, eager=expr). We purposefully
    # reject bare positional extras to keep the call site unambiguous.
    visible_expr = None
    eager_expr = None
    for bit in bits[2:]:
        if "=" not in bit:
            raise TemplateSyntaxError(
                "{%% dj_activity %%} unexpected positional argument %r; "
                "use kwargs: visible=... eager=..." % bit
            )
        key, _, raw = bit.partition("=")
        key = key.strip()
        if key == "visible":
            visible_expr = parser.compile_filter(raw)
        elif key == "eager":
            eager_expr = parser.compile_filter(raw)
        else:
            raise TemplateSyntaxError(
                "{%% dj_activity %%} unknown kwarg %r; expected 'visible' or 'eager'." % key
            )

    nodelist = parser.parse(("enddj_activity",))
    parser.delete_first_token()
    return DjActivityNode(name_expr, visible_expr, eager_expr, nodelist)


# ---------------------------------------------------------------------------
# {% live_render %} — embed a LiveView as a child (Phase A of Sticky LiveViews)
# ---------------------------------------------------------------------------

# Event attributes carrying dj-* directives. When ``{% live_render %}``
# stamps ``view_id`` on embedded elements, it scans for these — a no-op
# on static markup, but every event-bearing element gets a scoped id so
# the consumer's event-dispatch path (``websocket.py``) routes per-view.
_LIVE_RENDER_EVENT_ATTRS = (
    "dj-click",
    "dj-submit",
    "dj-input",
    "dj-change",
    "dj-keydown",
    "dj-keyup",
    "dj-keypress",
    "dj-focus",
    "dj-blur",
    "dj-hook",
    "dj-mounted",
    "dj-viewport-enter",
    "dj-viewport-leave",
    "dj-mouseenter",
    "dj-mouseleave",
)

# Pre-compiled regex: matches the opening of an element tag that carries
# one of the event attributes listed above. Captures:
#   (1) the "<TagName" prefix (e.g. "<button")
#   (2) the attributes string up to (and including) the event attr name
#       — uses per-attribute alternation so that ``>`` inside a quoted
#       attribute value is NOT treated as the end of the tag (fix #4).
#   (3) the trailing ``=`` of the event attr.
_LIVE_RENDER_ELEMENT_WITH_EVENT_RE = re.compile(
    r"(<[a-zA-Z][\w:-]*)"  # (1) <TagName
    # (2) zero-or-more attributes (quoted, apostrophed, or bare) followed
    # by a dj-* event attribute whose ``=`` we also want to consume.
    r"("
    r"(?:\s+[^\s\"'<>/=]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s<>]+))?)*?"
    r"\s+(?:" + "|".join(re.escape(a) for a in _LIVE_RENDER_EVENT_ATTRS) + r")"
    r")"
    r"(\s*=)",  # (3) trailing '='
    re.IGNORECASE | re.DOTALL,
)

# Mask <script>...</script> blocks and <!-- ... --> comments before
# stamping so we don't accidentally inject ``data-djust-embedded`` inside
# a script body or an HTML comment. The placeholder uses NUL sentinels
# that cannot appear in valid HTML.
#
# The closing-tag pattern ``</script[^>]*>`` accepts any tokens between
# ``</script`` and ``>`` per HTML5 tokenizer tolerance — e.g.
# ``</script >``, ``</script\t\n foo>`` are all valid script-close
# forms that browsers honor. Using ``</script\s*>`` was insufficient
# and failed CodeQL py/bad-html-filtering-regexp; ``[^>]*`` matches
# the full HTML5 close-tag grammar.
_SCRIPT_OR_COMMENT_RE = re.compile(
    r"<script\b[^>]*>.*?</script[^>]*>|<!--.*?-->",
    re.DOTALL | re.IGNORECASE,
)

# Sentinel format for masked script/comment spans. NUL bytes are invalid
# in HTML so we can round-trip without ambiguity.
_MASK_PLACEHOLDER_RE = re.compile(r"\x00DJUST_MASK_(\d+)\x00")


def _stamp_view_id(html: str, view_id: str) -> str:
    """Inject ``data-djust-embedded="..."`` inside every event-attribute-bearing tag.

    Scans ``html`` for elements carrying dj-* event attributes and adds a
    ``data-djust-embedded`` attribute INSIDE the opening tag (right after
    the tag name) so the client's ``getEmbeddedViewId`` DOM walker can
    pick it up via ``dataset.djustEmbedded`` and surface it in outbound
    event params as ``view_id``. Idempotent — elements that already carry
    ``data-djust-embedded=`` in their opening-tag span are skipped so
    successive invocations don't stack duplicate attrs. Safe against
    ``>`` inside quoted attribute values, and skips ``<script>`` bodies
    and HTML comments entirely.
    """
    if not html:
        return html

    escaped_id = escape(view_id)
    marker = f' data-djust-embedded="{escaped_id}"'

    # 1. Mask out <script>...</script> and <!-- ... --> so the regex can't
    #    inject attrs inside them. See tests ``test_script_blocks_are_not_stamped``.
    placeholders: list[str] = []

    def _mask(match: "re.Match[str]") -> str:
        placeholders.append(match.group(0))
        return f"\x00DJUST_MASK_{len(placeholders) - 1}\x00"

    masked = _SCRIPT_OR_COMMENT_RE.sub(_mask, html)

    # 2. Inject the marker inside the opening tag.
    def _inject(match: "re.Match[str]") -> str:
        tag_prefix = match.group(1)  # e.g. "<button"
        attrs_body = match.group(2)  # e.g. ' dj-click'
        trailing_eq = match.group(3)  # e.g. '='
        # Idempotence: if the tag's opening segment already has the marker
        # for *any* view_id, skip. Distinct nested-view_ids inside the same
        # tag would indicate a programming error — we honor the innermost
        # stamp (applied first by the recursion order).
        if "data-djust-embedded=" in match.group(0):
            return match.group(0)
        return tag_prefix + marker + attrs_body + trailing_eq

    stamped = _LIVE_RENDER_ELEMENT_WITH_EVENT_RE.sub(_inject, masked)

    # 3. Restore masked spans.
    def _unmask(match: "re.Match[str]") -> str:
        idx = int(match.group(1))
        return placeholders[idx]

    return _MASK_PLACEHOLDER_RE.sub(_unmask, stamped)


# Context-render-local scratch key for tracking sticky_ids already
# registered in the current parent render pass. Used to raise
# TemplateSyntaxError on ``{% live_render 'X' sticky=True %}`` collisions.
_STICKY_IDS_SEEN_KEY = "_djust_sticky_ids_seen"


@register.simple_tag(takes_context=True)
def live_render(context, view_path: str, **kwargs) -> Any:
    """Embed a LiveView as a child of the current view (Phoenix nested-LV parity).

    Resolves the dotted path to a :class:`~djust.live_view.LiveView`
    subclass, instantiates it with the parent's request, runs its
    ``mount(request, **kwargs) -> get_context_data -> render``, stamps
    the child's assigned ``view_id`` on every event-bearing element in
    the rendered HTML (as ``data-djust-embedded``), and registers the
    child on the parent so inbound events can route to it by ``view_id``.

    Usage::

        {% load live_tags %}
        <div dj-root>
          <h1>Page</h1>
          {% live_render "myapp.views.AudioPlayerView" session=request.session.session_key %}
        </div>

    Phase A ships this non-sticky embedding primitive only. Phase B
    (this PR) adds ``sticky=True`` preservation across
    ``live_redirect``:

        {% live_render "myapp.views.AudioPlayer" sticky=True %}

    The child class must declare ``sticky = True`` and a non-empty
    ``sticky_id``. In the destination page's template, mark the
    re-attachment point with::

        <div dj-sticky-slot="audio-player"></div>

    The client detaches the sticky subtree BEFORE sending the
    live_redirect, then ``replaceWith`` it onto the matching slot in
    the new layout — DOM identity, form values, scroll, and focus all
    preserved.

    Security notes:

    * The child receives the parent's ``request`` object **by reference**.
      Children MUST NOT mutate ``request`` — treat it as read-only. Mutating
      attributes (e.g. stashing state on ``request.user`` or ``request.session``)
      will leak across the parent + every other embedded sibling. This is a
      convention, not an enforced copy; a deep copy of every request on every
      embed would be prohibitively expensive.
    * The child's auth is re-checked against the parent's request via
      :func:`djust.auth.core.check_view_auth`. An unauthenticated (or under-
      permissioned) request causes the tag to raise
      :class:`~django.core.exceptions.PermissionDenied` (Django middleware
      turns this into a 403) or :class:`~django.template.TemplateSyntaxError`
      with the login-redirect URL in the message. This mirrors the guarantee
      the consumer provides at mount time for top-level views — a child
      cannot silently bypass the parent's auth posture.
    * The dotted path can be constrained with the
      ``DJUST_LIVE_RENDER_ALLOWED_MODULES`` setting (list of dotted-path
      prefixes). If unset, all paths are permitted (backward-compatible).

    Args:
        view_path: Dotted import path to a LiveView subclass. Must be
            allowed by ``DJUST_LIVE_RENDER_ALLOWED_MODULES`` when set.
        **kwargs: Forwarded to the child's ``mount(request, **kwargs)``
            and merged into its ``get_context_data`` pass. ``view_id``
            (optional) pins a stable id for the child — otherwise an
            auto-generated ``child_N`` stamp is used.

    Raises:
        TemplateSyntaxError: If ``view_path`` cannot be resolved, is
            not on the allowlist, the target is not a LiveView subclass,
            the tag is used outside a LiveView render context, or the
            child's auth check returned a redirect URL.
        PermissionDenied: If the child's auth check raised PermissionDenied
            (authenticated user without required permissions).
    """
    from django.template.loader import get_template
    from django.utils.module_loading import import_string

    from ..auth.core import check_view_auth
    from ..live_view import LiveView  # Lazy import to avoid cycle

    # 1a. Optional allowlist check — opt-in hardening via
    #     ``DJUST_LIVE_RENDER_ALLOWED_MODULES`` setting. When unset, any
    #     dotted path resolvable by ``import_string`` is permitted; this
    #     preserves backward compatibility. When set, the resolved view
    #     path must start with one of the allowed prefixes. The check is
    #     prefix-based (like ``INSTALLED_APPS``) so e.g. ``"myapp.views"``
    #     matches ``"myapp.views.X"`` and ``"myapp.views.sub.Y"``.
    allowed_prefixes = getattr(settings, "DJUST_LIVE_RENDER_ALLOWED_MODULES", None)
    if allowed_prefixes is not None:
        if not any(
            view_path == prefix or view_path.startswith(prefix + ".") for prefix in allowed_prefixes
        ):
            raise TemplateSyntaxError(
                "{%% live_render %%} view_path %r is not in "
                "DJUST_LIVE_RENDER_ALLOWED_MODULES" % view_path
            )

    # 1. Resolve the dotted path.
    try:
        child_cls = import_string(view_path)
    except (ImportError, AttributeError, ModuleNotFoundError) as exc:
        raise TemplateSyntaxError(
            "{%% live_render %%} cannot resolve %r: %s" % (view_path, exc)
        ) from exc

    # 2. Validate it's a LiveView subclass.
    if not (isinstance(child_cls, type) and issubclass(child_cls, LiveView)):
        raise TemplateSyntaxError(
            "{%% live_render %%} target %r must be a LiveView subclass; got %r"
            % (view_path, child_cls)
        )

    # 3. Locate the parent view in the render context.
    parent = context.get("view") or context.get("self")
    if parent is None or not isinstance(parent, LiveView):
        raise TemplateSyntaxError(
            "{% live_render %} must be called inside a LiveView template; "
            "no parent view in the current render context"
        )

    # 4. Instantiate + mount the child.
    request = context.get("request")
    preferred_view_id = kwargs.pop("view_id", None)
    # Phase B: sticky kwarg — if the caller asks for sticky preservation,
    # the child class must opt in (``sticky = True`` + non-empty
    # ``sticky_id``). Reject mismatched pairs at render time with
    # TemplateSyntaxError so template authors don't discover the
    # mis-configuration only when a live_redirect fails silently.
    sticky_kwarg = bool(kwargs.pop("sticky", False))
    sticky_id_value = None
    if sticky_kwarg:
        if getattr(child_cls, "sticky", False) is not True:
            raise TemplateSyntaxError(
                "{%% live_render %%} sticky=True requires %r to set "
                "``sticky = True`` as a class attribute; the child is NOT "
                "sticky-enabled." % view_path
            )
        sticky_id_value = getattr(child_cls, "sticky_id", None)
        if not sticky_id_value:
            raise TemplateSyntaxError(
                "{%% live_render %%} sticky=True requires %r to set a "
                "non-empty ``sticky_id`` class attribute; no slot key." % view_path
            )
        # Enforce sticky_id uniqueness across the current render pass.
        seen = context.render_context.setdefault(_STICKY_IDS_SEEN_KEY, set())
        if sticky_id_value in seen:
            raise TemplateSyntaxError(
                "{%% live_render %%} sticky_id %r is used by more than "
                "one embed in this page; sticky_ids must be unique per "
                "parent." % sticky_id_value
            )
        seen.add(sticky_id_value)
        # Pin the sticky_id as the view_id for register/dispatch.
        preferred_view_id = sticky_id_value
    child = child_cls()
    child.request = request

    # 4a. Enforce child's auth posture against the parent's request BEFORE
    #     running mount(). Consumers apply the same check at top-level
    #     mount; children should not silently bypass it. check_view_auth
    #     returns None on success, a login-redirect URL on unauth, and
    #     raises PermissionDenied for authenticated users without perms.
    auth_redirect = check_view_auth(child, request)
    if auth_redirect is not None:
        raise TemplateSyntaxError(
            "{%% live_render %%} target %r denied access: the child view "
            "requires auth/permissions that the parent's request does not "
            "satisfy (login redirect: %s)" % (view_path, auth_redirect)
        )

    mount = getattr(child, "mount", None)
    if callable(mount):
        mount(request, **kwargs)

    # 5. Assign the view_id and register on the parent. _register_child
    #    wires parent/view_id back-references on the child.
    view_id = parent._assign_view_id(preferred_view_id)
    parent._register_child(view_id, child)

    # 6. Build the child's render context and render its template.
    #    The child gets its own context, independent of the parent's —
    #    each embedded view manages its own state.
    child_context: Dict[str, Any] = {}
    get_ctx = getattr(child, "get_context_data", None)
    if callable(get_ctx):
        try:
            child_context = dict(get_ctx())
        except Exception:  # noqa: BLE001 — fall back to empty context on error
            logger.exception(
                "live_render: child %s.get_context_data raised; rendering with empty context",
                child_cls.__name__,
            )
            child_context = {}
    child_context.setdefault("request", request)
    child_context.setdefault("view", child)

    # 7. Resolve + render the child's template. Prefer the inline
    #    ``template`` attribute when set (used pervasively in tests);
    #    otherwise fall through to Django's ``template_name`` resolver.
    inline = getattr(child, "template", None)
    if inline:
        rendered_inner = Template(inline).render(Context(child_context))
    else:
        template_name = getattr(child, "template_name", None)
        if not template_name:
            raise TemplateSyntaxError(
                "{%% live_render %%} child %r has neither ``template`` nor "
                "``template_name`` set" % view_path
            )
        rendered_inner = get_template(template_name).render(child_context, request)

    # 8. Stamp view_id on every event-attribute-bearing element in the
    #    rendered HTML so inbound events route to this child.
    rendered_stamped = _stamp_view_id(rendered_inner, view_id)

    # 9. Wrap in a [dj-view] container carrying ``data-djust-embedded`` —
    #    the client's DOM walker (``getEmbeddedViewId`` in
    #    01-dom-helpers-turbo.js) reads ``dataset.djustEmbedded`` to
    #    surface the id on outbound events as ``view_id``.
    #
    #    Phase B sticky branch: also carries ``dj-sticky-view="<id>"`` +
    #    ``dj-sticky-root`` attributes. The client's
    #    ``45-child-view.js`` module walks ``[dj-sticky-view]`` before a
    #    live_redirect is sent and detaches the subtree into an
    #    in-memory stash; after the new mount arrives, the server sends
    #    a ``sticky_hold`` frame listing surviving ids and the client
    #    re-attaches each stashed subtree at ``[dj-sticky-slot="<id>"]``
    #    in the new DOM via ``replaceWith()``.
    #
    #    Non-sticky branch behavior is unchanged (the Phase A contract).
    escaped_id = escape(view_id)
    if sticky_kwarg:
        escaped_sticky_id = escape(sticky_id_value)
        return mark_safe(
            '<div dj-view dj-sticky-view="'
            + escaped_sticky_id
            + '" dj-sticky-root data-djust-embedded="'
            + escaped_id
            + '">'
            + rendered_stamped
            + "</div>"
        )
    return mark_safe(
        '<div dj-view data-djust-embedded="' + escaped_id + '">' + rendered_stamped + "</div>"
    )
