"""
PostProcessingMixin - Debug info, React hydration, and client script injection for LiveView.
"""

import json
import logging
import re
import sys
from typing import Any, Dict

logger = logging.getLogger(__name__)


class PostProcessingMixin:
    """Post-processing: get_debug_info, _hydrate_react_components, _inject_client_script."""

    def get_debug_info(self) -> Dict[str, Any]:
        """
        Get debug information about this LiveView instance.

        Returns:
            Dict with debug information
        """
        from ..validation import get_handler_signature_info
        from ..decorators import is_event_handler

        handlers = {}
        variables = {}

        # Match the runtime event_security policy: only @event_handler-decorated
        # methods are callable.

        for name in dir(self):
            if name.startswith("_"):
                continue

            try:
                attr = getattr(self, name)
            except AttributeError:
                continue

            if callable(attr) and hasattr(attr, "__func__"):
                # Show only handlers that would pass _check_event_security at runtime
                if is_event_handler(attr):
                    sig_info = get_handler_signature_info(attr)

                    handlers[name] = {
                        "name": name,
                        "params": sig_info["params"],
                        "description": sig_info["description"],
                        "accepts_kwargs": sig_info["accepts_kwargs"],
                        "decorators": getattr(attr, "_djust_decorators", {}),
                    }

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
            "template": self.template_name if hasattr(self, "template_name") else None,
            "config": {"maxHistory": max_history},
        }

    def get_debug_update(self) -> Dict[str, Any]:
        """
        Get a slim debug payload for event responses (skip static handler metadata).

        Unlike get_debug_info() which includes handler signatures (~20KB+),
        this returns only the parts that change per event: variables and view class.
        Handlers are static and only sent on initial mount via get_debug_info().
        """
        variables = {}

        for name in dir(self):
            if name.startswith("_"):
                continue

            try:
                attr = getattr(self, name)
            except AttributeError:
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
        }

    def _hydrate_react_components(self, html: str) -> str:
        """
        Post-process HTML to hydrate React component placeholders.
        """
        from ..react import react_components
        import json as json_module

        pattern = r'<div data-react-component="([^"]+)" data-react-props=\'([^\']+)\'>(.*?)</div>'

        def replace_component(match):
            component_name = match.group(1)
            props_json = match.group(2)
            children = match.group(3)

            try:
                props = json_module.loads(props_json)
            except json_module.JSONDecodeError:
                props = {}

            context = self.get_context_data()
            resolved_props = {}
            for key, value in props.items():
                if isinstance(value, str) and "{{" in value and "}}" in value:
                    var_match = re.search(r"\{\{\s*(\w+)\s*\}\}", value)
                    if var_match:
                        var_name = var_match.group(1)
                        if var_name in context:
                            resolved_props[key] = context[var_name]
                        else:
                            resolved_props[key] = value
                    else:
                        resolved_props[key] = value
                else:
                    resolved_props[key] = value

            renderer = react_components.get(component_name)

            if renderer:
                rendered_content = renderer(resolved_props, children)
                resolved_props_json = json_module.dumps(resolved_props).replace('"', "&quot;")
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
        loading_grouping_classes = config.get(
            "loading_grouping_classes",
            ["d-flex", "btn-group", "input-group", "form-group", "btn-toolbar"],
        )

        loading_classes_js = json.dumps(loading_grouping_classes)

        # Graceful fallback: Auto-inject Tailwind CDN in development if compiled CSS is missing
        tailwind_cdn_fallback = ""
        if settings.DEBUG and self._should_inject_tailwind_cdn():
            tailwind_cdn_fallback = (
                '\n        <script src="https://cdn.tailwindcss.com"></script>\n'
                "        <!-- djust: Tailwind CDN injected (development fallback) -->\n"
                '        <!-- Run "python manage.py djust_setup_css tailwind" to compile CSS -->\n'
            )

        debug_info_script = ""
        debug_css_link = ""
        if settings.DEBUG:
            debug_info = self.get_debug_info()
            debug_info_script = f"""
            <script data-turbo-track="reload">
                window.DJUST_DEBUG_INFO = {json.dumps(debug_info)};
            </script>
            """
            debug_css_link = '<link rel="stylesheet" href="/static/djust/debug-panel.css" data-turbo-track="reload">'

        config_script = f"""
        <script data-turbo-track="reload">
            // djust configuration
            window.DJUST_USE_WEBSOCKET = {str(use_websocket).lower()};
            window.DJUST_DEBUG_VDOM = {str(debug_vdom).lower()};
            window.DJUST_LOADING_GROUPING_CLASSES = {loading_classes_js};
            // Enable debug logging for client-dev.js (development only)
            window.djustDebug = {str(settings.DEBUG).lower()};
        </script>
        {debug_info_script}
        """

        from django.templatetags.static import static

        try:
            client_js_url = static("djust/client.js")
        except (ValueError, AttributeError):
            client_js_url = "/static/djust/client.js"

        script = f'<script src="{client_js_url}" defer data-turbo-track="reload"></script>'

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

        # Inject Tailwind CDN fallback in <head> if needed (dev mode only)
        if tailwind_cdn_fallback and "</head>" in html:
            html = html.replace("</head>", f"{tailwind_cdn_fallback}</head>")

        if debug_css_link and "</head>" in html:
            html = html.replace("</head>", f"{debug_css_link}</head>")

        if "</body>" in html:
            html = html.replace("</body>", f"{full_script}</body>")
        else:
            html += full_script

        return html

    def _should_inject_tailwind_cdn(self) -> bool:
        """Check if Tailwind CDN should be auto-injected as fallback."""
        import os
        from django.conf import settings

        # Only in DEBUG mode
        if not settings.DEBUG:
            return False

        # Check if Tailwind is configured
        has_tailwind_config = os.path.exists("tailwind.config.js")

        # Check for input.css in STATICFILES_DIRS
        has_input_css = False
        static_dirs = getattr(settings, "STATICFILES_DIRS", [])
        for static_dir in static_dirs:
            input_path = os.path.join(static_dir, "css", "input.css")
            if os.path.exists(input_path):
                try:
                    with open(input_path, "r") as f:
                        content = f.read()
                        if "@import" in content and "tailwind" in content.lower():
                            has_input_css = True
                            break
                except Exception:
                    pass

        if not (has_tailwind_config or has_input_css):
            return False

        # Check if output.css exists
        for static_dir in static_dirs:
            if os.path.exists(os.path.join(static_dir, "css", "output.css")):
                return False  # Compiled CSS exists, no fallback needed

        # Tailwind configured but output.css missing → inject CDN
        logger.info(
            "[djust] Tailwind CSS configured but output.css not found. "
            "Using CDN as fallback in development. "
            "Run 'python manage.py djust_setup_css tailwind' to compile CSS."
        )
        return True
