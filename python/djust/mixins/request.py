"""
RequestMixin - HTTP GET/POST request handling for LiveView.
"""

import json
import logging
import sys
import time

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.db import models

from ..serialization import DjangoJSONEncoder
from ..validation import validate_handler_params
from ..security import safe_setattr

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

            wrapper = loader.get_template(self.wrapper_template)
            html = wrapper.render({"liveview_content": liveview_content}, request)
            html = html.replace("<div data-djust-root></div>", liveview_content)
        else:
            html = liveview_content

        t_total = (time.perf_counter() - t_start) * 1000
        print("\n[LIVEVIEW GET TIMING]", file=sys.stderr)
        print(f"  mount(): {t_mount:.2f}ms", file=sys.stderr)
        print(f"  assign_component_ids(): {t_assign:.2f}ms", file=sys.stderr)
        print(f"  get_context_data(): {t_get_context:.2f}ms", file=sys.stderr)
        print(f"  JSON serialize/deserialize: {t_json:.2f}ms", file=sys.stderr)
        print(f"  save_components_to_session(): {t_save_components:.2f}ms", file=sys.stderr)
        print(f"  initialize_rust_view(): {t_init_rust:.2f}ms", file=sys.stderr)
        print(f"  sync_state_to_rust(): {t_sync:.2f}ms", file=sys.stderr)
        print(f"  get_template(): {t_get_template:.2f}ms", file=sys.stderr)
        print(f"  render_with_diff(): {t_render_diff:.2f}ms", file=sys.stderr)
        print(f"  render_full_template(): {t_render_full:.2f}ms", file=sys.stderr)
        print(f"  TOTAL get(): {t_total:.2f}ms\n", file=sys.stderr)

        # Debug: Save the rendered HTML to a file for inspection
        if "registration" in request.path:
            form_start = html.find("<form")
            if form_start != -1:
                form_end = html.find("</form>", form_start) + 7
                form_html = html[form_start:form_end]
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", delete=False, suffix=".html", prefix="registration_form_"
                ) as f:
                    f.write(form_html)
                    print(f"[LiveView] Saved form HTML to {f.name}", file=sys.stderr)

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
            event_name = data.get("event")
            params = data.get("params", {})

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

            # Call the event handler
            handler = getattr(self, event_name, None)
            if handler and callable(handler):
                coerce = True
                if hasattr(handler, "_djust_decorators"):
                    event_meta = handler._djust_decorators.get("event_handler", {})
                    coerce = event_meta.get("coerce_types", True)

                validation = validate_handler_params(handler, params, event_name, coerce=coerce)
                if not validation["valid"]:
                    logger.error(f"Parameter validation failed: {validation['error']}")
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
