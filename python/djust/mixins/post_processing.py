"""
PostProcessingMixin - Debug info, React hydration, and client script injection for LiveView.
"""

import json
import logging
import re
import sys
from typing import TYPE_CHECKING, Any, Dict, cast
from .._class_snapshot import attribute_names

logger = logging.getLogger(__name__)

# #2987: find the document's real <head>/<body> boundaries. Anything tag-shaped
# inside a raw-text region is text, not markup, so the lookups run on a masked
# copy: ``_mask_raw_text`` (script/style bodies and comments, #2663) plus the
# RCDATA elements title/textarea. The masks are length-preserving, so positions
# found in the masked copy index the original string. An unterminated region
# runs to the end of the document, as it does in the HTML tokenizer.
_RCDATA_RE = re.compile(
    r"<(title|textarea)(?=[\s/>])[^<>]*>.*?(?:</\1[^<>]*>|\Z)",
    re.DOTALL | re.IGNORECASE,
)
# A tag name ends at whitespace, "/" or ">": ``<body-shell>`` is not ``<body>``.
# End tags may carry junk before ">" (``</head foo>``), as browsers accept.
# ``[^<>]*`` (not ``[^>]*``) keeps a tag from spanning into the next one, and
# keeps the scan linear when many tags have no ">" (PR #3017 review).
_HEAD_CLOSE_RE = re.compile(r"</head(?=[\s/>])[^<>]*>", re.IGNORECASE)
_BODY_OPEN_RE = re.compile(r"<body(?=[\s/>])", re.IGNORECASE)
_BODY_CLOSE_TAG_RE = re.compile(r"</body(?=[\s/>])[^<>]*>", re.IGNORECASE)
_HTML_CLOSE_TAG_RE = re.compile(r"</html(?=[\s/>])[^<>]*>", re.IGNORECASE)


def _mask_document_text(html: str) -> str:
    from .template import _mask_raw_text

    masked = _mask_raw_text(html)
    return _RCDATA_RE.sub(lambda m: "\x00" * len(m.group(0)), masked)


def _find_head_close(masked: str) -> int:
    """Index of the document's real closing ``</head>`` tag, or ``-1`` (#2987).

    ``masked`` is the page after :func:`_mask_document_text`. A plain
    ``str.replace("</head>", ...)`` hit the first (or every) ``</head>``
    *string*, including one inside an inline script or a comment. This returns
    the first real ``</head>``, provided it comes before ``<body``.
    """
    head_close = _HEAD_CLOSE_RE.search(masked)
    if head_close is None:
        return -1
    body_open = _BODY_OPEN_RE.search(masked, 0, head_close.start())
    return -1 if body_open else head_close.start()


def _find_body_close(masked: str) -> int:
    """Index of the document's last real closing ``</body>`` tag, or ``-1``."""
    found = -1
    for match in _BODY_CLOSE_TAG_RE.finditer(masked):
        found = match.start()
    return found


def _find_html_close(masked: str) -> int:
    """Index of the document's last real closing ``</html>`` tag, or ``-1``
    (#3018: the handler-metadata fallback when a page has no ``</body>``)."""
    found = -1
    for match in _HTML_CLOSE_TAG_RE.finditer(masked):
        found = match.start()
    return found


_MISSING = object()


def _property_types() -> tuple:
    import functools

    from django.utils.functional import cached_property as django_cached_property

    return (property, functools.cached_property, django_cached_property)


def _read_debug_attr(view: Any, name: str) -> "tuple[Any, Dict[str, Any] | None]":
    """Read one attribute for the legacy debug panel (#3103).

    Returns ``(value, None)`` for a readable attribute, ``(_MISSING, None)``
    when it does not exist, or ``(_MISSING, placeholder)`` when the panel lists
    the name without a value. A property is code, not state: running it could
    raise anything or query the database on every debug render, so it is
    listed as not evaluated (a cached property already computed is state and
    is read from the instance). Any other descriptor that raises is reported
    as unavailable with its exception type, and the rest of the panel renders.
    """
    import inspect

    instance_dict = getattr(view, "__dict__", {})
    if name not in instance_dict:
        try:
            static = inspect.getattr_static(view, name)
        except AttributeError:
            static = _MISSING
        if isinstance(static, _property_types()):
            return _MISSING, {
                "name": name,
                "type": "property",
                "value": "<property: not evaluated>",
                "size_bytes": 0,
            }
    try:
        return getattr(view, name), None
    except AttributeError:
        return _MISSING, None
    except Exception as exc:  # noqa: BLE001 -- any descriptor may raise; report it, keep going
        return _MISSING, {
            "name": name,
            "type": "unavailable",
            "value": "<unavailable: %s>" % type(exc).__name__,
            "size_bytes": 0,
        }


class PostProcessingMixin:
    """Post-processing: get_debug_info, _hydrate_react_components, _inject_client_script."""

    if TYPE_CHECKING:
        # Cooperating method supplied by the host class (LiveView via
        # ContextMixin.get_context_data, mixins/context.py). Declared type-only
        # so the strict-island mypy run resolves it on the mixin without a
        # runtime change — this mixin is never instantiated standalone.
        def get_context_data(self, **kwargs: Any) -> Dict[str, Any]: ...
        def _extract_handler_metadata(self) -> Dict[str, Dict[str, Any]]: ...

    def get_debug_info(self) -> Dict[str, Any]:
        """
        Get debug information about this LiveView instance.

        Returns:
            Dict with debug information
        """
        from .._exposure import explicit_debug_projection

        explicit = explicit_debug_projection(self)
        if explicit is not None:
            # Do not inspect arbitrary descriptors or handler default values.
            # Typed public handler metadata will be supplied by ADR-036/037.
            from ..config import config

            return {
                **self._explicit_debug_update(explicit),
                "handlers": {},
                "template": None,
                "config": {"maxHistory": config.get("debug_panel_max_history", 50)},
            }
        from .._parameter_metadata import _event_methods
        from ..validation import get_handler_signature_info

        handlers = {}
        variables = {}

        # Exactly the handlers dispatch resolves (ADR-037 D1), never a second
        # discovery: only @event_handler-decorated methods are callable.
        decorators = self._extract_handler_metadata()
        for name, method in sorted(_event_methods(self).items()):
            sig_info = get_handler_signature_info(method)
            handlers[name] = {
                "name": name,
                "params": sig_info["params"],
                "description": sig_info["description"],
                "accepts_kwargs": sig_info["accepts_kwargs"],
                "decorators": decorators.get(name, {}),
            }

        for name in attribute_names(self):  # dir() races class writes (#3151)
            if name.startswith("_") or name in handlers:
                continue

            attr, placeholder = _read_debug_attr(self, name)
            if placeholder is not None:
                variables[name] = placeholder
                continue
            if attr is _MISSING:
                continue

            if callable(attr) and hasattr(attr, "__func__"):
                continue  # A method, not a state variable.

            elif (
                not callable(attr)
                and not isinstance(attr, type)
                and not hasattr(attr, "__module__")
            ):
                try:
                    from django import forms

                    if isinstance(attr, forms.Form):
                        continue

                    type_name = type(attr).__name__

                    try:
                        serialized = json.dumps(attr, default=str)
                        size_bytes = len(serialized.encode("utf-8"))
                    except (TypeError, ValueError):
                        size_bytes = sys.getsizeof(attr)

                    value_repr = repr(attr)
                    if len(value_repr) > 100:
                        value_repr = value_repr[:100] + "..."

                    variables[name] = {
                        "name": name,
                        "type": type_name,
                        "value": value_repr,
                        "size_bytes": size_bytes,
                    }
                except Exception:
                    logger.debug("Failed to collect debug panel variable '%s'", name)

        from ..config import config

        max_history = config.get("debug_panel_max_history", 50)

        return {
            "view_class": self.__class__.__name__,
            "handlers": handlers,
            "variables": variables,
            "state_sizes": self._debug_state_sizes(),
            "template": self.template_name if hasattr(self, "template_name") else None,
            "config": {"maxHistory": max_history},
        }

    def _debug_state_sizes(self) -> Dict[str, Dict[str, Any]]:
        """Return size breakdown of public state variables for debug toolbar."""
        from .._exposure import explicit_debug_projection

        explicit = explicit_debug_projection(self)
        if explicit is not None:
            return cast(
                Dict[str, Dict[str, Any]], self._explicit_debug_update(explicit)["state_sizes"]
            )
        # #762: Filter framework-internal attrs from the observability payload
        # so state_sizes reflects user-owned reactive state, not framework config.
        from ..live_view import _FRAMEWORK_INTERNAL_ATTRS

        sizes: Dict[str, Dict[str, Any]] = {}
        for attr_name in sorted(vars(self)):
            if attr_name.startswith("_"):
                continue
            if attr_name in _FRAMEWORK_INTERNAL_ATTRS:
                continue
            value = getattr(self, attr_name)
            if callable(value):
                continue
            try:
                serialized = json.dumps(value, default=str)
                sizes[attr_name] = {
                    "memory": sys.getsizeof(value),
                    "serialized": len(serialized.encode("utf-8")),
                }
            except (TypeError, ValueError):
                sizes[attr_name] = {
                    "memory": sys.getsizeof(value),
                    "serialized": None,
                }
        return sizes

    def get_debug_update(self) -> Dict[str, Any]:
        """
        Get a slim debug payload for event responses (skip static handler metadata).

        Unlike get_debug_info() which includes handler signatures (~20KB+),
        this returns only the parts that change per event: variables and view class.
        Handlers are static and only sent on initial mount via get_debug_info().
        """
        from .._exposure import explicit_debug_projection

        explicit = explicit_debug_projection(self)
        if explicit is not None:
            return self._explicit_debug_update(explicit)
        # #762: Filter framework-internal attrs from the observability payload.
        from ..live_view import _FRAMEWORK_INTERNAL_ATTRS

        variables = {}

        for name in attribute_names(self):  # dir() races class writes (#3151)
            if name.startswith("_"):
                continue
            if name in _FRAMEWORK_INTERNAL_ATTRS:
                continue

            attr, placeholder = _read_debug_attr(self, name)
            if placeholder is not None:
                variables[name] = placeholder
                continue
            if attr is _MISSING:
                continue

            if callable(attr):
                continue
            if isinstance(attr, type) or hasattr(attr, "__module__"):
                continue

            try:
                from django import forms

                if isinstance(attr, forms.Form):
                    continue

                type_name = type(attr).__name__

                try:
                    serialized = json.dumps(attr, default=str)
                    size_bytes = len(serialized.encode("utf-8"))
                except (TypeError, ValueError):
                    size_bytes = sys.getsizeof(attr)

                value_repr = repr(attr)
                if len(value_repr) > 100:
                    value_repr = value_repr[:100] + "..."

                variables[name] = {
                    "name": name,
                    "type": type_name,
                    "value": value_repr,
                    "size_bytes": size_bytes,
                }
            except Exception:
                logger.debug("Failed to collect debug panel variable '%s'", name)

        return {
            "view_class": self.__class__.__name__,
            "variables": variables,
            "state_sizes": self._debug_state_sizes(),
        }

    def _explicit_debug_update(self, projection: Dict[str, Any]) -> Dict[str, Any]:
        """Format already-bounded debug primitives, never inspect the view again."""
        variables = {}
        sizes = {}
        for name, value in projection.items():
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            size = len(encoded.encode("utf-8"))
            redacted = value == "[redacted]"
            variables[name] = {
                "name": name,
                "type": "redacted" if redacted else type(value).__name__,
                "value": encoded[:100] + ("..." if len(encoded) > 100 else ""),
                "size_bytes": None if redacted else size,
            }
            # Sizes describe the exported value only; hidden values are not read.
            sizes[name] = {"memory": None, "serialized": None if redacted else size}
        return {"view_class": type(self).__name__, "variables": variables, "state_sizes": sizes}

    def _hydrate_react_components(self, html: str) -> str:
        """
        Post-process HTML to hydrate React component placeholders.
        """
        from ..react import react_components
        import html as html_module
        import json as json_module

        pattern = r'<div data-react-component="([^"]+)" data-react-props=\'([^\']+)\'>(.*?)</div>'

        def replace_component(match: "re.Match[str]") -> str:
            component_name = match.group(1)
            # The renderer entity-escapes the attribute value.
            props_json = html_module.unescape(match.group(2))
            children = match.group(3)

            try:
                props = json_module.loads(props_json)
            except json_module.JSONDecodeError:
                props = {}

            # The template renderer already resolved `{{ var }}` props from
            # the view context. Don't resolve again here: a resolved value is
            # user data, and one that reads `{{ other }}` must stay literal.
            resolved_props = props

            renderer = react_components.get(component_name)

            if renderer:
                rendered_content = renderer(resolved_props, children)
                # Single-quoted attribute: escape `'` too, not only `"`.
                resolved_props_json = html_module.escape(
                    json_module.dumps(resolved_props), quote=True
                )
                return f"<div data-react-component=\"{component_name}\" data-react-props='{resolved_props_json}'>{rendered_content}</div>"
            else:
                return match.group(0)

        html = re.sub(pattern, replace_component, html, flags=re.DOTALL)

        return html

    def _inject_client_script(self, html: str) -> str:
        """Inject the LiveView client JavaScript into the HTML"""
        from ..config import config
        from django.conf import settings

        use_websocket = config.get("use_websocket", True)
        debug_vdom = config.get("debug_vdom", False)
        ws_compression = config.get("websocket_compression", True)
        loading_grouping_classes = config.get(
            "loading_grouping_classes",
            ["d-flex", "btn-group", "input-group", "form-group", "btn-toolbar"],
        )

        loading_classes_js = json.dumps(loading_grouping_classes)

        debug_info_script = ""
        debug_css_link = ""
        if settings.DEBUG:
            from ..security import escape_json_for_script

            debug_info = self.get_debug_info()
            # get_debug_info() includes repr()s of user-controlled public view
            # attributes; json.dumps does NOT escape </script>, so a value
            # containing "</script><script>…" would break out of this inline
            # block (finding #8, CWE-79). escape_json_for_script() neutralizes
            # <, >, & (and U+2028/2029).
            debug_info_json = escape_json_for_script(json.dumps(debug_info))
            debug_info_script = f"""
            <script data-turbo-track="reload">
                window.DJUST_DEBUG_INFO = {debug_info_json};
            </script>
            """
            debug_css_link = '<link rel="stylesheet" href="/static/djust/debug-panel.css" data-turbo-track="reload">'

        config_script = f"""
        <script data-turbo-track="reload">
            // djust configuration
            window.DJUST_USE_WEBSOCKET = {str(use_websocket).lower()};
            window.DJUST_DEBUG_VDOM = {str(debug_vdom).lower()};
            window.DJUST_WS_COMPRESSION = {str(ws_compression).lower()};
            window.DJUST_LOADING_GROUPING_CLASSES = {loading_classes_js};
            // Enable debug logging for client-dev.js (development only)
            window.djustDebug = {str(settings.DEBUG).lower()};
            window.DEBUG_MODE = {str(settings.DEBUG).lower()};
        </script>
        {debug_info_script}
        """

        from django.templatetags.static import static

        # v0.6.0 P1: serve the pre-minified client.min.js in production.
        # DEBUG=True continues to serve the readable client.js so stack
        # traces point at meaningful line numbers and contributors can
        # poke at source directly. An explicit override
        # ``DJUST_CLIENT_JS_MINIFIED`` in settings (bool) takes precedence
        # over the DEBUG heuristic if an operator wants to test the
        # minified file locally.
        client_js_name = "djust/client.js"
        use_min = getattr(settings, "DJUST_CLIENT_JS_MINIFIED", not settings.DEBUG)
        if use_min:
            client_js_name = "djust/client.min.js"
        try:
            client_js_url = static(client_js_name)
        except (ValueError, AttributeError):
            client_js_url = f"/static/{client_js_name}"

        script = f'<script src="{client_js_url}" defer data-turbo-track="reload"></script>'

        # Official adapters (ADR-025 milestone C). Opt-in via
        # DJUST_CONFIG['extensions']; nothing is emitted when unused.
        # These MUST come after client.js: they register into
        # window.djust.hooks / window.djust.commands, which client.js
        # creates. `defer` scripts execute in document order, so appending
        # here is what guarantees the ordering.
        from ..extensions import get_extension_static_paths

        for ext_path in get_extension_static_paths():
            try:
                ext_url = static(ext_path)
            except (ValueError, AttributeError):
                ext_url = f"/static/{ext_path}"
            script += f'\n        <script src="{ext_url}" defer data-turbo-track="reload"></script>'

        if settings.DEBUG:
            # debug-panel.js MUST load before client-dev.js so that
            # DjustDebugPanel is defined when client-dev.js calls
            # initDebugPanel() (fixes #193 and #196).
            try:
                debug_panel_js_url = static("djust/debug-panel.js")
            except (ValueError, AttributeError):
                debug_panel_js_url = "/static/djust/debug-panel.js"
            script += f'\n        <script src="{debug_panel_js_url}" defer data-turbo-track="reload"></script>'

            try:
                client_dev_js_url = static("djust/client-dev.js")
            except (ValueError, AttributeError):
                client_dev_js_url = "/static/djust/client-dev.js"
            script += f'\n        <script src="{client_dev_js_url}" defer data-turbo-track="reload"></script>'

        full_script = config_script + script
        # ADR-036: public owner contracts of the initial page (set by get();
        # escaped JSON, outside dj-root). A data block, not executable script.
        initial_contracts = self.__dict__.pop("_initial_parameter_contracts", None)
        if initial_contracts:
            full_script = (
                '<script type="application/json" data-djust-parameter-contracts>'
                f"{initial_contracts}</script>" + full_script
            )

        # The HTTP event fallback and djust.call need the CSRF token even when
        # the project renames the cookie (CSRF_COOKIE_NAME) or keeps it out of
        # JavaScript's reach (CSRF_COOKIE_HTTPONLY, CSRF_USE_SESSIONS). The
        # client reads these via window.djust.csrfToken() (00-namespace.js).
        csrf_meta = ""
        request = getattr(self, "request", None)
        if request is not None:
            from django.middleware.csrf import get_token
            from django.utils.html import escape

            csrf_meta = (
                f'<meta name="djust-csrf-cookie" content="{escape(settings.CSRF_COOKIE_NAME)}">'
                f'<meta name="djust-csrf-token" content="{escape(get_token(request))}">'
            )
        # Both go into the document's real <head>, once (#2987).
        masked = _mask_document_text(html)
        head_close = _find_head_close(masked)
        head_inject = ""
        if csrf_meta:
            if head_close >= 0:
                head_inject += csrf_meta
            else:
                full_script = csrf_meta + full_script
        if debug_css_link and head_close >= 0:
            head_inject += debug_css_link
        if head_inject:
            html = html[:head_close] + head_inject + html[head_close:]

        # Found before the head insertion, so shift it past the inserted text.
        body_close = _find_body_close(masked)
        if body_close >= 0 and head_inject and body_close >= head_close:
            body_close += len(head_inject)
        if body_close >= 0:
            html = html[:body_close] + full_script + html[body_close:]
        else:
            html += full_script

        return html
