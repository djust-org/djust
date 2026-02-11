"""
RequestMixin - HTTP GET/POST request handling for LiveView.
"""

import json
import logging
import time

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.db import models

from ..serialization import DjangoJSONEncoder
from ..validation import validate_handler_params
from ..security import safe_setattr
from ..security.event_guard import is_safe_event_name
from ..decorators import is_event_handler

logger = logging.getLogger(__name__)


class RequestMixin:
    """HTTP handling: get, post."""

    @method_decorator(ensure_csrf_cookie)
    def get(self, request, *args, **kwargs):
        """Handle GET requests - initial page load"""
        t_start = time.perf_counter()

        # Initialize temporary assigns with default values before mount
        self._initialize_temporary_assigns()

        # IMPORTANT: mount() must be called first to initialize clean state
        t0 = time.perf_counter()
        self.mount(request, **kwargs)
        t_mount = (time.perf_counter() - t0) * 1000

        # Automatically assign deterministic IDs to components based on variable names
        t0 = time.perf_counter()
        self._assign_component_ids()
        t_assign = (time.perf_counter() - t0) * 1000

        # Ensure session exists
        if not request.session.session_key:
            request.session.create()

        # Get context for rendering and cache it so _sync_state_to_rust()
        # and render_with_diff() don't re-evaluate QuerySets.
        # Note: cached BEFORE _apply_context_processors, so downstream callers
        # of get_context_data() won't see processor-added keys (csrf_token,
        # messages, etc.). This is intentional — those callers only need
        # serialized view state, not request-scoped processor context.
        t0 = time.perf_counter()
        context = self.get_context_data()
        self._cached_context = dict(context)
        context = self._apply_context_processors(context, request)
        t_get_context = (time.perf_counter() - t0) * 1000

        # Serialize state for rendering (but don't store in session)
        from ..components.base import LiveComponent

        state = {k: v for k, v in context.items() if not isinstance(v, LiveComponent)}

        t0 = time.perf_counter()
        for key, value in list(state.items()):
            if isinstance(value, models.Model):
                state[key] = json.loads(json.dumps(value, cls=DjangoJSONEncoder))
            elif isinstance(value, list) and value and isinstance(value[0], models.Model):
                state[key] = json.loads(json.dumps(value, cls=DjangoJSONEncoder))

        state_serializable = state
        t_json = (time.perf_counter() - t0) * 1000

        t_save_components = 0.0

        # Save state to session on GET when WebSocket is disabled (HTTP-only mode).
        # Without this, the first POST finds no saved state and re-mounts,
        # which is wasteful when mount() is expensive (e.g., scanning dirs, API calls).
        from ..config import config as _lv_config

        if not _lv_config.get("use_websocket", True):
            view_key = f"liveview_{request.path}"
            # Use _cached_context (pre-context-processor copy) to avoid
            # non-serializable processor objects (PermWrapper, csrf, etc.)
            _cached = self._cached_context or {}
            _session_state = {k: v for k, v in _cached.items() if not isinstance(v, LiveComponent)}
            _session_json = json.dumps(_session_state, cls=DjangoJSONEncoder)
            request.session[view_key] = json.loads(_session_json)
            t0_sc = time.perf_counter()
            self._save_components_to_session(request, _cached)
            t_save_components = (time.perf_counter() - t0_sc) * 1000

        # IMPORTANT: Always call get_template() on GET requests to set _full_template
        t0 = time.perf_counter()
        self.get_template()
        t_get_template = (time.perf_counter() - t0) * 1000

        # Render full template for the browser
        t0 = time.perf_counter()
        html = self.render_full_template(request, serialized_context=state_serializable)
        t_render_full = (time.perf_counter() - t0) * 1000
        liveview_content = html

        # CRITICAL: Establish VDOM baseline for subsequent PATCH responses
        t0 = time.perf_counter()
        self._initialize_rust_view(request)
        t_init_rust = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        self._sync_state_to_rust()
        t_sync = (time.perf_counter() - t0) * 1000

        # Establish VDOM baseline
        t0 = time.perf_counter()
        _, _, _ = self.render_with_diff(request)
        t_render_diff = (time.perf_counter() - t0) * 1000

        # Clear context cache so WebSocket events get fresh data
        self._cached_context = None

        # Wrap in Django template if wrapper_template is specified
        if hasattr(self, "wrapper_template") and self.wrapper_template:
            from django.template import loader

            try:
                wrapper = loader.get_template(self.wrapper_template)
                html = wrapper.render({"liveview_content": liveview_content}, request)
                html = html.replace("<div data-djust-root></div>", liveview_content)
            except Exception as e:
                logger.error(
                    "Failed to render wrapper_template '%s': %s",
                    self.wrapper_template,
                    e,
                )
                html = liveview_content
        else:
            html = liveview_content

        t_total = (time.perf_counter() - t_start) * 1000
        logger.debug(
            "[LIVEVIEW GET TIMING] mount=%.2fms assign_ids=%.2fms "
            "get_context=%.2fms json=%.2fms save_components=%.2fms "
            "init_rust=%.2fms sync_state=%.2fms get_template=%.2fms "
            "render_diff=%.2fms render_full=%.2fms TOTAL=%.2fms",
            t_mount,
            t_assign,
            t_get_context,
            t_json,
            t_save_components,
            t_init_rust,
            t_sync,
            t_get_template,
            t_render_diff,
            t_render_full,
            t_total,
        )

        # Expose timing breakdown for metrics middleware
        request._djust_timing = {
            "mount_ms": round(t_mount, 2),
            "context_ms": round(t_get_context + t_json, 2),
            "render_ms": round(t_render_full, 2),
            "vdom_ms": round(t_init_rust + t_sync + t_render_diff, 2),
        }

        # Inject view path into data-djust-root for WebSocket mounting
        view_path = f"{self.__class__.__module__}.{self.__class__.__name__}"
        html = html.replace(
            "<div data-djust-root>", f'<div data-djust-root data-djust-view="{view_path}">'
        )

        # Inject LiveView client script
        html = self._inject_client_script(html)

        return HttpResponse(html)

    def post(self, request, *args, **kwargs):
        """Handle POST requests - event handling"""
        from ..components.base import Component, LiveComponent

        try:
            data = json.loads(request.body)
            # Support both formats:
            # 1. Standard: {"event": "name", "params": {...}}
            # 2. HTTP fallback: X-Djust-Event header + flat params in body
            if "event" in data:
                # Standard format — event name in body, params nested
                event_name = data["event"]
                params = data.get("params", {})
            else:
                # HTTP fallback — event name in header, params flat in body
                event_name = request.headers.get("X-Djust-Event", "")
                params = {k: v for k, v in data.items() if not k.startswith("_")}
                # Preserve _cacheRequestId for @cache decorator support
                if "_cacheRequestId" in data:
                    params["_cacheRequestId"] = data["_cacheRequestId"]

            if not event_name:
                logger.warning("HTTP fallback POST with no event name from %s", request.path)
                return JsonResponse({"error": "No event name provided"}, status=400)

            # Security: validate event name format (blocks dunders, private methods)
            if not is_safe_event_name(event_name):
                return JsonResponse({"error": "Invalid event name"}, status=400)

            # Restore state from session
            view_key = f"liveview_{request.path}"
            saved_state = request.session.get(view_key, {})

            for key, value in saved_state.items():
                if not key.startswith("_") and not callable(value):
                    safe_setattr(self, key, value, allow_private=False)

            self._initialize_temporary_assigns()

            if not saved_state:
                self.mount(request, **kwargs)
            else:
                pass

            self._assign_component_ids()

            # Restore component state
            component_state = request.session.get(f"{view_key}_components", {})
            for key, state in component_state.items():
                component = getattr(self, key, None)
                if component and isinstance(component, (Component, LiveComponent)):
                    self._restore_component_state(component, state)

            # Call the event handler — only @event_handler-decorated methods
            # can be invoked via POST (matches WS security)
            handler = getattr(self, event_name, None)
            if handler and callable(handler):
                if not is_event_handler(handler):
                    logger.warning(
                        "HTTP POST blocked undecorated handler '%s' on %s",
                        event_name,
                        type(self).__name__,
                    )
                    return JsonResponse(
                        {"error": "Event handler not found"},
                        status=400,
                    )
                coerce = True
                if hasattr(handler, "_djust_decorators"):
                    event_meta = handler._djust_decorators.get("event_handler", {})
                    coerce = event_meta.get("coerce_types", True)

                validation = validate_handler_params(handler, params, event_name, coerce=coerce)
                if not validation["valid"]:
                    logger.error("Parameter validation failed: %s", validation["error"])
                    return JsonResponse(
                        {
                            "type": "error",
                            "error": validation["error"],
                            "validation_details": {
                                "expected_params": validation["expected"],
                                "provided_params": validation["provided"],
                                "type_errors": validation["type_errors"],
                            },
                        },
                        status=400,
                    )

                coerced_params = validation.get("coerced_params", params)
                if coerced_params:
                    handler(**coerced_params)
                else:
                    handler()

            # Save updated state back to session
            updated_context = self.get_context_data()
            state = {k: v for k, v in updated_context.items() if not isinstance(v, LiveComponent)}
            state_json = json.dumps(state, cls=DjangoJSONEncoder)
            state_serializable = json.loads(state_json)
            request.session[view_key] = state_serializable

            self._save_components_to_session(request, updated_context)

            # Render with diff to get patches
            html, patches_json, version = self.render_with_diff(request)

            import json as json_module

            PATCH_THRESHOLD = 100

            cache_request_id = params.get("_cacheRequestId")

            if patches_json:
                patches = json_module.loads(patches_json)
                patch_count = len(patches)

                if patch_count > 0 and patch_count <= PATCH_THRESHOLD:
                    response_data = {"patches": patches, "version": version}
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    return JsonResponse(response_data)
                else:
                    self._rust_view.reset()
                    response_data = {"html": html, "version": version}
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    return JsonResponse(response_data)
            else:
                response_data = {"html": html, "version": version}
                if cache_request_id:
                    response_data["cache_request_id"] = cache_request_id
                return JsonResponse(response_data)

        except Exception as e:
            import traceback
            from django.conf import settings

            error_msg = f"Error in {self.__class__.__name__}"
            if event_name:
                error_msg += f".{event_name}()"
            error_msg += f": {type(e).__name__}: {str(e)}"

            logger.error(error_msg, exc_info=True)

            if settings.DEBUG:
                error_details = {
                    "error": error_msg,
                    "type": type(e).__name__,
                    "traceback": traceback.format_exc(),
                    "event": event_name,
                    "params": params,
                }
                return JsonResponse(error_details, status=500)
            else:
                return JsonResponse(
                    {
                        "error": "An error occurred processing your request. Please try again.",
                        "debug_hint": "Check server logs for details",
                    },
                    status=500,
                )
