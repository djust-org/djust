"""
RequestMixin - HTTP GET/POST request handling for LiveView.
"""

import asyncio
import json
import logging
import time
from contextlib import contextmanager

from django.http import HttpResponse, HttpResponseRedirect, JsonResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.db import models

from ..serialization import normalize_django_value
from ..utils import is_model_list
from ..validation import validate_handler_params
from ..security import safe_setattr
from ..security.event_guard import is_safe_event_name
from ..decorators import is_event_handler
from ..hooks import run_on_mount_hooks

logger = logging.getLogger(__name__)


class RequestMixin:
    """HTTP handling: get, post."""

    @contextmanager
    def _processor_context(self, request):
        """Temporarily inject context processor output as instance attributes.

        Used by the POST (HTTP fallback) path to ensure auth context (user,
        perms, messages) is available during template rendering. Cleanup is
        guaranteed via the context manager pattern. (#717)
        """
        processor_output = self._apply_context_processors({}, request)
        injected_keys = []
        for key, value in processor_output.items():
            if not hasattr(self, key):
                injected_keys.append(key)
                setattr(self, key, value)
        try:
            yield processor_output
        finally:
            for key in injected_keys:
                try:
                    delattr(self, key)
                except AttributeError:
                    pass

    @method_decorator(ensure_csrf_cookie)
    def get(self, request, *args, **kwargs):
        """Handle GET requests - initial page load.

        On WSGI deployments, or when ``streaming_render = False``, this is
        the only path. ASGI deployments with ``streaming_render = True``
        route through :meth:`aget` (PR-A foundation, ADR-015).
        """
        t_start = time.perf_counter()
        # PR-B (ADR-015): defensive reset so a re-used view instance
        # doesn't see lazy thunks from a prior request stashed by the
        # template tag during sync render.
        self._lazy_thunks = []

        # Check login_required / permission_required (matches WebSocket path).
        # Without this, views with login_required=True render their full HTML
        # to unauthenticated users on the initial HTTP GET.
        from ..auth import check_view_auth

        redirect_url = check_view_auth(self, request)
        if redirect_url:
            from django.utils.http import urlencode

            next_url = f"{redirect_url}?{urlencode({'next': request.get_full_path()})}"
            return HttpResponseRedirect(next_url)

        # Initialize temporary assigns with default values before mount
        self._initialize_temporary_assigns()

        # Run on_mount hooks (auth guards, etc.) before mount
        hook_redirect = run_on_mount_hooks(self, request, **kwargs)
        if hook_redirect:
            # Validate hook-returned URL to prevent open-redirect via
            # a developer-defined hook echoing untrusted request data.
            # Falls back to "/" on any off-site/malicious target.
            if not url_has_allowed_host_and_scheme(
                url=hook_redirect,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                logger.warning(
                    "on_mount hook returned unsafe redirect URL for %s; falling back to '/'",
                    self.__class__.__name__,
                )
                return HttpResponseRedirect("/")
            return HttpResponseRedirect(hook_redirect)

        # IMPORTANT: mount() must be called first to initialize clean state
        t0 = time.perf_counter()
        self.mount(request, **kwargs)
        t_mount = (time.perf_counter() - t0) * 1000

        # Snapshot user-defined _private attrs (set in mount) before render
        # cycle adds framework-internal attrs.
        self._snapshot_user_private_attrs()

        # Call handle_params after mount (Phoenix parity).
        # The WebSocket path does this in websocket.py:1261-1266, but the
        # HTTP GET path was missing it. Without this, URL params like ?tab=X
        # are only read after WebSocket connects, causing a content flash.
        params = {
            k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in request.GET.lists()
        }
        uri = request.get_full_path()
        if hasattr(self, "handle_params"):
            self.handle_params(params, uri)

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
                state[key] = normalize_django_value(value)
            elif is_model_list(value):
                state[key] = normalize_django_value(value)

        state_serializable = state
        t_json = (time.perf_counter() - t0) * 1000

        # Save state to session after GET so the WebSocket mount can restore it
        # instead of re-running mount() (which doubles page load cost).
        # Also used by HTTP-only POST path to restore state before event handling.
        view_key = f"liveview_{request.path}"
        # Use _cached_context (pre-context-processor copy) to avoid
        # non-serializable processor objects (PermWrapper, csrf, etc.)
        _cached = self._cached_context or {}
        _session_state = {k: v for k, v in _cached.items() if not isinstance(v, LiveComponent)}
        request.session[view_key] = normalize_django_value(_session_state)

        # Persist user-defined _private attributes so they survive reconnects
        private_state = self._get_private_state()
        if private_state:
            request.session[f"{view_key}__private"] = normalize_django_value(private_state)

        t0_sc = time.perf_counter()
        self._save_components_to_session(request, _cached)
        t_save_components = (time.perf_counter() - t0_sc) * 1000

        # IMPORTANT: Always call get_template() on GET requests to set _full_template
        t0 = time.perf_counter()
        self.get_template()
        t_get_template = (time.perf_counter() - t0) * 1000

        # Render full template for the browser.
        # render_full_template() now calls _initialize_rust_view() +
        # _sync_state_to_rust() internally, so self._rust_view is ready
        # after this returns.
        t0 = time.perf_counter()
        html = self.render_full_template(request, serialized_context=state_serializable)
        t_render_full = (time.perf_counter() - t0) * 1000
        liveview_content = html

        # Establish VDOM baseline for subsequent PATCH responses.
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
                html = html.replace("<div dj-root></div>", liveview_content)
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
            "get_template=%.2fms render_full=%.2fms "
            "render_diff=%.2fms TOTAL=%.2fms",
            t_mount,
            t_assign,
            t_get_context,
            t_json,
            t_save_components,
            t_get_template,
            t_render_full,
            t_render_diff,
            t_total,
        )

        # Expose timing breakdown for metrics middleware
        request._djust_timing = {
            "mount_ms": round(t_mount, 2),
            "context_ms": round(t_get_context + t_json, 2),
            "render_ms": round(t_render_full, 2),
            "vdom_ms": round(t_render_diff, 2),
        }

        # Inject view path into dj-root for WebSocket mounting
        view_path = f"{self.__class__.__module__}.{self.__class__.__name__}"
        html = html.replace("<div dj-root>", f'<div dj-root dj-view="{view_path}">')

        # Inject LiveView client script
        html = self._inject_client_script(html)

        if getattr(self, "streaming_render", False):
            return self._make_streaming_response(html)
        return HttpResponse(html)

    def _make_streaming_response(self, full_html: str) -> StreamingHttpResponse:
        """Return a chunked ``StreamingHttpResponse`` for the initial GET.

        The iterator yields the page in three chunks so the browser can
        begin parsing ``<head>`` and loading CSS/JS while the remainder of
        the response is still on the wire:

        1. ``shell_open`` — everything before ``<div dj-root>``.
        2. ``main_content`` — the ``<div dj-root>...</div>`` block.
        3. ``shell_close`` — ``</body></html>`` + any trailing markup.

        Templates without a ``<div dj-root>`` (edge case — e.g. a raw
        body fragment) yield a single chunk, equivalent to the
        non-streaming ``HttpResponse`` path.

        The response omits the ``Content-Length`` header (HTTP chunked
        transfer is implicit). Middleware that reads or modifies the
        response body must be streaming-aware; ``X-Djust-Streaming: 1``
        is set as an observability marker.

        :param full_html: Fully-rendered HTML string from :meth:`get`.
        :returns: ``StreamingHttpResponse`` with ``text/html`` content type.
        """
        shell_open, main, shell_close = self._split_for_streaming(full_html)

        def _iter():
            if shell_open:
                yield shell_open
            if main:
                yield main
            if shell_close:
                yield shell_close

        response = StreamingHttpResponse(_iter(), content_type="text/html; charset=utf-8")
        # Explicitly DO NOT set Content-Length — chunked transfer. Middleware
        # that reads/modifies the response body must be streaming-aware.
        response["X-Djust-Streaming"] = "1"
        return response

    def _is_asgi_context(self, request=None) -> bool:
        """Detect whether we are handling a real ASGI request.

        The accurate signal is ``isinstance(request, ASGIRequest)`` —
        Django's WSGI test ``Client`` produces ``WSGIRequest`` even
        when the view is async-callable (``async_to_sync`` wraps the
        coroutine but the request object itself is sync). Checking
        ``asyncio.get_running_loop()`` is fooled by that wrapping,
        which produces false-positive ASGI detection on sync test
        clients.

        Falls back to the loop-based check when ``request`` is not
        provided (callers like ``aget`` always pass it; older callers
        may not).
        """
        if request is not None:
            try:
                from django.core.handlers.asgi import ASGIRequest

                return isinstance(request, ASGIRequest)
            except ImportError:  # pragma: no cover — Django <4.1
                pass
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    async def aget(self, request, *args, **kwargs):
        """Async-streaming GET path. PR-A foundation (ADR-015).

        Parallel to :meth:`get`. Returns a ``StreamingHttpResponse`` whose
        ``streaming_content`` is an async iterator over chunks produced
        by :meth:`TemplateMixin.arender_chunks`. ``await asyncio.sleep(0)``
        between chunks gives the ASGI handler an opportunity to flush
        the shell to the wire before the body chunks are queued.

        Activation rules:

        * ``streaming_render = False`` (default) — never activates;
          callers route to :meth:`get`.
        * WSGI deployment (no running asyncio loop) — falls back to
          :meth:`get` via ``sync_to_async`` so the same Phase-1
          cosmetic chunked response shape is preserved. Documented
          gracefully-degrade contract per ADR-015 risk #10.
        * ASGI deployment with ``streaming_render = True`` —
          shell-then-body chunks via the async iterator.

        ASGI disconnect handling: a background task polls
        ``request.is_disconnected()`` (Django 5.x) and calls
        :meth:`ChunkEmitter.cancel` when the client closes the
        connection. Tasks already kicked off by ``start_async`` chains
        keep running per the explicit ADR cancellation contract.
        """
        from asgiref.sync import sync_to_async

        from ..http_streaming import ChunkEmitter

        # WSGI fallback: sync get() does the right thing already
        # (returns HttpResponse or Phase-1 streaming wrapper).
        if not self._is_asgi_context(request):
            return await sync_to_async(self.get)(request, *args, **kwargs)

        # Run the existing sync GET pipeline to produce the fully-rendered
        # HTML and any redirect / error responses. We do this in a thread
        # via sync_to_async so the per-request mount/render work doesn't
        # block the event loop. The result is one of:
        #   - HttpResponseRedirect (auth or hook redirect) — pass through.
        #   - StreamingHttpResponse (Phase-1 path when streaming_render
        #     is True but we somehow re-enter — defensive).
        #   - HttpResponse (normal render output) — upgrade to async.
        sync_response = await sync_to_async(self.get)(request, *args, **kwargs)

        if isinstance(sync_response, HttpResponseRedirect):
            return sync_response

        # If streaming wasn't requested, deliver the plain HttpResponse
        # untouched. (aget should only be reached when streaming_render
        # is True, but be defensive.)
        if not getattr(self, "streaming_render", False):
            return sync_response

        # Pull the rendered HTML out of the response. For an HttpResponse
        # this is .content; for a Phase-1 StreamingHttpResponse it's the
        # joined sync iterator.
        if isinstance(sync_response, StreamingHttpResponse):
            html_bytes = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in sync_response.streaming_content
            )
        else:
            html_bytes = sync_response.content

        try:
            full_html = html_bytes.decode("utf-8")
        except UnicodeDecodeError:
            full_html = html_bytes.decode("utf-8", errors="replace")

        emitter = ChunkEmitter(request)
        # Stash on the view so PR-B's lazy thunks can find it from the
        # template-tag render path.
        self._chunk_emitter = emitter

        # PR-B (ADR-015): the live_render tag stashes lazy thunks on
        # ``self._lazy_thunks`` during the sync ``get()`` render (the
        # tag has no access to the emitter at render time because aget
        # constructs the emitter AFTER ``sync_to_async(self.get)``
        # returns). Transfer the stash to the emitter so phase-5 of
        # ``arender_chunks`` can invoke them.
        stashed_thunks = getattr(self, "_lazy_thunks", None) or []
        self._lazy_thunks = []  # reset to defend against view-instance re-use
        for view_id, thunk_fn in stashed_thunks:
            emitter.register_thunk(view_id, thunk_fn)

        # Producer task: render chunks into the emitter's queue.
        # ``arender_chunks`` is a regular coroutine that pushes via
        # ``emitter.emit()``; awaiting it once drives the full emit loop.
        async def _produce():
            try:
                await self.arender_chunks(full_html, emitter)
            except Exception:  # pragma: no cover — defensive
                logger.exception("arender_chunks raised; cancelling emitter")
                await emitter.cancel("producer_error")
            finally:
                await emitter.close()

        produce_task = asyncio.ensure_future(_produce())

        # Disconnect watcher: poll request.is_disconnected() (Django 5.x).
        # Older Django versions don't expose it; we degrade silently.
        async def _watch_disconnect():
            is_disconnected = getattr(request, "is_disconnected", None)
            if not callable(is_disconnected):
                return
            try:
                while not produce_task.done():
                    try:
                        if await is_disconnected():
                            await emitter.cancel("client_disconnected")
                            return
                    except Exception:
                        # Disconnect APIs vary between Django/Daphne/Uvicorn.
                        # Log once and stop polling rather than crashing.
                        logger.debug(
                            "is_disconnected() raised; halting watcher",
                            exc_info=True,
                        )
                        return
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                return

        disconnect_task = asyncio.ensure_future(_watch_disconnect())

        async def _streaming_iter():
            try:
                async for chunk in emitter:
                    yield chunk
            finally:
                # Producer should be done by now (it pushed STREAM_END).
                # Cancel the disconnect watcher to release its task.
                if not disconnect_task.done():
                    disconnect_task.cancel()
                # Clear the per-request emitter stash so a future request
                # on a re-used view instance (rare in production but
                # possible in long-lived test harnesses or custom
                # threading) doesn't see a stale ``_chunk_emitter``
                # reference that PR-B would mistake for the current
                # render.
                self._chunk_emitter = None

        response = StreamingHttpResponse(
            _streaming_iter(),
            content_type="text/html; charset=utf-8",
        )
        # Mirror the Phase-1 observability marker so existing tests and
        # ops dashboards still detect streaming responses.
        response["X-Djust-Streaming"] = "1"
        # PR-A marker so callers can tell the async path was used.
        response["X-Djust-Streaming-Phase"] = "2"
        # Copy any cookies/headers the sync_response set (CSRF cookie etc.)
        # so we don't drop them by re-wrapping.
        for header, value in sync_response.items():
            if header.lower() in {"content-length", "content-type"}:
                continue
            response[header] = value
        for cookie in sync_response.cookies.values():
            response.cookies[cookie.key] = cookie

        return response

    def post(self, request, *args, **kwargs):
        """Handle POST requests - event handling"""
        from ..components.base import Component, LiveComponent

        try:
            # Ensure self.request is set for context processors and
            # _sync_state_to_rust csrf_token injection (#705).
            self.request = request
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

            # Restore user-defined _private attributes
            private_state = request.session.get(f"{view_key}__private", {})
            if private_state:
                self._restore_private_state(private_state)

            self._initialize_temporary_assigns()

            # Run on_mount hooks (auth guards, etc.) before mount
            hook_redirect = run_on_mount_hooks(self, request, **kwargs)
            if hook_redirect:
                return JsonResponse({"redirect": hook_redirect}, status=403)

            if not saved_state:
                self.mount(request, **kwargs)
                self._snapshot_user_private_attrs()
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
            t_handler_ms = 0.0
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
                t0_handler = time.perf_counter()
                if coerced_params:
                    handler(**coerced_params)
                else:
                    handler()
                t_handler_ms = (time.perf_counter() - t0_handler) * 1000

            # Persist user-defined _private attributes BEFORE get_context_data()
            # because get_context_data() sets render-cycle internals that we
            # don't want to accidentally capture.
            private_state = self._get_private_state()
            if private_state:
                request.session[f"{view_key}__private"] = normalize_django_value(private_state)
            else:
                # Clean up if no private attrs remain
                request.session.pop(f"{view_key}__private", None)

            # Save updated state back to session
            updated_context = self.get_context_data()
            state = {k: v for k, v in updated_context.items() if not isinstance(v, LiveComponent)}
            state_serializable = normalize_django_value(state)
            request.session[view_key] = state_serializable

            self._save_components_to_session(request, updated_context)

            # Apply context processors so the render includes auth context
            # (user, perms, messages, etc.). Without this, template conditionals
            # like {% if user.is_authenticated %} evaluate to false and the HTTP
            # fallback returns logged-out HTML. Fixes #705.
            # Unified via _processor_context context manager (#717).
            with self._processor_context(request):
                t0_render = time.perf_counter()
                html, patches_json, version = self.render_with_diff(request)
                t_render_ms = (time.perf_counter() - t0_render) * 1000

            import json as json_module

            PATCH_THRESHOLD = 100

            cache_request_id = params.get("_cacheRequestId")

            # Inject debug info for the debug panel (HTTP-only mode)
            from django.conf import settings as _settings

            def _inject_debug(resp_data):
                if _settings.DEBUG:
                    try:
                        debug_info = self.get_debug_update()
                        debug_info["_eventName"] = event_name
                        debug_info["performance"] = {
                            "handler_ms": round(t_handler_ms, 2),
                            "render_ms": round(t_render_ms, 2),
                        }
                        resp_data["_debug"] = debug_info
                    except Exception:
                        logger.debug("Failed to inject debug info", exc_info=True)

            # Drain side-channel commands (flash, page metadata) so they
            # are delivered in the HTTP response, not only via WebSocket.
            def _inject_side_channels(resp_data):
                if hasattr(self, "_drain_flash"):
                    flash_commands = self._drain_flash()
                    if flash_commands:
                        resp_data["_flash"] = flash_commands
                if hasattr(self, "_drain_page_metadata"):
                    meta_commands = self._drain_page_metadata()
                    if meta_commands:
                        resp_data["_page_metadata"] = meta_commands

            if patches_json:
                patches = json_module.loads(patches_json)
                patch_count = len(patches)

                if patch_count > 0 and patch_count <= PATCH_THRESHOLD:
                    response_data = {"patches": patches, "version": version}
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    _inject_side_channels(response_data)
                    _inject_debug(response_data)
                    return JsonResponse(response_data)
                else:
                    self._rust_view.reset()
                    response_data = {"html": html, "version": version}
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    _inject_side_channels(response_data)
                    _inject_debug(response_data)
                    return JsonResponse(response_data)
            else:
                response_data = {"html": html, "version": version}
                if cache_request_id:
                    response_data["cache_request_id"] = cache_request_id
                _inject_side_channels(response_data)
                _inject_debug(response_data)
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
                return JsonResponse(error_details, status=500)  # nosec B105 -- only returned when DEBUG=True
            else:
                return JsonResponse(
                    {
                        "error": "An error occurred processing your request. Please try again.",
                        "debug_hint": "Check server logs for details",
                    },
                    status=500,
                )
