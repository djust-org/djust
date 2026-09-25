"""
RequestMixin - HTTP GET/POST request handling for LiveView.
"""

import asyncio
import inspect
import json
import logging
import time
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Dict,
    Iterator,
    List,
    Optional,
    Tuple,
)

from django.core.exceptions import PermissionDenied
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseRedirect,
    JsonResponse,
    StreamingHttpResponse,
)
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.db import models

from ..serialization import decode_state_roundtrip, normalize_django_value
from ..utils import is_model_list
from ..validation import (
    validate_handler_params,
    validated_call_arguments,
    get_handler_parameter_policy,
)
from ..security import safe_setattr
from ..security.event_guard import is_safe_event_name
from ..decorators import is_event_handler
from ..hooks import run_on_mount_hooks
from .._exposure import uses_legacy_exposure

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

#: Sent by the client when its HTTP scope holds strict contracts, so a response
#: whose tree no longer has any carries an explicit clear (the stateless HTTP
#: counterpart of the socket runtime's ``_parameter_contracts_active``).
PARAMETER_CONTRACTS_HEADER = "X-Djust-Parameter-Contracts"


def _initial_parameter_contracts(view: Any, view_path: str) -> Optional[str]:
    """Escaped JSON for the initial page's contract element, or None if legacy.

    ``false`` marks discovery failure: the client installs an invalid scope and
    strict lookups fail closed instead of guessing a legacy contract.
    """
    from .._parameter_metadata import parameter_contract_manifest
    from ..security import escape_json_for_script

    try:
        manifest = parameter_contract_manifest(view)
    except Exception:  # noqa: BLE001 — declaration errors must not break the page
        logger.warning("Initial parameter contracts unavailable")
        return escape_json_for_script(json.dumps({"view": view_path, "contracts": False}))
    if manifest is None:
        return None
    return escape_json_for_script(json.dumps({"view": view_path, "contracts": manifest}))


def _http_parameter_contract_fields(view: Any, request: Any) -> Optional[Dict[str, Any]]:
    """Contract fields for an HTTP render response (``{}`` for a legacy tree).

    None means discovery failed: the caller withholds the DOM update.
    """
    from .._parameter_metadata import parameter_contract_manifest

    try:
        manifest = parameter_contract_manifest(view)
    except Exception:  # noqa: BLE001 — never send a DOM update with invalid contracts
        logger.warning("Render parameter contracts unavailable")
        return None
    if manifest is None and request.headers.get(PARAMETER_CONTRACTS_HEADER) != "1":
        return {}
    view_path = f"{view.__class__.__module__}.{view.__class__.__name__}"
    return {"parameter_contracts": manifest, "parameter_contract_view": view_path}


def _contract_error_response() -> JsonResponse:
    return JsonResponse(
        {"type": "error", "error": "Render parameter contracts unavailable."}, status=500
    )


class RequestMixin:
    """HTTP handling: get, post."""

    if TYPE_CHECKING:
        # Cooperating attributes/methods supplied by the host class (LiveView)
        # and sibling mixins. Declared type-only so the strict-island mypy run
        # resolves them on this mixin without any runtime change — the real
        # definitions live on LiveView / the other mixins (this mixin is never
        # instantiated standalone). See streaming.py for the same pattern.
        request: Any
        _rust_view: Any
        _lazy_thunks: List[Any]
        _chunk_emitter: Any
        wrapper_template: Optional[str]
        _cached_context: Optional[Dict[str, Any]]
        _child_views: Dict[str, Any]

        def _apply_context_processors(
            self, context: Dict[str, Any], request: Any
        ) -> Dict[str, Any]: ...

        def get_context_data(self, **kwargs: Any) -> Dict[str, Any]: ...

        def _split_for_streaming(self, full_html: str) -> Tuple[str, str, str]: ...

        async def arender_chunks(self, full_html: str, emitter: Any) -> None: ...

        def get_debug_update(self) -> Dict[str, Any]: ...

        def _drain_flash(self) -> List[Dict[str, str]]: ...

        def _drain_page_metadata(self) -> List[Dict[str, str]]: ...

        def render_with_diff(self, *args: Any, **kwargs: Any) -> Any: ...

        def render_full_template(self, *args: Any, **kwargs: Any) -> str: ...

        def get_template(self) -> str: ...

        @staticmethod
        def _stamp_dj_view(html: str, view_path: str) -> str: ...

        def handle_params(self, params: Dict[str, Any], uri: str) -> None: ...

        def mount(self, request: Any, **kwargs: Any) -> None: ...

        def _initialize_temporary_assigns(self) -> None: ...

        def _get_private_state(self) -> Dict[str, Any]: ...

        def _restore_private_state(self, private_state: Dict[str, Any]) -> None: ...

        def _snapshot_user_private_attrs(self) -> None: ...

        def _inject_client_script(self, html: str) -> str: ...

        def _get_all_child_views(self) -> Dict[str, Any]: ...

        def _assign_component_ids(self) -> None: ...

        def _restore_component_state(self, component: Any, state: Dict[str, Any]) -> None: ...

        def _save_components_to_session(self, request: Any, context: Dict[str, Any]) -> None: ...

    @contextmanager
    def _processor_context(self, request: "HttpRequest") -> Iterator[Dict[str, Any]]:
        """Temporarily inject context processor output as instance attributes.

        Used by the POST (HTTP fallback) path to ensure auth context (user,
        perms, messages) is available during template rendering. Cleanup is
        guaranteed via the context manager pattern. (#717)
        """
        from .._exposure_providers import PROCESSOR_KEYS_ATTR

        processor_output = self._apply_context_processors({}, request)
        injected_keys = []
        for key, value in processor_output.items():
            if not hasattr(self, key):
                injected_keys.append(key)
                setattr(self, key, value)
        # These attributes are framework-injected, not view state: ADR-038
        # D-h reads the marker so they aren't instance-assigned components,
        # and the render's request-scoped key set (``_request_scoped_keys``)
        # reads it so they stay out of the state normalizer and the
        # change-detection fingerprint on both exposure policies (#3061).
        self.__dict__[PROCESSOR_KEYS_ATTR] = frozenset(injected_keys)
        try:
            yield processor_output
        finally:
            self.__dict__.pop(PROCESSOR_KEYS_ATTR, None)
            for key in injected_keys:
                try:
                    delattr(self, key)
                except AttributeError:
                    pass

    @method_decorator(ensure_csrf_cookie)
    def get(self, request: "HttpRequest", *args: Any, **kwargs: Any) -> HttpResponse:
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

        # Object-permission check (ADR-017) on the initial HTTP render. mount()
        # + handle_params() have populated the access-determining state (e.g.
        # self.<x>_id) that get_object() reads; enforce here, before render, so
        # the first server-rendered page can't leak a denied object (finding
        # #11). No-op for views that don't override get_object.
        from ..auth.core import enforce_object_permission

        try:
            enforce_object_permission(self, request)
        except PermissionDenied:
            return HttpResponseForbidden("Access denied for this object.")

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
        # ``streams`` is DERIVED from ``_streams`` on every get_context_data()
        # call — it is not user state and must never be persisted (#2112).
        #
        # Persisting it is self-poisoning: the restore paths below (and the WS
        # equivalent in runtime.py) safe_setattr every saved key back onto the
        # view, so ``self.streams`` would become a public attribute, the
        # attribute walk would put that STALE dict into the context, and the
        # existing-key-wins guard in mixins/context.py would then skip the live
        # data forever — every insert after a restore invisible.
        #
        # It is also pure bloat: the documented 500-item example persists ~45 KB
        # per GET, and streams exist precisely to keep large collections OUT of
        # state.
        if uses_legacy_exposure(self):
            _session_state = {
                k: v
                for k, v in _cached.items()
                if not isinstance(v, LiveComponent) and k != "streams"
            }
            request.session[view_key] = normalize_django_value(_session_state, state_roundtrip=True)

            # Legacy private/component persistence is separate from explicit
            # declared server fields. Never let it run as an explicit fallback.
            private_state = self._get_private_state()
            if private_state:
                request.session[f"{view_key}__private"] = normalize_django_value(
                    private_state, state_roundtrip=True
                )
            t0_sc = time.perf_counter()
            self._save_components_to_session(request, _cached)
        else:
            from .._exposure_sessions import save_server_state

            t0_sc = time.perf_counter()
            save_server_state(self, request)
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
        from .._child_rendering import render_view_full_template, render_view_with_diff

        html = render_view_full_template(self, request, serialized_context=state_serializable)
        # ADR-036 R1: recovery targets come from what the server rendered.
        from ..validation import note_rendered_recovery_targets

        note_rendered_recovery_targets(self, html)
        t_render_full = (time.perf_counter() - t0) * 1000
        liveview_content = html

        # Establish VDOM baseline for subsequent PATCH responses.
        t0 = time.perf_counter()
        _, _, _ = render_view_with_diff(self, request)
        t_render_diff = (time.perf_counter() - t0) * 1000

        if not uses_legacy_exposure(self):
            from .._exposure_child_persistence import save_child_states

            save_child_states(self, request)

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
                from .._exposure_diagnostics import log_failure_for

                # The wrapper render runs the project's context processors,
                # so its exception can carry application values (ADR-038).
                log_failure_for(
                    logger,
                    (self,),
                    e,
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

        # Inject view path into dj-root for WebSocket mounting — on the root
        # however it is written (any element, any other attributes), and only
        # where the author did not declare dj-view themselves (#2981).
        view_path = f"{self.__class__.__module__}.{self.__class__.__name__}"
        html = self._stamp_dj_view(html, view_path)

        # ADR-036: the page's own owner contracts, for events sent before a
        # socket mounts or over the HTTP fallback. Emitted outside dj-root so
        # the VDOM baseline is unaffected; all-legacy pages are unchanged.
        self.__dict__["_initial_parameter_contracts"] = _initial_parameter_contracts(
            self, view_path
        )

        # Inject LiveView client script
        html = self._inject_client_script(html)

        response: HttpResponse
        if getattr(self, "streaming_render", False):
            response = self._make_streaming_response(html)
        else:
            response = HttpResponse(html)
        # ADR-038 D-b: the service worker must not persist an explicit page's
        # HTML in its shell cache. Legacy responses are unchanged.
        from .._exposure import service_worker_cache_eligible
        from ..security.service_worker import SW_CACHE_HEADER, SW_CACHE_NO_STORE

        if not service_worker_cache_eligible(self):
            response[SW_CACHE_HEADER] = SW_CACHE_NO_STORE
        return response

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

        def _iter() -> Iterator[str]:
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

    def _is_asgi_context(self, request: Optional["HttpRequest"] = None) -> bool:
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

    async def aget(self, request: "HttpRequest", *args: Any, **kwargs: Any) -> HttpResponse:
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

        # Error responses (e.g. the object-permission 403 that get() returns on
        # an ADR-017 denial) must pass through with their status intact — never
        # be re-wrapped into a default-200 StreamingHttpResponse below. (Stage-11
        # review of #155 / finding #11 on the streaming path.)
        if getattr(sync_response, "status_code", 200) >= 400:
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
        async def _produce() -> None:
            try:
                await self.arender_chunks(full_html, emitter)
            except Exception as exc:  # pragma: no cover — defensive
                from .._exposure_diagnostics import log_failure_for

                # arender_chunks renders the view's templates with its context,
                # so the exception can carry application values (ADR-038).
                log_failure_for(
                    logger,
                    (self,),
                    exc,
                    "arender_chunks raised; cancelling emitter",
                    traceback=True,
                )
                await emitter.cancel("producer_error")
            finally:
                await emitter.close()

        produce_task = asyncio.ensure_future(_produce())

        # Disconnect watcher: poll request.is_disconnected() (Django 5.x).
        # Older Django versions don't expose it; we degrade silently.
        async def _watch_disconnect() -> None:
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

        async def _streaming_iter() -> AsyncIterator[bytes]:
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

    def post(self, request: "HttpRequest", *args: Any, **kwargs: Any) -> HttpResponse:
        """Handle POST requests - event handling"""
        from ..components.base import LiveComponent, SESSION_COMPONENT_TYPES

        # Referenced by the ``except`` at the end of this method, which must not
        # raise its own UnboundLocalError when the failure precedes their
        # assignment (e.g. a body that is not valid JSON). Previously that
        # masked the real exception as a 500 and the logger never recorded it.
        event_name: str = ""
        params: dict = {}

        try:
            # Ensure self.request is set for context processors and
            # _sync_state_to_rust csrf_token injection (#705).
            self.request = request

            def _inject_side_channels(resp_data: Dict[str, Any]) -> None:
                if hasattr(self, "_drain_flash"):
                    flash_commands = self._drain_flash()
                    if flash_commands:
                        resp_data["_flash"] = flash_commands
                if hasattr(self, "_drain_page_metadata"):
                    meta_commands = self._drain_page_metadata()
                    if meta_commands:
                        resp_data["_page_metadata"] = meta_commands

            # --- Authorization layer 1 of 3: view-level ---------------------
            # login_required / permission_required / check_permissions().
            # ``get()`` enforces this, and every WS/SSE event path enforces it
            # before dispatch; this transport enforced none of the three, so an
            # unauthenticated client could drive @event_handler methods on a
            # ``login_required = True`` view with a plain POST. The denial shape
            # is the 403+redirect the on_mount hook below uses: this path is a
            # programmatic call, so it cannot follow a page redirect the way
            # ``get()`` does.
            from ..auth import check_view_auth

            try:
                redirect_url = check_view_auth(self, request)
            except PermissionDenied:
                # check_view_auth *raises* for an authenticated user who lacks
                # the view's permission_required (only the unauthenticated case
                # returns a URL). Uncaught, the broad handler below would report
                # a permission failure as a 500.
                logger.warning(
                    "Auth denied for %s: missing view permission (HTTP POST)",
                    type(self).__name__,
                )
                return JsonResponse({"error": "Permission denied"}, status=403)
            if redirect_url:
                logger.info(
                    "Auth denied for %s: login required (HTTP POST)",
                    type(self).__name__,
                )
                return JsonResponse({"redirect": redirect_url}, status=403)

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
            legacy_exposure = uses_legacy_exposure(self)
            saved_state = request.session.get(view_key, {}) if legacy_exposure else {}

            # #2252: the write side tagged every Decimal
            # (``normalize_django_value(..., state_roundtrip=True)`` above), so
            # decode before anything is applied to the view — an undecoded tag
            # reaches the template as a dict.
            saved_state = decode_state_roundtrip(saved_state)

            for key, value in saved_state.items():
                if not key.startswith("_") and not callable(value):
                    safe_setattr(self, key, value, allow_private=False)

            # Restore user-defined _private attributes
            private_state = (
                request.session.get(f"{view_key}__private", {}) if legacy_exposure else {}
            )
            if private_state:
                self._restore_private_state(private_state)

            self._initialize_temporary_assigns()

            # Run on_mount hooks (auth guards, etc.) before mount
            hook_redirect = run_on_mount_hooks(self, request, **kwargs)
            if hook_redirect:
                return JsonResponse({"redirect": hook_redirect}, status=403)

            if not legacy_exposure:
                from .._exposure_sessions import load_server_state

                # Reconstruct transient server services for this HTTP request.
                # The persisted projection is validated separately and overlays
                # only declared server fields, never render/context attributes.
                self.mount(request, **kwargs)
                restored = load_server_state(self, request)
                if restored is not None:
                    for key, value in restored.items():
                        safe_setattr(self, key, value, allow_private=False, raise_on_blocked=True)
            elif not saved_state:
                self.mount(request, **kwargs)
                self._snapshot_user_private_attrs()
            else:
                pass

            self._assign_component_ids()

            # Restore component state
            component_state = (
                request.session.get(f"{view_key}_components", {}) if legacy_exposure else {}
            )
            for key, state in component_state.items():
                component = getattr(self, key, None)
                if component and isinstance(component, SESSION_COMPONENT_TYPES):
                    self._restore_component_state(component, state)

            # --- Authorization layer 3 of 3: object-level (ADR-017) ----------
            # Runs here, after the session-state restore above, because
            # ``get_object()`` reads the access-determining state (e.g.
            # ``self.<x>_id``) from it; and per event, as the WS path does, so a
            # session cannot carry a stale ``_object`` cache past a denial. A
            # no-op for views that do not override ``get_object``.
            from ..auth.core import enforce_object_permission

            try:
                enforce_object_permission(self, request)
            except PermissionDenied:
                logger.warning(
                    "Auth denied for %s: object permission (HTTP POST)",
                    type(self).__name__,
                )
                return JsonResponse({"error": "Access denied for this object."}, status=403)

            # Call the event handler — only @event_handler-decorated methods
            # can be invoked via POST (matches WS security)
            t_handler_ms = 0.0
            observation_before = None
            # ADR-031: an event carrying ``component_id`` targets the
            # registered component, as ``runtime._dispatch_component_event``
            # does over WebSocket (#1646 — the HTTP fallback must not differ).
            owner: Any = self
            component_id = params.get("component_id") if isinstance(params, dict) else None
            if component_id:
                registry = getattr(self, "_components", None) or {}
                owner = registry.get(component_id)
                if owner is None:
                    logger.warning(
                        "HTTP POST component not found: %s on %s",
                        component_id,
                        type(self).__name__,
                    )
                    return JsonResponse({"error": "Component not found"}, status=400)
                params = {k: v for k, v in params.items() if k != "component_id"}
            handler = getattr(owner, event_name, None)
            if handler is None and owner is not self:
                logger.warning(
                    "HTTP POST handler '%s' not found on component %s",
                    event_name,
                    component_id,
                )
                return JsonResponse({"error": "Event handler not found"}, status=400)
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
                # --- Authorization layer 2 of 3: handler-level -----------
                # ``@permission_required`` on the handler — the same check the
                # WS path runs (websocket_utils._validate_event_security →
                # auth.check_handler_permission). Returns True for a handler
                # that declares no permission, so the call is unconditional.
                # The WS path needs a sync_to_async wrapper here (#1648); this
                # path is synchronous, so the bare call is correct.
                from ..auth import check_handler_permission

                if not check_handler_permission(handler, request):
                    logger.warning(
                        "HTTP POST denied: handler '%s' on %s requires a "
                        "permission the caller lacks",
                        event_name,
                        type(self).__name__,
                    )
                    return JsonResponse({"error": "Permission denied"}, status=403)
                coerce = True
                if hasattr(handler, "_djust_decorators"):
                    event_meta = handler._djust_decorators.get("event_handler", {})
                    coerce = event_meta.get("coerce_types", True)

                if get_handler_parameter_policy(handler) == "strict" and "event" not in data:
                    # The flat body drops "_" keys for legacy handlers. Strict
                    # validation owns that namespace on every transport: it
                    # drops transport metadata, consumes _args and rejects the
                    # rest, so an unknown key is not silently discarded here.
                    params.update({k: v for k, v in data.items() if k.startswith("_")})
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

                call_args, call_kwargs = validated_call_arguments(validation)
                from ..components._interactive import DropdownMenu

                if isinstance(owner, DropdownMenu) and event_name == "observe_toggle":
                    from ..websocket import _snapshot_assigns, _compute_changed_keys

                    observation_before = _snapshot_assigns(self)
                t0_handler = time.perf_counter()
                if inspect.iscoroutinefunction(handler):
                    from asgiref.sync import async_to_sync

                    async_to_sync(handler)(*call_args, **call_kwargs)
                else:
                    handler(*call_args, **call_kwargs)
                t_handler_ms = (time.perf_counter() - t0_handler) * 1000

            # Persist user-defined _private attributes BEFORE get_context_data()
            # because get_context_data() sets render-cycle internals that we
            # don't want to accidentally capture.
            if legacy_exposure:
                private_state = self._get_private_state()
                if private_state:
                    request.session[f"{view_key}__private"] = normalize_django_value(
                        private_state, state_roundtrip=True
                    )
                else:
                    request.session.pop(f"{view_key}__private", None)

                updated_context = self.get_context_data()
                state = {
                    k: v for k, v in updated_context.items() if not isinstance(v, LiveComponent)
                }
                request.session[view_key] = normalize_django_value(state, state_roundtrip=True)
                self._save_components_to_session(request, updated_context)
            else:
                from .._exposure_sessions import save_server_state

                save_server_state(self, request)

            if (
                observation_before is not None
                and not _compute_changed_keys(observation_before, _snapshot_assigns(self))
                and not getattr(self, "_force_full_html", False)
                and not getattr(self, "_async_tasks", None)
                and not getattr(self, "_async_pending", None)
            ):
                noop_response = {"type": "noop", "event_name": event_name}
                _inject_side_channels(noop_response)
                return JsonResponse(noop_response)

            # A handler (view or ``component_id`` route) that set
            # ``self._skip_render = True`` asked for no render this turn. The
            # WebSocket / SSE routes resolve that through
            # ``_resolve_skip_render`` (#2834, #2924); the HTTP fallback never
            # consulted it, so the same flag rendered here and was never reset
            # (#3038, the #1646 parallel-path class). The state above is already
            # saved, so the next render diffs from the last one the client has.
            # The answer is an empty patch list with no ``version``: nothing was
            # rendered, so the client's VDOM cursor must not move. Side channels
            # (flash, page metadata) still go out, as they do with the WS noop.
            # No ``cache_request_id``: the WS/SSE noop carries none, so a
            # skipped turn is never stored as an ``@cache`` hit on any transport.
            from ..websocket import _resolve_skip_render

            if _resolve_skip_render(self):
                skip_response: Dict[str, Any] = {"patches": []}
                contract_fields = _http_parameter_contract_fields(self, request)
                if contract_fields is None:
                    return _contract_error_response()
                skip_response.update(contract_fields)
                if hasattr(self, "_drain_flash"):
                    flash_commands = self._drain_flash()
                    if flash_commands:
                        skip_response["_flash"] = flash_commands
                if hasattr(self, "_drain_page_metadata"):
                    meta_commands = self._drain_page_metadata()
                    if meta_commands:
                        skip_response["_page_metadata"] = meta_commands
                return JsonResponse(skip_response)

            # Apply context processors so the render includes auth context
            # (user, perms, messages, etc.). Without this, template conditionals
            # like {% if user.is_authenticated %} evaluate to false and the HTTP
            # fallback returns logged-out HTML. Fixes #705.
            # Unified via _processor_context context manager (#717).
            with self._processor_context(request):
                from .._child_rendering import render_view_with_diff

                t0_render = time.perf_counter()
                html, patches_json, version = render_view_with_diff(self, request)
                t_render_ms = (time.perf_counter() - t0_render) * 1000

            # ADR-036: the rendered tree's owner contracts travel with the DOM
            # update they describe. Discovery failure withholds that update and
            # drops the unsent diff baseline, as the socket runtime does.
            contract_fields = _http_parameter_contract_fields(self, request)
            if contract_fields is None:
                self._rust_view.reset()
                return _contract_error_response()

            if not legacy_exposure:
                from .._exposure_child_persistence import save_child_states

                save_child_states(self, request)

            # ADR-018 iter 18a — HTTP sticky-child state save (Decision 4,
            # HTTP side). The POST path has no ``view_id`` routing — it
            # always operates on ``self`` (the parent) — so this is a
            # parent-driven sweep, not a child-routed save. It MUST run
            # AFTER ``render_with_diff`` because ``{% live_render %}``
            # registers children on ``self._child_views`` during the
            # template render (verified: ``_child_views`` is empty before
            # the render at line ~604, populated after it here). For each
            # registered child satisfying the both-opt-in gate, persist
            # its state under the stable sticky key; then write the GC
            # ledger. Django saves the session at response time.
            from .sticky import (
                save_sticky_child_state_sync,
                sticky_child_should_persist,
                warn_sticky_child_optin_skip,
                write_sticky_index_and_prune_sync,
            )

            from .sticky import sticky_ids_index_key as _sticky_index_key

            _sticky_children = (
                self._get_all_child_views() if hasattr(self, "_get_all_child_views") else {}
            )
            _sticky_to_save = [
                child
                for child in _sticky_children.values()
                if sticky_child_should_persist(child, self)
            ]
            # ADR-018 iter 18c — for each registered child NOT in the save set,
            # warn if it's the Decision-5 opt-in mismatch (child opted in,
            # parent did not). The helper re-checks the misconfiguration, so
            # children that simply don't opt in produce no warning.
            for _child in _sticky_children.values():
                if _child not in _sticky_to_save:
                    warn_sticky_child_optin_skip(_child, self)
            # Run the save + ledger sweep when there are children to save OR a
            # stale ledger exists (so a parent whose last sticky child was
            # removed still gets its orphans pruned). A parent that never had
            # sticky children pays zero cost — no ledger key, empty sweep.
            if _sticky_to_save or _sticky_index_key(request.path) in request.session:
                for _child in _sticky_to_save:
                    save_sticky_child_state_sync(_child, request.session, request.path)
                write_sticky_index_and_prune_sync(self, request.session, request.path)

            import json as json_module

            PATCH_THRESHOLD = 100

            cache_request_id = params.get("_cacheRequestId")

            # Inject debug info for the debug panel (HTTP-only mode)
            from django.conf import settings as _settings

            def _inject_debug(resp_data: Dict[str, Any]) -> None:
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

            if patches_json:
                patches = json_module.loads(patches_json)
                patch_count = len(patches)

                if patch_count > 0 and patch_count <= PATCH_THRESHOLD:
                    response_data = {"patches": patches, "version": version, **contract_fields}
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    _inject_side_channels(response_data)
                    _inject_debug(response_data)
                    return JsonResponse(response_data)
                else:
                    self._rust_view.reset()
                    response_data = {"html": html, "version": version, **contract_fields}
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    _inject_side_channels(response_data)
                    _inject_debug(response_data)
                    return JsonResponse(response_data)
            else:
                response_data = {"html": html, "version": version, **contract_fields}
                if cache_request_id:
                    response_data["cache_request_id"] = cache_request_id
                _inject_side_channels(response_data)
                _inject_debug(response_data)
                return JsonResponse(response_data)

        except Exception as e:
            import traceback
            from django.conf import settings

            # uses_legacy_exposure is the module-level import; a local import
            # here would make the name local to all of post().
            from .._exposure_diagnostics import diagnostics_policy_allows

            if not diagnostics_policy_allows(self):
                # ADR-038: undeclared state can occur in the exception's message,
                # its traceback and the posted params, so in production a
                # nonlegacy view gets the value-free log line and the generic
                # response. Under DEBUG it gets Django-like detail (D-a).
                from .._exposure_diagnostics import log_failure_for

                log_failure_for(logger, (self,), e, "HTTP event failed")
                return JsonResponse(
                    {
                        "error": "An error occurred processing your request. Please try again.",
                        "debug_hint": "Check server logs for details",
                    },
                    status=500,
                )
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
