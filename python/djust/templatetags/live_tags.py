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

import contextlib
import logging
import re
import threading
from collections.abc import Iterator
from typing import Any, Dict, Optional

from django import template
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.template import Context, Node, Template, TemplateSyntaxError
from django.template.base import NodeList, Parser, Token
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe

from .._html import build_tag
from ..config import config
from ..utils import get_csp_nonce

register = template.Library()
logger = logging.getLogger(__name__)


def _record_child_dj_model_allowlist(child: Any) -> None:
    """Populate an embedded ``{% live_render %}`` child's dj-model allowlist
    from the CHILD's own TEMPLATE SOURCE (CWE-915 mass-assignment guard).

    Single shared helper for all three live_render render sites (eager,
    lazy, sticky) so the same invariant can't drift across them
    (parallel-path cure, #1646). The child renders through Django's template
    engine (bypassing ``render_with_diff``), so its allowlist must be derived
    here. Source is preferred via ``child.get_template()`` (fully resolves
    ``{% extends %}``); falls back to the inline ``template`` / raw
    ``template_name`` source. Derivation is from the Rust template AST
    (Text-node literals only), immune to rendered-output poisoning.
    """
    if not hasattr(child, "_record_dj_model_fields_from_source"):
        return
    try:
        from ..utils import get_template_dirs

        source = None
        get_tmpl = getattr(child, "get_template", None)
        if callable(get_tmpl):
            source = get_tmpl()
        else:
            inline = getattr(child, "template", None)
            if inline:
                source = inline
            else:
                template_name = getattr(child, "template_name", None)
                if template_name:
                    from django.template import loader as _loader

                    source = _loader.get_template(template_name).template.source
        child._record_dj_model_fields_from_source(source, get_template_dirs())
    except Exception:  # noqa: BLE001 — never let allowlist collection break a render
        # Fail closed: the child's _record_dj_model_fields_from_source already
        # resets to an empty frozenset on its own error path; guard the
        # source-resolution above too.
        logger.warning("[dj-model] child auto-allowlist collection failed; failing closed")


# Matches </script> with any letter casing. Used by the `{% colocated_hook %}`
# body-escape defense to prevent a template-author typo from prematurely
# closing the <script> block that carries the hook body.
_SCRIPT_CLOSE_RE = re.compile(r"</(script)>", re.IGNORECASE)


# ---------------------------------------------------------------------------
# {% djust_client_config %} — FORCE_SCRIPT_NAME / sub-path mount support (#987)
# ---------------------------------------------------------------------------
#
# Emits a ``<meta name="djust-api-prefix" content="...">`` tag whose content
# is derived via Django's ``reverse()``. Because ``reverse()`` honors
# ``FORCE_SCRIPT_NAME`` and any ``api_patterns(prefix=...)`` mount prefix,
# this automatically resolves sub-path / mounted-app deployments without
# the template author having to hard-code the prefix.
#
# The client reads the meta tag at bootstrap time (``00-namespace.js``) and
# sets ``window.djust.apiPrefix``. Server-function / HTTP-API URLs are then
# built via ``window.djust.apiUrl(path)`` which joins prefix + path with
# slash normalization.
#
# See ``docs/website/guides/server-functions.md`` (Sub-path deploys section)
# and ``docs/website/guides/http-api.md`` for the developer-facing docs.


# URL names to try, in order. With ``api_patterns()`` the namespace
# ``djust_api`` is set; with ``include("djust.api.urls")`` there is no
# namespace. We probe both so either mount style works without the
# template author having to tell us which is in use.
_DJUST_API_CALL_URL_NAMES = ("djust_api:djust-api-call", "djust-api-call")

# Placeholder slugs fed into reverse(). Any valid ``<str>`` values work —
# we only need the URL so we can strip the trailing segment and recover
# the prefix. ``_`` is valid for Django's ``<str>`` converter.
_REVERSE_PROBE_KWARGS = {"view_slug": "_", "function_name": "_"}
_REVERSE_PROBE_SUFFIX = "call/_/_/"

# SSE URL names — mirrors the API pattern for #992 (SSE clients also
# hardcoded /djust/sse/). Probed order is the same: namespaced mount via
# sse_urlpatterns include first, then bare.
_DJUST_SSE_URL_NAMES = ("djust-sse-stream",)
_SSE_PROBE_KWARGS = {"session_id": "_"}
_SSE_PROBE_SUFFIX = "sse/_/"


def _resolve_api_prefix() -> str:
    """Return the API mount prefix via ``reverse()`` or ``""`` if unmounted.

    ``reverse()`` honors both ``FORCE_SCRIPT_NAME`` and any
    ``api_patterns(prefix=...)`` mount. We build a URL for the
    server-function call endpoint with sentinel slugs, then strip the
    ``call/_/_/`` suffix to recover the mount prefix.

    Returns an empty string if the API is not mounted (so the template
    tag can emit ``content=""`` and let the client fall back to its
    compile-time default ``/djust/api/``).
    """
    from django.urls import NoReverseMatch, reverse

    for name in _DJUST_API_CALL_URL_NAMES:
        try:
            url: str = reverse(name, kwargs=_REVERSE_PROBE_KWARGS)
        except NoReverseMatch:
            continue
        # url ends with the suffix we probed for; strip it to recover
        # the mount prefix. Using rsplit handles the case where the
        # prefix itself contained the literal string "call/_/_/"
        # (extraordinarily unlikely but harmless to be defensive).
        if url.endswith(_REVERSE_PROBE_SUFFIX):
            return url[: -len(_REVERSE_PROBE_SUFFIX)]
        # Defensive fallback: reverse() returned something unexpected,
        # hand it back as-is rather than silently mangling.
        return url
    return ""


def _resolve_sse_prefix() -> str:
    """Return the SSE mount prefix via ``reverse()`` or ``""`` if unmounted.

    Mirrors :func:`_resolve_api_prefix`. The SSE app is mounted via
    ``path("djust/", include(sse_urlpatterns))`` (see ``djust/sse.py``)
    and the named route is ``djust-sse-stream``. We probe with a sentinel
    session_id and strip ``sse/_/`` to recover the mount prefix.
    """
    from django.urls import NoReverseMatch, reverse

    for name in _DJUST_SSE_URL_NAMES:
        try:
            url: str = reverse(name, kwargs=_SSE_PROBE_KWARGS)
        except NoReverseMatch:
            continue
        if url.endswith(_SSE_PROBE_SUFFIX):
            return url[: -len(_SSE_PROBE_SUFFIX)]
        return url
    return ""


def _client_config_html(request: Any = None) -> Any:
    """Build the ``{% djust_client_config %}`` output (shared across engines).

    Emits the API/SSE prefix ``<meta>`` tags and, when the URLconf has any
    ``LiveView`` routes, appends the route-map ``<script>`` that populates
    ``window.djust._routeMap`` for zero-wiring ``dj-navigate`` (#1733,
    ADR-021 Stage 1).

    Both the Django-engine ``@register.simple_tag`` below and the Rust-engine
    ``ClientConfigTagHandler`` call this helper so their output stays
    byte-identical (the dual-registration invariant from PR #993).

    Security: the resolved prefixes are HTML-escaped via
    :func:`django.utils.html.escape`; the route map is emitted via
    :func:`djust.routing.get_route_map_script`, which ``json.dumps``-escapes
    the developer-defined route data and ``format_html``-escapes the CSP
    nonce. No user-input-to-HTML surface is introduced (#1078).
    """
    api_prefix = _resolve_api_prefix()
    sse_prefix = _resolve_sse_prefix()
    # escape() handles all HTML-special chars including the double-quote
    # (rendered as &quot;) so the value is safe to interpolate inside
    # content="..." — even if a developer accidentally puts
    # <script> in FORCE_SCRIPT_NAME.
    api_escaped = escape(api_prefix)
    sse_escaped = escape(sse_prefix)
    # String concatenation (not f-string) to comply with the repo rule
    # against f-string mark_safe interpolation.
    html = (
        '<meta name="djust-api-prefix" content="' + api_escaped + '">'
        '\n<meta name="djust-sse-prefix" content="' + sse_escaped + '">'
    )
    # auto_navigate (#1734, ADR-021 Stage 2): emit an opt-in flag the client
    # reads to install its delegated link-interception listener. Reads
    # LIVEVIEW_CONFIG['auto_navigate'] via the config singleton (the canonical
    # accessor; already imported above). Default OFF → no meta, no client
    # behavior change. Static content ("1"), so no escaping surface; CSP-clean
    # (a <meta>, not an inline <script>).
    if config.get("auto_navigate"):
        html = html + '\n<meta name="djust-auto-navigate" content="1">'
    # Auto-emit the route map (#1733). get_route_map_script returns "" when
    # the app has no LiveView routes (empty-safe — no stray <script>), and a
    # nonce-bearing <script> when request.csp_nonce is available.
    from ..routing import get_route_map_script

    route_script = get_route_map_script(request)
    if route_script:
        # route_script is already a SafeString (format_html). Concatenating a
        # SafeString to a plain str via mark_safe(...) below is safe because
        # both the meta markup (static + escape()d) and the route script
        # (format_html + json.dumps) are individually escaped.
        html = html + "\n" + str(route_script)
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def djust_client_config(context: Context) -> Any:
    """Emit client bootstrap configuration as ``<meta>`` tags + route map.

    Emits::

        <meta name="djust-api-prefix" content="<resolved API prefix>">
        <meta name="djust-sse-prefix" content="<resolved SSE prefix>">
        <script>window.djust._routeMap={...};</script>   (when LiveViews exist)

    The resolved prefix is computed via Django's ``reverse()`` so it
    honors ``FORCE_SCRIPT_NAME`` and any custom ``api_patterns(prefix=...)``
    mount. When the djust API is not mounted at all, the meta tag is still
    emitted with ``content=""`` (for easier debugging — an inspector
    looking at the rendered HTML can immediately see resolution failed),
    and the client falls back to its compile-time default ``/djust/api/``.

    The route-map ``<script>`` is auto-derived from the Django URLconf
    (every route whose callback resolves to a ``LiveView`` subclass) so
    ``dj-navigate`` works with **zero wiring** — no ``live_session()``
    required (#1733, ADR-021 Stage 1). It is **empty-safe**: when the app
    has no LiveView routes, no ``<script>`` is appended. The tag now takes
    the template context so it can read ``request.csp_nonce`` for the
    route-map script's nonce attribute.

    Usage::

        {% load live_tags %}
        <!DOCTYPE html>
        <html>
        <head>
            {% djust_client_config %}
            <!-- ...other head content... -->
        </head>

    Place the tag inside ``<head>`` BEFORE the djust client script is
    loaded so it is in the DOM when ``00-namespace.js`` runs. When djust
    auto-injects ``client.js`` via the LiveView post-processing path,
    the auto-injection also reads ``window.djust.apiPrefix`` if it's
    already been set — so either placement works.

    Security: the resolved prefix is HTML-escaped via
    :func:`django.utils.html.escape` so a mis-configured
    ``FORCE_SCRIPT_NAME`` value cannot break out of the ``content="..."``
    attribute. See ``test_tag_output_is_escaped``.
    """
    request = context.get("request") if context is not None else None
    return _client_config_html(request)


@register.simple_tag
def live_form(view: Any, **kwargs: Any) -> Any:
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
        <form dj-submit="submit_form">
            {% live_form view %}
            <button type="submit">Submit</button>
        </form>
    """
    if not hasattr(view, "as_live"):
        return "<!-- ERROR: View does not have as_live() method. Did you use FormMixin? -->"

    return view.as_live(**kwargs)


@register.simple_tag
def live_field(view: Any, field_name: str, **kwargs: Any) -> Any:
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
def live_errors(view: Any, field_name: str | None = None) -> str:
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
def field_value(view: Any, field_name: str) -> Any:
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
def has_errors(view: Any, field_name: str) -> bool:
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
        return bool(view.has_field_errors(field_name))
    return False


# ---------------------------------------------------------------------------
# {% live_input %} — standalone state-bound form field (#650)
# ---------------------------------------------------------------------------


# Default dj-* event per field type. Callers can override via ``event=``.
# text-like fields default to per-keystroke dj-input; select/radio/checkbox
# default to dj-change; hidden has no interactive event.
_DEFAULT_EVENT_BY_TYPE: Dict[str, Optional[str]] = {
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
def live_input(field_type: str = "text", **kwargs: Any) -> Any:
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
        # Defense-in-depth (#1646 / finding #18): field_type is a template-author
        # literal (not attacker input), but mark_safe(f"... {field_type!r} ...")
        # interpolates a variable into an HTML comment without escaping ``-->``.
        # format_html escapes the interpolated value, so the comment can't be
        # broken out of regardless of source.
        return format_html(
            "<!-- ERROR: {{% live_input %}} unknown field_type "
            "{} — supported: text, textarea, select, password, "
            "email, number, url, tel, search, hidden, checkbox, radio -->",
            repr(field_type),
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

    def __init__(
        self,
        name: str,
        nodelist: NodeList,
        force_global: bool = False,
    ) -> None:
        self.name = name
        self.nodelist = nodelist
        self.force_global = force_global

    def _namespace(self, context: Context) -> str:
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

    def render(self, context: Context) -> SafeString:
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
def do_colocated_hook(parser: Parser, token: Token) -> "ColocatedHookNode":
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
def djust_skeleton(
    context: Context,
    shape: str = "line",
    width: str | None = None,
    height: str | None = None,
    count: int = 1,
    class_: str | None = None,
) -> SafeString:
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
def djust_track_static() -> SafeString:
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

    def __init__(
        self,
        name_expr: Any,
        visible_expr: Any | None,
        eager_expr: Any | None,
        nodelist: NodeList,
    ) -> None:
        self.name_expr = name_expr
        self.visible_expr = visible_expr
        self.eager_expr = eager_expr
        self.nodelist = nodelist

    def _resolve_bool(self, expr: Any | None, context: Context, default: bool) -> bool:
        if expr is None:
            return default
        try:
            value = expr.resolve(context)
        except Exception:  # noqa: BLE001 — defensive: unresolvable var → default
            return default
        if value is None:
            return default
        return bool(value)

    def _resolve_name(self, context: Context) -> str:
        try:
            raw = self.name_expr.resolve(context)
        except Exception:  # noqa: BLE001
            raw = None
        if raw is None:
            return ""
        name = str(raw).strip()
        return name

    def render(self, context: Context) -> str:
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
def do_dj_activity(parser: Parser, token: Token) -> "DjActivityNode":
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


def _render_sticky_child_html(
    child: Any,
    view_id: str,
    sticky_id_value: str,
    request: Any,
    view_path: str,
) -> Any:
    """Render an ALREADY-mounted/registered sticky child to its wrapped HTML.

    Shared by the fresh-mount eager path (steps 6-9 of :func:`live_render`)
    and the #1813 (b1) live-instance-reuse hatch, so both paths produce
    byte-identical wrapper markup (parallel-path-drift guard, #1646). The
    caller is responsible for instance lifecycle: this helper does NOT mount
    or register — it only renders the child's CURRENT ``get_context_data()``,
    stamps ``view_id`` on event-bearing elements, and wraps in the sticky
    ``[dj-view][dj-sticky-view][dj-sticky-root]`` container.
    """
    from django.template.loader import get_template

    child_context: Dict[str, Any] = {}
    get_ctx = getattr(child, "get_context_data", None)
    if callable(get_ctx):
        try:
            child_context = dict(get_ctx())
        except Exception:  # noqa: BLE001 — fall back to empty context on error
            logger.exception(
                "live_render: child %s.get_context_data raised; rendering with empty context",
                type(child).__name__,
            )
            child_context = {}
    child_context.setdefault("request", request)
    child_context.setdefault("view", child)

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

    # Record the child's dj-model auto-allowlist from its own TEMPLATE SOURCE
    # so child update_model events (gated on the child's _dj_model_fields)
    # accept its dj-model inputs — this render bypasses render_with_diff
    # (#3 Stage-11 review, #1646 parallel-path).
    _record_child_dj_model_allowlist(child)
    rendered_stamped = _stamp_view_id(rendered_inner, view_id)
    escaped_id = escape(view_id)
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


# Context-render-local scratch key for tracking sticky_ids already
# registered in the current parent render pass. Used to raise
# TemplateSyntaxError on ``{% live_render 'X' sticky=True %}`` collisions.
_STICKY_IDS_SEEN_KEY = "_djust_sticky_ids_seen"


# ---------------------------------------------------------------------------
# Active-parent-view thread-local (#1784)
# ---------------------------------------------------------------------------
#
# The initial HTTP GET renders the page shell — including any embedded
# ``{% live_render %}`` tag — through the Rust renderer with a
# JSON-serialized context (see ``RequestMixin.get`` →
# ``TemplateMixin.render_full_template``). A JSON context structurally cannot
# carry the live parent ``LiveView`` object that ``live_render`` needs, so the
# tag historically raised ``TemplateSyntaxError: ... no parent view in the
# current render context`` and the request 500'd — which blocked
# server-rendering of sticky / app-shell pages entirely.
#
# Fix: ``render_full_template`` AND ``render_with_diff`` register the active
# parent view in this thread-local for the duration of their Rust render (via
# the :func:`active_parent_view` context manager, which save/restores so nested
# child renders don't blank the parent and the value is always restored even on
# error — never leaking across requests/threads). The ``live_render`` tag falls
# back to this thread-local only when the render context has no ``view``/``self``
# key — the WS path and the Django-engine path still carry a real ``view`` in
# context and are unaffected (the fallback is inert for them).
#
# Both render paths must set it: ``RequestMixin.get`` calls
# ``render_full_template`` AND then ``render_with_diff`` to establish the VDOM
# baseline, and both re-run the ``live_render`` tag through the Rust engine
# (parallel-path-drift class, per CLAUDE.md — fixing only one leaves the twin).
_active_parent_view = threading.local()


def get_active_parent_view() -> Any:
    """Return the active parent view registered for the current thread, or
    ``None`` when none is set (the common case for the WS / Django-engine
    render paths)."""
    return getattr(_active_parent_view, "view", None)


@contextlib.contextmanager
def active_parent_view(view: Any) -> Iterator[None]:
    """Context manager that registers ``view`` as the active parent
    :class:`~djust.live_view.LiveView` for the current thread so an embedded
    ``{% live_render %}`` tag can find it during a Rust render that carries
    only a JSON-serialized context (#1784).

    The PREVIOUS value is saved on entry and restored on exit (not merely
    cleared) so nested renders — a parent render that triggers a child's
    ``render_with_diff`` — restore the parent as the active view rather than
    blanking it. Restoration runs even on error, so the thread-local never
    leaks across requests/threads.
    """
    previous = getattr(_active_parent_view, "view", None)
    _active_parent_view.view = view
    try:
        yield
    finally:
        _active_parent_view.view = previous


@register.simple_tag(takes_context=True)
def live_render(context: Context, view_path: str, **kwargs: Any) -> Any:
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
    #
    # On the initial HTTP GET the page shell is rendered through the Rust
    # engine with a JSON-serialized context that cannot carry the live
    # ``LiveView`` object, so ``view``/``self`` are absent. Fall back to the
    # thread-local registered by ``render_full_template`` (#1784). The WS path
    # and the Django-engine path put a real ``view`` in the context, so the
    # fallback is inert for them.
    parent = context.get("view") or context.get("self")
    if parent is None or not isinstance(parent, LiveView):
        parent = get_active_parent_view()
    if parent is None or not isinstance(parent, LiveView):
        raise TemplateSyntaxError(
            "{% live_render %} must be called inside a LiveView template; "
            "no parent view in the current render context"
        )

    # 4. Instantiate + mount the child.
    #
    # On the initial HTTP GET shell render (#1784) the context flows through
    # the Rust engine as a JSON-serialized dict, so ``request`` is a stringified
    # placeholder (``normalize_django_value`` turns the WSGIRequest into a str).
    # The child needs the real request object for ``mount(request, ...)`` and
    # ``check_view_auth``, so fall back to the parent view's live
    # ``request`` attribute when the context value isn't an ``HttpRequest``.
    from django.http import HttpRequest

    request = context.get("request")
    if not isinstance(request, HttpRequest):
        request = getattr(parent, "request", None)
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
        # Past the truthiness guard, ``sticky_id`` is a non-empty class string.
        # Pin it to a ``str``-typed local so downstream helpers that require a
        # ``str`` slot key (``_render_sticky_child_html``) type-check without
        # widening their contract back to ``Any | None``.
        sticky_id_value = str(sticky_id_value)
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

        # ADR-014: auto-detect a preserved sticky carried over from the
        # previous view via ``LiveViewConsumer._sticky_preserved``. When
        # found, re-register the survivor onto the new parent and emit
        # only a ``<dj-sticky-slot>`` placeholder — the consumer's
        # post-render slot scan + the client's ``replaceWith`` reattach
        # then complete the round-trip without the survivor's mount()
        # ever running again. Falls through to fresh-mount on the HTTP
        # GET path (no consumer back-reference) and on first navigations
        # (empty ``_sticky_preserved``).
        consumer = getattr(parent, "_ws_consumer", None)
        preserved_map = getattr(consumer, "_sticky_preserved", None) if consumer else None
        survivor = preserved_map.get(sticky_id_value) if preserved_map else None
        if survivor is not None:
            try:
                parent._register_child(sticky_id_value, survivor)
            except ValueError:
                logger.warning(
                    "live_render: sticky %r already registered on parent — falling through",
                    sticky_id_value,
                )
            else:
                # Load-bearing: ``_preserve_sticky_children`` set the
                # survivor's request to its own staging-time request,
                # which is a different object than the new parent's
                # mount-time request. Middleware on the new request
                # may have populated attributes (auth, session, etc.)
                # the survivor's handlers will read.
                survivor.request = request
                # ``consumer`` is guaranteed non-None here: ``survivor`` was
                # read from ``preserved_map``, which is only set when
                # ``consumer`` is truthy (see the ``... if consumer else None``
                # guard above). Assert for the type-checker; inert at runtime.
                assert consumer is not None
                auto_set = getattr(consumer, "_sticky_auto_reattached", None)
                if auto_set is None:
                    consumer._sticky_auto_reattached = {sticky_id_value}
                else:
                    auto_set.add(sticky_id_value)
                logger.debug(
                    "live_render: auto-reattach sticky %r on %s",
                    sticky_id_value,
                    view_path,
                )
                return mark_safe('<div dj-sticky-slot="' + escape(sticky_id_value) + '"></div>')

    # PR-B (ADR-015): ``lazy=True`` opt-in. Defer the child mount +
    # render to a thunk that runs after the parent shell has flushed.
    # See ``arender_chunks`` Phase 5 for thunk invocation; see
    # ``RequestMixin.aget`` for thunk transfer from view-stash to
    # emitter. The placeholder emitted here is replaced client-side by
    # ``static/djust/src/50-lazy-fill.js`` once the fill chunk arrives.
    lazy_kwarg = kwargs.pop("lazy", False)
    if lazy_kwarg:
        if sticky_kwarg:
            # Hard incompatibility per ADR §"Failure modes": sticky
            # preservation depends on the slot DOM existing at mount-
            # frame time so the WS reattach can ``replaceWith`` the
            # stashed subtree. Lazy renders the slot AFTER mount, so
            # the stash-target doesn't exist when reattach runs.
            raise TemplateSyntaxError(
                "{%% live_render %%} sticky=True and lazy=True are mutually "
                "exclusive on %r — sticky preservation requires the slot to "
                "exist at mount-frame time, lazy defers slot rendering. "
                "Pick one." % view_path
            )
        # Normalize the three forms of ``lazy=`` into a single config dict.
        lazy_config: Dict[str, Any]
        if lazy_kwarg is True:
            lazy_config = {
                "trigger": "flush",
                "timeout_s": 15.0,
                "on_error": "fallback",
                "placeholder": None,
            }
        elif lazy_kwarg == "visible":
            lazy_config = {
                "trigger": "visible",
                "timeout_s": 15.0,
                "on_error": "fallback",
                "placeholder": None,
            }
        elif isinstance(lazy_kwarg, dict):
            lazy_config = {
                "trigger": "flush",
                "timeout_s": 15.0,
                "on_error": "fallback",
                "placeholder": None,
            }
            for key, value in lazy_kwarg.items():
                if key not in lazy_config:
                    raise TemplateSyntaxError(
                        "{%% live_render %%} lazy= dict has unknown key "
                        "%r; valid keys: trigger, timeout_s, on_error, "
                        "placeholder" % key
                    )
                lazy_config[key] = value
            if lazy_config["trigger"] not in ("flush", "visible"):
                raise TemplateSyntaxError(
                    "{%% live_render %%} lazy['trigger']=%r — valid: "
                    "'flush' (default) or 'visible'." % lazy_config["trigger"]
                )
            if lazy_config["on_error"] not in ("fallback", "close"):
                raise TemplateSyntaxError(
                    "{%% live_render %%} lazy['on_error']=%r — valid: "
                    "'fallback' (default) or 'close'." % lazy_config["on_error"]
                )
            try:
                lazy_config["timeout_s"] = float(lazy_config["timeout_s"])
                if lazy_config["timeout_s"] <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise TemplateSyntaxError(
                    "{%% live_render %%} lazy['timeout_s']=%r must be a "
                    "positive number." % lazy_config["timeout_s"]
                )
        else:
            raise TemplateSyntaxError(
                "{%% live_render %%} lazy= accepts True, 'visible', or a "
                "dict — got %r." % lazy_kwarg
            )

        # Capture state for the thunk closure now (before kwargs may
        # be mutated by the eager branch path).
        captured_kwargs = dict(kwargs)
        view_id = parent._assign_view_id(preferred_view_id)
        # Reserve the id by registering a placeholder NOW so a sibling
        # eager `_register_child` collision is detectable (the actual
        # child is created at thunk-fire time, but registry slot is
        # locked here).
        # We don't register a real child until the thunk fires;
        # _assign_view_id has already mutated the parent's id-counter.
        escaped_id: str = escape(view_id)

        # #1147 — CSP nonce propagation. When the project uses
        # ``django-csp`` (or compatible) middleware, ``request.csp_nonce``
        # carries the per-response nonce. Strict-CSP deployments
        # (``script-src 'nonce-...'``, no ``unsafe-inline``) reject the
        # inline ``<script>`` activator unless it carries a matching
        # nonce attribute. We thread the nonce through to BOTH the
        # ``<template>`` element and the activator ``<script>``. When
        # no nonce is set (the common case for sites without CSP
        # middleware), the attribute is omitted entirely — preserving
        # backward compatibility for non-CSP deployments.
        _csp_nonce_raw = get_csp_nonce(request)
        _csp_nonce_attr = ' nonce="' + escape(_csp_nonce_raw) + '"' if _csp_nonce_raw else ""

        async def _lazy_thunk() -> bytes:
            """Run the eager mount+render path and emit the fill chunk
            envelope. Catches its own exceptions and wraps them in a
            data-status="error" envelope per ADR §"Error propagation".
            """
            from asyncio import wait_for, TimeoutError as _AsyncioTimeout
            from asgiref.sync import sync_to_async

            def _render_eager() -> str:
                # Eager mount + render path — same as the non-lazy
                # branch below. Returns the stamped child HTML.
                child = child_cls()
                child.request = request
                auth_redirect = check_view_auth(child, request)
                if auth_redirect is not None:
                    raise PermissionError("child %r denied access (auth gate)" % view_path)
                mount_fn = getattr(child, "mount", None)
                if callable(mount_fn):
                    mount_fn(request, **captured_kwargs)
                # Object-permission check (ADR-017) for the lazy embedded child
                # — same as the eager path (finding #12). No-op unless the child
                # overrides get_object; fail-closed otherwise.
                from ..auth.core import enforce_object_permission

                try:
                    enforce_object_permission(child, request)
                except PermissionDenied:
                    raise PermissionError("child %r denied access (object permission)" % view_path)
                # Don't re-register the child by view_id — _assign_view_id
                # already incremented the counter; calling _register_child
                # here is what locks the slot. The lazy-fill DOM ends up
                # under [data-djust-embedded="<view_id>"] so events route
                # correctly.
                parent._register_child(view_id, child)
                child_context: Dict[str, Any] = {}
                get_ctx_fn = getattr(child, "get_context_data", None)
                if callable(get_ctx_fn):
                    try:
                        child_context = dict(get_ctx_fn())
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "live_render lazy: child %s.get_context_data raised; "
                            "rendering with empty context",
                            child_cls.__name__,
                        )
                        child_context = {}
                child_context.setdefault("request", request)
                child_context.setdefault("view", child)
                inline_template = getattr(child, "template", None)
                if inline_template:
                    rendered_inner = Template(inline_template).render(Context(child_context))
                else:
                    template_name = getattr(child, "template_name", None)
                    if not template_name:
                        raise RuntimeError(
                            "child %r has neither ``template`` nor "
                            "``template_name`` set" % view_path
                        )
                    rendered_inner = get_template(template_name).render(child_context, request)
                # Record the child's dj-model auto-allowlist from its own
                # TEMPLATE SOURCE so child update_model events (gated on the
                # child's _dj_model_fields) accept its dj-model inputs — this
                # render bypasses render_with_diff (#3 Stage-11 review, #1646
                # parallel-path).
                _record_child_dj_model_allowlist(child)
                rendered_stamped = _stamp_view_id(rendered_inner, view_id)
                # Wrap in a [dj-view] container so events route via
                # data-djust-embedded just like the eager path.
                return (
                    '<div dj-view data-djust-embedded="'
                    + escaped_id
                    + '">'
                    + rendered_stamped
                    + "</div>"
                )

            try:
                rendered_html = await wait_for(
                    sync_to_async(_render_eager)(),
                    timeout=lazy_config["timeout_s"],
                )
                status = "ok"
                body = rendered_html
            except _AsyncioTimeout:
                status = "timeout"
                body = (
                    '<dj-error aria-live="polite">Lazy child '
                    + escaped_id
                    + " timed out after "
                    + escape(str(lazy_config["timeout_s"]))
                    + "s.</dj-error>"
                )
            except PermissionError as exc:
                status = "error"
                body = '<dj-error aria-live="polite">' + escape(str(exc)) + "</dj-error>"
            except Exception as exc:  # noqa: BLE001
                logger.exception("live_render lazy thunk for %s raised", view_path)
                status = "error"
                body = (
                    '<dj-error aria-live="polite">Lazy child '
                    + escaped_id
                    + " failed: "
                    + escape(type(exc).__name__)
                    + "</dj-error>"
                )

            # Build the fill envelope. Browsers parse <template> inertly;
            # the inline <script> activator runs at parse time and
            # window.djust.lazyFill('X') from 50-lazy-fill.js performs
            # the slot replacement.
            #
            # #1147 — both the <template> and the <script> activator
            # carry ``nonce="..."`` when ``request.csp_nonce`` is set,
            # so strict-CSP deployments (``script-src 'nonce-...'``)
            # don't reject the activator.
            envelope = (
                '<template id="djl-fill-'
                + escaped_id
                + '" data-target="'
                + escaped_id
                + '" data-status="'
                + status
                + '"'
                + _csp_nonce_attr
                + ">"
                + body
                + "</template>"
                + "<script"
                + _csp_nonce_attr
                + '>window.djust&&window.djust.lazyFill&&window.djust.lazyFill("'
                + escaped_id
                + '")</script>'
            )
            return envelope.encode("utf-8")

        # Stash the thunk on the OUTERMOST parent. ``aget`` reads
        # ``self._lazy_thunks`` after sync_to_async(get) returns and
        # transfers them onto the chunk emitter.
        if not hasattr(parent, "_lazy_thunks") or parent._lazy_thunks is None:
            parent._lazy_thunks = []
        parent._lazy_thunks.append((view_id, _lazy_thunk))

        # Synchronous placeholder return. Custom <dj-lazy-slot> element
        # — parsed as HTMLUnknownElement on browsers without a
        # registered custom-element class; fully selectable via
        # querySelector and replaceable via replaceWith.
        custom_placeholder = lazy_config["placeholder"]
        if custom_placeholder:
            inner = str(custom_placeholder)
        else:
            inner = ""
        # #1147 — propagate ``request.csp_nonce`` onto the <dj-lazy-slot>
        # placeholder so client code can read it via ``getAttribute``
        # if it ever needs to inject CSP-bound scripts under the same
        # policy. The load-bearing nonce is the one on the <script>
        # activator emitted by the thunk above.
        return mark_safe(
            '<dj-lazy-slot data-id="'
            + escaped_id
            + '" data-trigger="'
            + lazy_config["trigger"]
            + '"'
            + _csp_nonce_attr
            + ">"
            + inner
            + "</dj-lazy-slot>"
        )

    # 4-pre. (#1813 (b1)) Live-instance-reuse hatch — the STRUCTURAL CURE for
    #     sticky-child state loss on parent re-render.
    #
    #     ``render_with_diff()`` (and ``render_full_template`` on the GET path)
    #     re-execute this tag on EVERY parent render with ``parent`` set to the
    #     LIVE parent view (via ``active_parent_view(self)``). The parent's
    #     :class:`StickyChildRegistry` (``_child_views``, keyed by ``sticky_id``
    #     since ``preferred_view_id = sticky_id`` above) therefore still holds
    #     the child instance that was mounted on a PRIOR render — carrying any
    #     state the user mutated via embedded-child events.
    #
    #     Without this hatch the tag always builds a FRESH ``child_cls()`` +
    #     ``mount()``, so every parent re-render (and every ``_recovery_html``
    #     snapshot taken during a parent event) renders the child at mount
    #     defaults. When the client later falls back to ``request_html``
    #     recovery, the sticky child is reset and the user's interactions are
    #     discarded — the #1813 data-loss bug.
    #
    #     The two pre-existing escape hatches do NOT cover this:
    #       * ``_sticky_preserved`` auto-reattach (above) only fires on a
    #         live_redirect/navigation (the #1471 reconnect path);
    #       * ``restore_sticky_child_state`` (below) is gated behind
    #         ``enable_state_snapshot=True`` on BOTH parent + child and reads
    #         from the session — inert in the default config.
    #
    #     This hatch is INDEPENDENT of those: it reuses the in-memory live
    #     instance the parent already holds. It composes with — does not bypass
    #     — the others, because it only fires when the registry already has a
    #     live child for this ``sticky_id`` (i.e. on the SECOND and later
    #     renders of a long-lived WS parent). On the first render the registry
    #     is empty, so we fall through to the existing fresh-mount / restore
    #     path. It is keyed by ``sticky_id`` (== ``preferred_view_id`` for a
    #     sticky child) so it only applies to sticky children — non-sticky
    #     ``{% live_render %}`` re-creates per render exactly as before.
    if sticky_kwarg and preferred_view_id is not None:
        existing_child = None
        get_child = getattr(parent, "_get_child_view", None)
        if callable(get_child):
            try:
                existing_child = get_child(preferred_view_id)
            except Exception:  # noqa: BLE001 — never break the parent render
                logger.exception(
                    "live_render: live-instance lookup for sticky %r failed; mounting fresh",
                    preferred_view_id,
                )
                existing_child = None
        # Only reuse a genuine sticky instance of the EXPECTED class. A class
        # mismatch (two embeds sharing a sticky_id across different child
        # classes — already rejected for one render pass by the uniqueness
        # check, but defensive across renders) falls through to fresh mount.
        if (
            isinstance(existing_child, child_cls)
            and getattr(existing_child, "sticky_id", None) == sticky_id_value
        ):
            # Refresh the live request so handlers/middleware-populated attrs
            # (auth, session) read from the CURRENT parent render's request —
            # mirrors the ``_sticky_preserved`` auto-reattach path above.
            existing_child.request = request
            # ``sticky_kwarg`` is True here, so ``sticky_id_value`` passed the
            # non-empty guard above and is a ``str``. Narrow for the helper's
            # ``str`` slot-key contract (inert at runtime).
            assert sticky_id_value is not None
            return _render_sticky_child_html(
                existing_child,
                preferred_view_id,
                sticky_id_value,
                request,
                view_path,
            )

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

    # 4b. ADR-018 iter 18b — sticky-child state restore (Decisions 2 + 6).
    #     A sticky child whose state was persisted by 18a's SAVE path has
    #     that state restored here, BEFORE mount(), in lieu of mount()'s
    #     state-init — mirroring the parent's own skip-mount()-on-saved-state
    #     path at websocket.py:handle_mount. ``restore_sticky_child_state``
    #     internally returns False (and the fresh mount() runs) when the
    #     child is non-sticky, the both-opt-in gate fails, the session is
    #     absent, or no saved state exists — so there is NO behavior change
    #     for the unsaved / non-opted-in case. Wrapped in try/except: a
    #     malformed session entry must fall through to a fresh mount(),
    #     never break the parent's render.
    restored = False
    if sticky_kwarg:
        from ..mixins.sticky import restore_sticky_child_state

        try:
            # ADR-018 iter 18c — path-derivation invariant. ``parent_path``
            # (the 4th arg) MUST match the path used at SAVE time: the WS save
            # uses ``_djust_mount_request.path`` (websocket.py), the HTTP save
            # uses ``request.path`` (mixins/request.py). On all current
            # transports the restore-time ``request.path`` and the save-time
            # parent mount-request path coincide — the parent is re-mounted on
            # the same URL it was saved on. If a future transport makes the
            # restore-time request path differ from the parent's original
            # mount path, this key derivation breaks SILENTLY: the restore
            # looks under the wrong ``liveview_<path>__sticky__<id>`` key and
            # falls through to a fresh ``mount()``. Keep save and restore
            # path-derivation in lockstep.
            restored = restore_sticky_child_state(
                child,
                parent,
                getattr(request, "session", None),
                getattr(request, "path", "/"),
            )
        except Exception:  # noqa: BLE001 — restore must never break render
            logger.exception(
                "live_render: sticky restore for %r failed; mounting fresh",
                view_path,
            )
            restored = False

    if not restored:
        mount = getattr(child, "mount", None)
        if callable(mount):
            mount(request, **kwargs)

    # 4c. Object-permission check (ADR-017) for the embedded child. The child's
    #     view-level auth ran above (check_view_auth); the object-level step
    #     must run too, or an embedded object-scoped child renders a denied
    #     object (finding #12). No-op for children that don't override
    #     get_object. Fail-closed: deny the embed rather than leak.
    from ..auth.core import enforce_object_permission

    try:
        enforce_object_permission(child, request)
    except PermissionDenied:
        raise TemplateSyntaxError(
            "{%% live_render %%} target %r denied access: object-level permission "
            "check failed for the requested object." % view_path
        )

    # 5. Assign the view_id and register on the parent. _register_child
    #    wires parent/view_id back-references on the child.
    view_id = parent._assign_view_id(preferred_view_id)
    parent._register_child(view_id, child)

    # 6-9. Sticky branch: render via the shared helper so the fresh-mount path
    #      and the #1813 (b1) live-instance-reuse hatch emit byte-identical
    #      wrapper markup (parallel-path-drift guard, #1646). The helper builds
    #      the child's context, renders its template, stamps the view_id, and
    #      wraps in the ``[dj-view][dj-sticky-view][dj-sticky-root]`` container.
    #
    #      The sticky wrapper carries ``dj-sticky-view`` + ``dj-sticky-root``:
    #      the client's ``45-child-view.js`` walks ``[dj-sticky-view]`` before a
    #      live_redirect, detaches the subtree into an in-memory stash, and
    #      re-attaches each at ``[dj-sticky-slot="<id>"]`` via ``replaceWith()``
    #      after the new mount arrives (the #1471 reconnect path).
    if sticky_kwarg:
        # ``sticky_id_value`` passed the non-empty guard above (set whenever
        # ``sticky_kwarg`` is True) and is a ``str``. Narrow for the helper's
        # ``str`` slot-key contract (inert at runtime).
        assert sticky_id_value is not None
        return _render_sticky_child_html(child, view_id, sticky_id_value, request, view_path)

    # Non-sticky branch (the Phase A contract) — unchanged. Build the child's
    # context, render its template, stamp the view_id, and wrap in a plain
    # ``[dj-view]`` container carrying ``data-djust-embedded`` (the client's DOM
    # walker reads ``dataset.djustEmbedded`` to surface ``view_id`` on events).
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

    # Record the child's dj-model auto-allowlist from its own TEMPLATE SOURCE
    # so child update_model events (gated on the child's _dj_model_fields)
    # accept its dj-model inputs — this render bypasses render_with_diff
    # (#3 Stage-11 review, #1646 parallel-path).
    _record_child_dj_model_allowlist(child)
    rendered_stamped = _stamp_view_id(rendered_inner, view_id)
    escaped_id = escape(view_id)
    return mark_safe(
        '<div dj-view data-djust-embedded="' + escaped_id + '">' + rendered_stamped + "</div>"
    )
