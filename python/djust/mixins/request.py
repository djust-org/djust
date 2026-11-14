"""
RequestMixin - HTTP GET/POST request handling for LiveView.
"""

import asyncio
import contextvars
import inspect
import json
from types import MappingProxyType
import logging
import sys
import threading
import time
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Tuple,
    cast,
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

from ..serialization import normalize_django_value
from ..utils import is_model_list
from ..validation import (
    validate_handler_params,
    validated_call_arguments,
    get_handler_parameter_policy,
)
from ..security import safe_setattr, sanitize_for_log
from ..security.event_guard import is_safe_event_name
from ..decorators import is_event_handler, is_push_only
from ..hooks import run_on_mount_hooks
from .._exposure import uses_legacy_exposure
from .._view_hooks import call_async_result_sync, call_hook_sync

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

#: ``LiveView.http_status_of_state`` as the framework declares it (#3706): an empty read-only map. It is
#: one object, so ``view.http_status_of_state is UNDECLARED_STATUS_MAP`` tells a view that declares
#: nothing (read ``LIVEVIEW_CONFIG["http_status_of_state"]``) from one that declares ``{}`` (a map of
#: its own, which is empty: it opts out of the project's). It compares equal to ``{}`` and is falsy.
UNDECLARED_STATUS_MAP: Mapping[str, int] = MappingProxyType({})

#: How many times queued background work may queue more before the fallback
#: gives up and says so. A task that schedules the next page of a load is a
#: handful of rounds; a loop that never ends would hold the request open.
_HTTP_BACKGROUND_MAX_ROUNDS = 8

#: Name prefix of the worker threads that run background work under a deadline.
_HTTP_BACKGROUND_THREAD_PREFIX = "djust-http-bg"

#: How many background worker threads may hold a slot in this process at once
#: (a thread started under the deadline, or an abandoned one that has not
#: ended). A sync callback the deadline stopped waiting for cannot be cancelled
#: and keeps running, and any thread in flight can become one, so bounding the
#: slots (reserved atomically before a worker starts) is what makes "at most this
#: many abandoned threads are alive" true under concurrent POSTs. Past it a new
#: task is not started: it is refused with a warning and a ``TimeoutError`` for
#: ``handle_async_result``, so a callback that hangs on every POST cannot pile up
#: threads (each holding a database connection) without limit. The slots of a
#: thread that finishes, or of an abandoned one that ends, are released.
_HTTP_BACKGROUND_MAX_ABANDONED = 16

_ABANDONED_WORKERS: "set[threading.Thread]" = set()
_WORKERS_RESERVED = 0  # in flight for a POST that is still waiting
_ABANDONED_LOCK = threading.Lock()


def _reserve_worker_slot() -> Optional[int]:
    """Take a worker slot, or return how many are held when none is free.

    ``None`` means a slot is now reserved and :func:`_release_worker_slot` must
    follow. Counting and reserving are one step under a lock, so concurrent POSTs
    cannot all pass the check before any of them starts.
    """
    global _WORKERS_RESERVED
    with _ABANDONED_LOCK:
        _ABANDONED_WORKERS.difference_update([t for t in _ABANDONED_WORKERS if not t.is_alive()])
        held = len(_ABANDONED_WORKERS) + _WORKERS_RESERVED
        if held >= _HTTP_BACKGROUND_MAX_ABANDONED:
            return held
        _WORKERS_RESERVED += 1
        return None


def _release_worker_slot(abandoned: Optional[threading.Thread] = None) -> None:
    """Give a reserved slot back; an abandoned worker keeps one until it ends."""
    global _WORKERS_RESERVED
    with _ABANDONED_LOCK:
        _WORKERS_RESERVED -= 1
        if abandoned is not None:
            _ABANDONED_WORKERS.add(abandoned)


def _http_url_params(request: Any) -> Dict[str, Any]:
    """The query string as ``handle_params()`` receives it.

    A single value is a scalar and a repeated key stays a list, on the GET, the
    socket mount and the ``url_change`` frame alike. One definition, so the
    fallback POST cannot read the URL differently from the page it answers.
    """
    return {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in request.GET.lists()}


def _run_http_handle_params(view: Any, request: Any) -> None:
    """Run the view's params hook the way the GET does.

    The GET and the socket mount call it right after ``mount()``. The page-POST
    fallback builds a fresh view and calls ``mount()`` on it, so it calls this
    next, or a page that reads its URL there (a filter, a scope, ``?tab=``)
    meets the event with ``mount()``'s defaults and answers with a diff against
    a page the browser never showed (#3624). The browser POSTs to
    ``window.location.href``, so the query string is on the POST's request.
    """
    from .._params_form import run_handle_params_sync

    run_handle_params_sync(view, _http_url_params(request), request.get_full_path())


def _take_queued_background_work(view: Any) -> List[Tuple[str, Any, Any, Any]]:
    """Pop ``view``'s queued ``start_async`` tasks as ``(name, fn, args, kwargs)``."""
    tasks: List[Tuple[str, Any, Any, Any]] = []
    queued = getattr(view, "_async_tasks", None)
    if queued:
        tasks.extend((name, *entry) for name, entry in list(queued.items()))
        view._async_tasks = {}
    return tasks


class _BackgroundBudget:
    """The time one fallback request may spend waiting for background work (#3634).

    One budget per request, shared by every call to
    :func:`_run_http_background_work` (mount's work, the handler's, the page's
    after a child's), not one per task: what the deadline bounds is the POST, and
    per-task deadlines would let N tasks hold it for N times as long. The clock
    starts when the first task does, so a slow handler does not spend it.
    """

    def __init__(self, timeout: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.timeout = timeout
        self._clock = clock
        self._deadline: Optional[float] = None

    def remaining(self) -> float:
        """Seconds left; the first call starts the clock."""
        now = self._clock()
        if self._deadline is None:
            self._deadline = now + self.timeout
        return max(0.0, self._deadline - now)

    def spent(self) -> bool:
        """True once the time is up: no further task may start."""
        return self._deadline is not None and self._clock() >= self._deadline


def _http_background_budget() -> Optional[_BackgroundBudget]:
    """A fresh budget for this request, or ``None`` when no deadline is configured."""
    from ..config import http_fallback_background_timeout

    timeout = http_fallback_background_timeout()
    return None if timeout is None else _BackgroundBudget(timeout)


class _BackgroundRun:
    """One task running on a worker thread the request may stop waiting for."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.abandoned = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.task: Optional["asyncio.Future[Any]"] = None
        self.thread: Optional[threading.Thread] = None

    def abandon(self) -> None:
        """Ask the task to stop (an ``async def`` callback honours it) and stop waiting."""
        with self.lock:
            self.abandoned = True
            if self.task is not None and self.loop is not None:
                try:
                    self.loop.call_soon_threadsafe(self.task.cancel)
                except RuntimeError:
                    logger.debug("[djust HTTP] background task's loop was already closed")


def _start_background_run(
    view: Any, name: str, callback: Any, args: Any, kwargs: Any
) -> _BackgroundRun:
    """Start ``callback`` on a worker thread and return the handle to wait on.

    Not on the request thread: ``async_to_sync`` runs a sync callback (through
    ``sync_to_async``) on the thread that called it, so a timeout on the
    coroutine awaiting it can never free the request. Called from a worker, the
    callback runs on the worker, which the request is free to leave behind. The
    thread is a daemon, runs inside a copy of the request's context (tenant,
    turn state) and closes the database connections it opened when it ends.
    """
    from asgiref.sync import async_to_sync

    from ..mixins.async_work import run_async_callback

    run = _BackgroundRun()

    async def runner() -> Any:
        task = asyncio.ensure_future(run_async_callback(callback, args, kwargs, owner=view))
        with run.lock:
            run.loop = asyncio.get_running_loop()
            run.task = task
            if run.abandoned:
                task.cancel()
        return await task

    def body() -> None:
        from django.db import connections

        completed = False
        try:
            run.result = async_to_sync(runner)()
            completed = True
        except (Exception, asyncio.CancelledError) as exc:
            # Handed back to the request thread (a cancelled task as well).
            run.error = exc
            completed = True
        finally:
            if not completed:
                # Anything else (SystemExit, KeyboardInterrupt) is not swallowed
                # here: it ends the thread, and the request thread gets the very
                # same exception, as it would without a deadline, instead of an
                # outcome that reads as success.
                run.error = sys.exc_info()[1]
            try:
                connections.close_all()
            except Exception:  # noqa: BLE001 - nothing to do for a failed close
                logger.debug("[djust HTTP] closing a background worker's connections failed")
            run.done.set()

    thread = threading.Thread(
        target=contextvars.copy_context().run,
        args=(body,),
        name="%s-%s" % (_HTTP_BACKGROUND_THREAD_PREFIX, sanitize_for_log(name)[:32]),
        daemon=True,
    )
    run.thread = thread
    thread.start()
    return run


def _run_http_background_work(view: Any, budget: Optional[_BackgroundBudget] = None) -> None:
    """Run ``view``'s ``start_async`` / ``@background`` work inside this request.

    On the socket the handler's state is flushed, then the work runs and its
    result arrives as a later frame. The fallback has one response and no later
    frame, but it is a plain request: the work can finish before the answer is
    rendered, so the one answer already holds its result. Without this the
    queued tasks were discarded with the request's view, a page that loads
    after its first render kept its loading state on every POST, and a write
    scheduled after a "Sending..." render never ran (#3624).

    Each task is dispatched by ``run_async_callback``, the helper the socket and
    SSE paths share, so ``async def`` and sync callbacks behave as they do
    there. A task cancelled with ``cancel_async()`` is skipped. The view's
    ``handle_async_result(name, result=..., error=...)`` sees each outcome; a
    failure is logged and handed to it rather than failing the request. Work a
    task or ``handle_async_result`` queues in turn runs in the next round, up to
    :data:`_HTTP_BACKGROUND_MAX_ROUNDS`; whatever is still queued after that is
    dropped, with a warning.

    With ``budget`` (``LIVEVIEW_CONFIG["http_fallback_background_timeout"]``, one
    per request) each task runs on a worker thread and the request waits for it
    only while the budget lasts. A task still running when it is spent is
    abandoned: an ``async def`` callback is cancelled, a sync one keeps running
    (a thread cannot be cancelled) and its late result is discarded. Either way
    ``handle_async_result`` receives a ``TimeoutError`` as ``error=``, and every
    task still queued is not started and receives one too. With no budget the
    callback runs on the request thread and nothing bounds it.
    """
    from asgiref.sync import async_to_sync

    from .._exposure_diagnostics import log_failure_for
    from ..mixins.async_work import run_async_callback
    from ..runtime import _consume_async_cancel

    for _round in range(_HTTP_BACKGROUND_MAX_ROUNDS):
        tasks = _take_queued_background_work(view)
        if not tasks:
            return
        dropped: List[str] = []
        for name, callback, args, kwargs in tasks:
            if _consume_async_cancel(view, name):
                continue
            result: Any = None
            error: Optional[BaseException] = None
            try:
                if budget is None:
                    result = async_to_sync(run_async_callback)(callback, args, kwargs, owner=view)
                elif budget.spent():
                    dropped.append(name)
                    error = TimeoutError(
                        "start_async task %r was not started: the %.4g second budget of "
                        "http_fallback_background_timeout was spent" % (name, budget.timeout)
                    )
                else:
                    held = _reserve_worker_slot()
                    if held is not None:
                        logger.warning(
                            "[djust HTTP] start_async task %s on %s was not started: %d background "
                            "worker threads (abandoned or still running) hold the %d slots. A "
                            "callback that hangs on every POST would pile up threads; fix it or "
                            "guard it off the fallback.",
                            sanitize_for_log(name),
                            view.__class__.__name__,
                            held,
                            _HTTP_BACKGROUND_MAX_ABANDONED,
                        )
                        error = TimeoutError(
                            "start_async task %r was not started: %d background worker threads "
                            "(abandoned or still running) hold the %d slots"
                            % (name, held, _HTTP_BACKGROUND_MAX_ABANDONED)
                        )
                    else:
                        abandoned_thread: Optional[threading.Thread] = None
                        try:
                            run = _start_background_run(view, name, callback, args, kwargs)
                            finished = run.done.wait(budget.remaining())
                            if not finished:
                                run.abandon()
                                abandoned_thread = run.thread
                        finally:
                            _release_worker_slot(abandoned_thread)
                        if finished:
                            if run.error is not None:
                                raise run.error
                            result = run.result
                        else:
                            logger.warning(
                                "[djust HTTP] start_async task %s on %s did not finish within the "
                                "%.4g second budget of http_fallback_background_timeout; the "
                                "fallback stopped waiting for it and answers without its result. "
                                "A sync callback keeps running on its worker thread.",
                                sanitize_for_log(name),
                                view.__class__.__name__,
                                budget.timeout,
                            )
                            error = TimeoutError(
                                "start_async task %r did not finish within the %.4g second budget "
                                "of http_fallback_background_timeout; the fallback stopped waiting"
                                % (name, budget.timeout)
                            )
            except asyncio.CancelledError:
                continue  # cancelled while it ran: no result, as on the socket
            except Exception as exc:  # noqa: BLE001 - routed to handle_async_result below
                error = exc
                log_failure_for(
                    logger,
                    (view,),
                    exc,
                    "[djust HTTP] start_async task %s on %s raised",
                    sanitize_for_log(name),
                    view.__class__.__name__,
                    traceback=True,
                )
            if not hasattr(view, "handle_async_result"):
                continue
            try:
                call_async_result_sync(
                    view, view.handle_async_result, name, result=result, error=error
                )
            except Exception as exc:  # noqa: BLE001 - one task's handler must not fail the rest
                log_failure_for(
                    logger,
                    (view,),
                    exc,
                    "[djust HTTP] handle_async_result for %s on %s raised",
                    sanitize_for_log(name),
                    view.__class__.__name__,
                    traceback=True,
                )
        if dropped:
            logger.warning(
                "[djust HTTP] %d start_async task(s) on %s were not started (%s): the "
                "http_fallback_background_timeout budget was spent.",
                len(dropped),
                view.__class__.__name__,
                ", ".join(sanitize_for_log(n) for n in dropped),
            )
    if _take_queued_background_work(view):
        logger.warning(
            "%s kept queueing background work (start_async) after %d rounds on the "
            "HTTP fallback; the rest was dropped.",
            view.__class__.__qualname__,
            _HTTP_BACKGROUND_MAX_ROUNDS,
        )


def _drain_http_side_channels(view: Any, resp_data: Dict[str, Any]) -> None:
    """Put ``view``'s queued client side effects on an HTTP fallback answer.

    The WebSocket turn sends each queue as its own frame
    (``LiveViewConsumer._flush_all_pending``). An HTTP answer carries them as
    keys the client's ``applyHttpSideChannels`` applies: ``_flash``,
    ``_page_metadata``, ``_push_events`` (``push_event`` / ``push_commands``,
    in the ``push_event`` frame's ``{event, payload}`` shape) and ``_layout``
    (``set_layout``, the ``layout`` frame's ``{path, html}``). Then the
    ``defer()`` callbacks run, last, as they do on the socket. Before this, the
    fallback carried only flash and page metadata, and dropped the other three
    queues on the floor.
    """
    if hasattr(view, "_drain_flash"):
        flash_commands = view._drain_flash()
        if flash_commands:
            resp_data["_flash"] = flash_commands
    if hasattr(view, "_drain_page_metadata"):
        meta_commands = view._drain_page_metadata()
        if meta_commands:
            resp_data["_page_metadata"] = meta_commands
    if hasattr(view, "_drain_push_events"):
        events = view._drain_push_events()
        if isinstance(events, list) and events:
            resp_data["_push_events"] = [
                {"event": event_name, "payload": payload} for event_name, payload in events
            ]
    if hasattr(view, "_drain_pending_layout"):
        layout_path = view._drain_pending_layout()
        if layout_path:
            layout = _render_http_layout(view, layout_path)
            if layout is not None:
                resp_data["_layout"] = layout
    _run_http_deferred(view)


def _render_http_layout(view: Any, layout_path: str) -> Optional[Dict[str, str]]:
    """``set_layout``'s ``{path, html}``, with the socket's error handling."""
    from django.conf import settings as django_settings
    from django.template.exceptions import TemplateDoesNotExist

    from .._exposure_diagnostics import log_failure_for
    from .layout import render_pending_layout

    try:
        html = render_pending_layout(view, layout_path)
    except TemplateDoesNotExist:
        logger.warning("set_layout(%r) — template not found; ignoring swap request", layout_path)
        return None
    except Exception as exc:  # noqa: BLE001 — a layout error must not fail the event answer
        log_failure_for(
            logger,
            (view,),
            exc,
            "set_layout(%r) — template rendering raised; ignoring swap request",
            layout_path,
            traceback=True,
        )
        # In DEBUG, re-raise so programmer errors are visible (the socket does).
        if getattr(django_settings, "DEBUG", False):
            raise
        return None
    return {"path": layout_path, "html": html}


def _run_http_deferred(view: Any) -> None:
    """Run ``view.defer(...)`` callbacks after an HTTP answer's render.

    Mirrors ``LiveViewConsumer._flush_deferred``: each callback runs once, a
    failure is logged at WARN and the next one still runs, and a coroutine a
    callback returns is awaited. A callback that calls ``defer()`` again queues
    for the next event, as on the socket.
    """
    if not hasattr(view, "_drain_deferred"):
        return
    callbacks = view._drain_deferred()
    if not isinstance(callbacks, list) or not callbacks:
        return
    from asgiref.sync import async_to_sync

    from .._exposure_diagnostics import log_failure_for

    async def _await(awaitable: Any) -> Any:
        return await awaitable

    for callback, args, kwargs in callbacks:
        try:
            result = callback(*args, **kwargs)
            if inspect.iscoroutine(result):
                async_to_sync(_await)(result)
        except Exception as exc:  # noqa: BLE001 — see docstring
            log_failure_for(
                logger,
                (view,),
                exc,
                "[djust HTTP] Deferred callback %s on %s raised; continuing to next",
                getattr(callback, "__qualname__", repr(callback)),
                view.__class__.__name__,
                level="warning",
                traceback=True,
            )


#: Sent by the client when its HTTP scope holds strict contracts, so a response
#: whose tree no longer has any carries an explicit clear (the stateless HTTP
#: counterpart of the socket runtime's ``_parameter_contracts_active``).
PARAMETER_CONTRACTS_HEADER = "X-Djust-Parameter-Contracts"


#: A page POST carrying this header (value ``1``) and no event asks for the
#: page as the saved state renders it now (#4108).
RESYNC_HEADER = "X-Djust-Resync"
#: What such a request is called where an event name is logged.
RESYNC_EVENT = "djust:resync"


def _compress_fallback_answer(request: Any, response: Any) -> Any:
    """Gzip a page-POST answer for a client that accepts it (#4108).

    A whole-page answer is the root's HTML as JSON: tens of kilobytes for a
    page with a table, and it compresses about sevenfold. Whether a deployment
    compresses ``application/json`` is a matter of its middleware and proxy
    configuration (nginx's default ``gzip_types`` is ``text/html`` alone), so
    the answer is compressed here, by Django's own ``GZipMiddleware`` applied
    to this one response. That keeps its rules: only for a request that
    accepts gzip, only a body of 200 bytes or more (a patch answer is usually
    smaller and goes out as it is), ``Vary: Accept-Encoding``, and the random
    padding that blunts BREACH. A response that already has a
    ``Content-Encoding`` is left alone, and one this function compressed is
    left alone by a ``GZipMiddleware`` the project installed.
    ``LIVEVIEW_CONFIG["http_fallback_compress"] = False`` turns it off.
    """
    try:
        from ..config import config

        if config.get("http_fallback_compress", True) is False:
            return response
        from django.middleware.gzip import GZipMiddleware

        return GZipMiddleware(lambda _request: response).process_response(request, response)
    except Exception:  # noqa: BLE001 - compression is an optimisation, never a failure
        logger.debug("HTTP fallback answer was not compressed", exc_info=False)
        return response


def _initial_parameter_contracts(view: Any, view_path: str) -> Optional[str]:
    """Escaped JSON for the initial page's contract element, or None if legacy.

    ``false`` marks discovery failure where strict contracts can exist: the
    client installs an invalid scope and strict lookups fail closed instead of
    guessing a legacy contract. A view that can only be legacy keeps its legacy
    page, as the socket path keeps a legacy session's frame shape.
    """
    from .._parameter_metadata import parameter_contract_manifest
    from ..security.json_script import escape_json_for_script  # the defining module (#3820)
    from ..validation import _strict_possible

    try:
        manifest = parameter_contract_manifest(view)
    except Exception:  # noqa: BLE001 — declaration errors must not break the page
        logger.warning("Initial parameter contracts unavailable")
        if not _strict_possible(view):
            return None
        return escape_json_for_script(json.dumps({"view": view_path, "contracts": False}))
    if manifest is None:
        return None
    return escape_json_for_script(json.dumps({"view": view_path, "contracts": manifest}))


def _http_parameter_contract_fields(view: Any, request: Any) -> Optional[Dict[str, Any]]:
    """Contract fields for an HTTP render response (``{}`` for a legacy tree).

    None means discovery failed where strict contracts can exist: the caller
    withholds the DOM update. A legacy-only view whose client never advertised
    contracts keeps its legacy response shape instead.
    """
    from .._parameter_metadata import parameter_contract_manifest
    from ..validation import _strict_possible

    try:
        manifest = parameter_contract_manifest(view)
    except Exception:  # noqa: BLE001 — never send a DOM update with invalid contracts
        logger.warning("Render parameter contracts unavailable")
        if request.headers.get(PARAMETER_CONTRACTS_HEADER) != "1" and not _strict_possible(view):
            return {}
        return None
    if manifest is None and request.headers.get(PARAMETER_CONTRACTS_HEADER) != "1":
        return {}
    view_path = f"{view.__class__.__module__}.{view.__class__.__name__}"
    return {"parameter_contracts": manifest, "parameter_contract_view": view_path}


def _contract_error_response() -> JsonResponse:
    return JsonResponse(
        {"type": "error", "error": "Render parameter contracts unavailable."}, status=500
    )


def _view_supplies(view: Any, key: str) -> bool:
    """Whether ``view`` itself provides ``key``, so a processor value must not replace it.

    ``hasattr`` is not enough: a framework-derived class attribute (the marker
    ``get_context_data`` already honours, ``_djust_framework_derived``) answers
    ``hasattr`` but is deliberately NOT collected into the context, so the
    processor's value is the only copy of that name the render would get.
    ``ThemeMixin.theme_head`` is one (#3626). Such an attribute is injectable
    only when it is a non-data descriptor, which an instance assignment
    shadows and ``delattr`` then uncovers again.
    """
    if key in view.__dict__:
        return True
    for klass in type(view).__mro__:
        if key in klass.__dict__:
            descriptor = klass.__dict__[key]
            if getattr(descriptor, "_djust_framework_derived", False) and not hasattr(
                type(descriptor), "__set__"
            ):
                return False
            return True
    return hasattr(view, key)


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
        _explicit_child_vdom_bindings: Dict[str, Any]
        http_status_of_state: Mapping[str, int]
        state_attribute: str

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
        def _stamp_dj_view(html: str, view_path: str, *, trusted: bool = False) -> str: ...

        def handle_params(self, params: Dict[str, Any], uri: str) -> None: ...

        def mount(self, request: Any, **kwargs: Any) -> None: ...

        def _initialize_temporary_assigns(self) -> None: ...

        def _inject_client_script(self, html: str) -> str: ...

        def _get_all_child_views(self) -> Dict[str, Any]: ...

        def _record_dj_model_fields_from_rust(self, rust_view: Any) -> None: ...

        def _assign_component_ids(self) -> None: ...

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
            if not _view_supplies(self, key):
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
        """Handle GET requests - initial page load, as one ``mount`` turn (ADR-044).

        The turn-middleware chain runs around :meth:`_get_turn_body` when
        ``LIVEVIEW_MIDDLEWARE`` lists any middleware (and the request is not
        already inside its own turn, as under :meth:`aget`).
        """
        from ..turns import app_chain_configured, current_turn

        running = current_turn()
        if not app_chain_configured() or (running is not None and running.request is request):
            return self._get_turn_body(request, *args, **kwargs)
        from .._turn_ports import run_http_turn

        return run_http_turn(
            request,
            lambda: self._get_turn_body(request, *args, **kwargs),
            kind="mount",
            source="page",
            view=self,
            json_body=False,
        )

    def _get_turn_body(self, request: "HttpRequest", *args: Any, **kwargs: Any) -> HttpResponse:
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
        # Django's middleware chain (LoginRequiredMiddleware included) has
        # processed this request for this view.
        from ..auth._login_required_middleware import note_http_dispatch

        note_http_dispatch(self, request)

        # Check login_required / permission_required (matches WebSocket path).
        # Without this, views with login_required=True render their full HTML
        # to unauthenticated users on the initial HTTP GET.
        from ..auth import check_view_auth

        # http_dispatched: Django's View.dispatch() brought this request here
        # after running the view's own dispatch() and method decorators, so
        # access control that lives there has already run (check_view_auth
        # step 5 refuses it only on paths that skip dispatch()).
        redirect_url = check_view_auth(self, request, http_dispatched=True)
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

        # ADR-035: a view that looks its object up from the route receives the
        # URL kwargs Django resolved for this request, never other input.
        bind_route = getattr(self, "_djust_bind_route_kwargs", None)
        if callable(bind_route):
            bind_route(kwargs)

        # IMPORTANT: mount() must be called first to initialize clean state
        t0 = time.perf_counter()
        call_hook_sync(self.mount, request, **kwargs)
        # Which ``_private`` attributes mount() created: saved with the view's
        # state so a later request can be rebuilt without mount() (1.3).
        cast(Any, self)._snapshot_user_private_attrs()
        t_mount = (time.perf_counter() - t0) * 1000

        # Call handle_params after mount (Phoenix parity).
        # The WebSocket path does this in websocket.py:1261-1266, but the
        # HTTP GET path was missing it. Without this, URL params like ?tab=X
        # are only read after WebSocket connects, causing a content flash.
        _run_http_handle_params(self, request)

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
        from ..sync_turn import run_sync_step

        context = run_sync_step(self, "render", self.get_context_data)
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

        t0_sc = time.perf_counter()
        # A page load is a fresh mount() and a new tab (#4106): its state is
        # saved as mount() left it, under an id the page carries and its client
        # sends back. Another tab on this route keeps its own state.
        from .. import _automatic_state as _saved_state

        self.__dict__["_djust_tab"] = _saved_state.new_tab_id()
        from .._exposure_sessions import save_server_state

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

        self._prepare_child_ids()
        self._defer_lazy_registration = True
        html = render_view_full_template(self, request, serialized_context=state_serializable)
        # ADR-036 R1: recovery targets come from what the server rendered.
        from ..validation import note_rendered_recovery_targets

        note_rendered_recovery_targets(self, html)
        # #3252: the VDOM-diff render below runs the template a SECOND time, so
        # the tag stashes a second thunk for every lazy slot. Those are NOT
        # interchangeable with the page pass: `_prepare_child_ids` scopes the
        # ids per render, and an explicit-exposure parent assigns different ones
        # on the two passes. Hold the page pass and restore it below, so neither
        # the fill nor the streaming emitter ever sees the baseline's copies.
        page_lazy_thunks = list(getattr(self, "_lazy_thunks", None) or [])
        t_render_full = (time.perf_counter() - t0) * 1000
        liveview_content = html

        prepared_page = getattr(self, "_pending_authenticated_page", False)
        authenticated_root = getattr(self, "_prepared_authenticated_partial_root", False)
        page_root = authenticated_root or getattr(self, "_prepared_authenticated_page_root", False)
        # Validate the final wrapped page before committing its prepared diff
        # baseline: a rejected response was never delivered.
        t_render_diff = 0.0
        if not prepared_page:
            t0 = time.perf_counter()
            self._prepare_child_ids()
            baseline_html, _, _ = render_view_with_diff(self, request)
            # #4108: the digest of the render this page shows, which the client
            # echoes with its fallback POSTs (emitted outside dj-root by
            # ``_inject_client_script``). A page prepared in one evaluation
            # (the branch not taken here) has no separate diff render; it gets
            # its first digest from its first fallback answer.
            from .._render_digest import render_token

            self.__dict__["_page_render_digest"] = render_token(self, request, baseline_html)
            t_render_diff = (time.perf_counter() - t0) * 1000
            self._lazy_thunks = list(page_lazy_thunks)
            if not uses_legacy_exposure(self):
                from .._exposure_child_persistence import save_child_states

                save_child_states(self, request)
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

        if page_root and html is not liveview_content:
            # Django's wrapper produces a plain string. Preserve the exact
            # original live output's provenance, and treat wrapper bytes as
            # opaque. A duplicate or changed insertion cannot be authenticated.
            from django.core.exceptions import ImproperlyConfigured
            from .._render_provenance import RenderedHTML

            content = str(liveview_content)
            if not isinstance(liveview_content, RenderedHTML) or html.count(content) != 1:
                raise ImproperlyConfigured("Unable to authenticate the wrapped LiveView root.")
            start = html.index(content)
            html = RenderedHTML(html[:start]) + liveview_content + html[start + len(content) :]

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
        if page_root:
            # Authenticate before stamping; only the framework-generated view
            # attribute on this already validated page may gain provenance.
            from .._rust import authenticated_root_shell

            authenticated_root_shell(
                html, list(cast("RenderedHTML", html).root_spans), exact_range=authenticated_root
            )
            html = self._stamp_dj_view(html, view_path, trusted=True)
        else:
            html = self._stamp_dj_view(html, view_path)

        # ADR-036: the page's own owner contracts, for events sent before a
        # socket mounts or over the HTTP fallback. Emitted outside dj-root so
        # the VDOM baseline is unaffected; all-legacy pages are unchanged.
        self.__dict__["_initial_parameter_contracts"] = _initial_parameter_contracts(
            self, view_path
        )

        # Inject LiveView client script
        html = self._inject_client_script(html)
        html = _saved_state.page_with_tab(html, self.__dict__.get("_djust_tab", ""))

        # #3252: the page-POST fallback hosts its lazy slots too. The
        # streaming path flushes their thunks through the chunk emitter; here
        # nothing ran them, so a browser with neither WebSocket nor EventSource
        # got an empty <dj-lazy-slot>. See `_fill_lazy_slots`.
        if not getattr(self, "streaming_render", False):
            html = self._fill_lazy_slots(html)

        from .._lazy_containers import finalize_lazy_containers

        html = finalize_lazy_containers(self, html)
        if page_root:
            # Recheck after wrapping, stamping and lazy processing: these are
            # the bytes the browser will actually parse and mount.
            from django.core.exceptions import ImproperlyConfigured
            from .._render_provenance import RenderedHTML

            if not isinstance(html, RenderedHTML):
                raise ImproperlyConfigured(
                    "LiveView root provenance was lost during page processing."
                )
            self._rust_view.validate_prepared_page(html, list(html.root_spans))
        if prepared_page:
            # Commit exactly the VDOM captured with the delivered page; do not
            # execute stateful template selectors a second time.
            t0 = time.perf_counter()
            self._rust_view.commit_authenticated_page()
            self._pending_authenticated_page = False
            self._record_dj_model_fields_from_rust(self._rust_view)
            request._djust_timing["vdom_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            if not uses_legacy_exposure(self):
                from .._exposure_child_persistence import save_child_states

                save_child_states(self, request)
            self._cached_context = None
        response: HttpResponse
        if getattr(self, "streaming_render", False):
            response = self._make_streaming_response(html)
        else:
            response = HttpResponse(html)
        status = self.get_http_status()
        if status is not None:
            response.status_code = int(status)
        # ADR-038 D-b: the service worker must not persist an explicit page's
        # HTML in its shell cache. Legacy responses are unchanged.
        from .._exposure import service_worker_cache_eligible
        from ..security.service_worker import SW_CACHE_HEADER, SW_CACHE_NO_STORE

        if not service_worker_cache_eligible(self):
            response[SW_CACHE_HEADER] = SW_CACHE_NO_STORE
        return response

    def get_http_status(self) -> Optional[int]:
        """The HTTP status of the page's first render; ``None`` means 200.

        The HTTP render and the WebSocket mount come from the same state, but a
        socket frame has no status and an override of ``get()`` never runs on
        the socket (``djust.V013``), so a page that renders a "not found" or
        "forbidden" state had no clean way to answer 404 or 403. Override this:
        it is read after the render, so it can depend on what ``mount()`` and
        ``get_context_data()`` found::

            def get_http_status(self):
                return 404 if self.document is None else None

        A project that draws its own not-found state need not override this on
        every view. Set ``http_status_of_state`` once (on a base view) and the
        default reads the state the view already has::

            class AppView(LiveView):
                abstract = True
                http_status_of_state = {"not_found": 404, "gone": 410}

            class ArticleView(AppView):
                def mount(self, request, slug="", **kwargs):
                    self.state = "ready" if load(slug) else "not_found"

        A project that has several base views sets the map once, for all of
        them, in ``LIVEVIEW_CONFIG["http_status_of_state"]``. The view's own
        ``http_status_of_state`` wins and the config is read only for a view
        that declares none, so the two are never merged: a view that declares a
        map answers by that map alone, and one that declares ``{}`` opts out of
        the project's.

        ``state_attribute`` names the attribute that holds the state (``"state"``
        by default); only a ``str`` is looked up, so any other value, or a name
        the map does not hold, answers 200. An override still replaces all of
        this; ``manage.py check`` reports one (``djust.V025``) while a
        project-wide map is configured.

        Only the HTTP response uses it.
        """
        statuses = self.http_status_of_state
        if statuses is UNDECLARED_STATUS_MAP:
            # The view declares no map: the project-wide one (#3706). A view's own attribute, even
            # an empty one, wins; the two are never merged. The config validated it when it loaded.
            from ..config import config

            statuses = config.get("http_status_of_state") or {}
        if not statuses:
            return None
        state = getattr(self, self.state_attribute, None)
        if not isinstance(state, str):
            return None
        return statuses.get(state)

    def _fill_lazy_slots(self, html: str) -> str:
        """Run this page's lazily-deferred slots into ``html`` (#3252).

        ``{% live_render ... lazy=True %}`` renders a ``<dj-lazy-slot>``
        placeholder and stashes an async thunk that builds a self-contained
        fill envelope: an inert ``<template id="djl-fill-X">`` holding the
        child's HTML, plus an activator that performs the replacement. The
        envelope needs no transport, because ``50-lazy-fill.js`` fills from the
        template — and its `DOMContentLoaded` auto-scan already covers the
        activator running before the bundle defines ``lazyFill``.

        Over a WebSocket or SSE stream the consumer or the chunk emitter runs
        those thunks. On the plain page render (the page-POST fallback, for a
        browser with neither WebSocket nor ``EventSource``) nothing did, so the
        slot stayed empty even though the server could render it. Running them
        here makes that transport host its lazy slots like the others.

        Returns ``html`` unchanged when the page has no lazy slot. A slot whose
        envelope cannot be built is left empty and logged: the thunk wraps its
        OWN failures in a ``data-status="error"`` envelope (ADR-015), so
        reaching the handler means the envelope itself did not build, and that
        must not take the page down with it.
        """
        thunks = list(getattr(self, "_lazy_thunks", None) or [])
        self._lazy_thunks = []
        if not thunks:
            return html

        from asgiref.sync import async_to_sync

        envelopes: list[str] = []
        for view_id, thunk in thunks:
            try:
                envelopes.append(async_to_sync(thunk)().decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 — a slot must not break the page
                from .._exposure_diagnostics import log_failure_for

                # The thunk mounts and renders a child view, so its exception
                # can carry application values (ADR-038).
                log_failure_for(
                    logger,
                    (self,),
                    exc,
                    "Lazy slot %s could not be filled on the page-POST fallback",
                    view_id,
                )
        if not envelopes:
            return html

        block = "".join(envelopes)
        if "</body>" in html:
            return html.replace("</body>", block + "</body>", 1)
        return html + block

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
        """Async-streaming GET path, as one ``mount`` turn (ADR-044).

        The chunk producer this starts inherits the turn's context, so view
        code it runs after this returns still sees what middleware set.
        """
        from ..turns import app_chain_configured

        if not app_chain_configured():
            return await self._aget_turn_body(request, *args, **kwargs)
        from .._turn_ports import run_async_http_turn

        return await run_async_http_turn(
            request,
            lambda: self._aget_turn_body(request, *args, **kwargs),
            kind="mount",
            source="page",
            view=self,
            json_body=False,
        )

    async def _aget_turn_body(
        self, request: "HttpRequest", *args: Any, **kwargs: Any
    ) -> HttpResponse:
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
        # A 2xx status the page chose (get_http_status) survives the re-wrap.
        response.status_code = sync_response.status_code
        # Copy any cookies/headers the sync_response set (CSRF cookie etc.)
        # so we don't drop them by re-wrapping.
        for header, value in sync_response.items():
            if header.lower() in {"content-length", "content-type"}:
                continue
            response[header] = value
        for cookie in sync_response.cookies.values():
            response.cookies[cookie.key] = cookie

        return response

    def _prepare_child_ids(self) -> None:
        """Number this render's auto-assigned child ids from 1 (HTTP, #3104).

        Every HTTP request builds the view afresh, so ids from the process-wide
        counter never repeat between the page GET and the event POST that
        follows; per-render numbering makes them repeat, which is what lets
        an event be routed to the child it names. A page that does not use
        legacy exposure keeps the counter: its children are not routed over
        HTTP.
        """
        if uses_legacy_exposure(self) and hasattr(self, "_scope_child_ids_to_render"):
            self._scope_child_ids_to_render()

    def _http_embedded_child(self, request: "HttpRequest", view_id: Any) -> Optional[Any]:
        """The embedded child an HTTP event's ``view_id`` names, or ``None`` (#3104).

        Children register while the page renders, so the page is rendered once
        here (its output is not sent: a child's event answers with the child's
        own HTML). The child exists only if it passed what ``{% live_render %}``
        checks at registration: its view-level auth and its object permission,
        against this request. Only a legacy page's legacy child is routed over
        HTTP: an explicit-exposure child's turn is authorized against the mount
        binding the socket transports hold, which a stateless request has not.
        Anything else is refused by the caller, as it was before routing.
        """
        if type(view_id) is not str or not view_id or not uses_legacy_exposure(self):
            return None
        self._prepare_child_ids()
        with self._processor_context(request):
            # Attribute-form authority comes from the coherent full page,
            # including includes/extends; the normalized diff source is not
            # an independent registration source (#3252).
            from .._child_rendering import render_view_full_template
            from .._lazy_containers import mount_http_lazy

            self.get_template()
            self._defer_lazy_registration = True
            page_html = render_view_full_template(self, request)
            from .._lazy_containers import finalize_lazy_containers

            if getattr(self, "wrapper_template", None):
                # Passing a page back through a wrapper value is reinjection;
                # that value does not carry authored renderer authority.
                page_html = str(page_html)
            finalize_lazy_containers(self, page_html)
        child = mount_http_lazy(self, request, view_id)
        if child is None:
            child = self._get_all_child_views().get(view_id)
        if child is None or not uses_legacy_exposure(child):
            return None
        return child

    def _http_child_response(
        self,
        request: "HttpRequest",
        child: Any,
        view_id: str,
        event_name: str,
        *,
        cache_request_id: Any = None,
    ) -> HttpResponse:
        """Answer an event the child's handler has run (#3104).

        The shape the socket transports use for a child's event: an
        ``embedded_update`` carrying the child's own HTML, which the client
        morphs into the child's container. Its side channels (flash, page
        metadata, navigation) are the child's, and nothing of the page is
        rendered or saved: the event could not have changed it.
        """
        from ..websocket import render_embedded_child_html

        response: Dict[str, Any] = {"type": "embedded_update", "view_id": view_id}
        navigation_frames: List[Dict[str, Any]] = []
        if hasattr(child, "_drain_navigation"):
            for command in child._drain_navigation():
                navigation_frames.append(
                    {
                        "type": "navigation",
                        "action": command.get("type"),
                        **{k: v for k, v in command.items() if k != "type"},
                    }
                )
        if navigation_frames:
            response["_navigation"] = navigation_frames
        _drain_http_side_channels(child, response)
        if any(frame["action"] == "live_redirect" for frame in navigation_frames):
            # The event leaves the page: nothing is rendered or saved for it.
            return JsonResponse(response)

        # The page tree's public parameter contracts travel with the update, as
        # with every other render answer (ADR-036): the child's owners are part
        # of that tree, and a reply without them would drop the page's scope.
        contract_fields = _http_parameter_contract_fields(self, request)
        if contract_fields is None:
            return _contract_error_response()
        response.update(contract_fields)
        response["html"] = render_embedded_child_html(child)
        from .._lazy_containers import save_http_lazy

        save_http_lazy(self, request, view_id, child)
        response["event_name"] = event_name
        if cache_request_id:
            response["cache_request_id"] = cache_request_id
        return JsonResponse(response)

    def post(self, request: "HttpRequest", *args: Any, **kwargs: Any) -> HttpResponse:
        """Handle POST requests: the event's turn, then the answer's compression (#4108)."""
        return _compress_fallback_answer(request, self._post_turn(request, *args, **kwargs))

    def _post_turn(self, request: "HttpRequest", *args: Any, **kwargs: Any) -> HttpResponse:
        """Handle POST requests - event handling, as one turn (ADR-044).

        An ``event`` turn (``http``/``client``), or a ``mount``/``lazy`` turn
        for a lazy slot's mount. The turn-middleware chain runs around
        :meth:`_post_turn_body` when ``LIVEVIEW_MIDDLEWARE`` lists any.
        """
        from ..turns import app_chain_configured

        if not app_chain_configured():
            return self._post_turn_body(request, *args, **kwargs)
        from .._lazy_containers import MOUNT_EVENT
        from .._turn_ports import run_http_turn

        event_name: Any = None
        params: Any = None
        try:
            data = json.loads(request.body) if request.body else {}
            if isinstance(data, dict):
                event_name = data.get("event") or request.headers.get("X-Djust-Event")
                params = data.get("params") if "event" in data else data
        except Exception:  # noqa: BLE001 - a best-effort label; the body re-parses and answers
            event_name = request.headers.get("X-Djust-Event")
        if not isinstance(event_name, str):
            event_name = None
        lazy = event_name == MOUNT_EVENT
        return run_http_turn(
            request,
            lambda: self._post_turn_body(request, *args, **kwargs),
            kind="mount" if lazy else "event",
            source="lazy" if lazy else "client",
            view=self,
            event=event_name,
            params=MappingProxyType(params) if isinstance(params, dict) else None,
            json_body=True,
        )

    def _post_turn_body(self, request: "HttpRequest", *args: Any, **kwargs: Any) -> HttpResponse:
        """Handle POST requests - event handling"""

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
            # Django's middleware chain has processed this request for this view.
            from ..auth._login_required_middleware import note_http_dispatch

            note_http_dispatch(self, request)

            # Navigation the handler queued (``live_redirect`` / ``live_patch``),
            # as ``navigation`` frames in the WebSocket shape. Filled right after
            # the handler runs; the client's ``handleNavigation`` consumes each
            # entry of the answer's ``_navigation`` list (#3303).
            navigation_frames: List[Dict[str, Any]] = []

            def _inject_side_channels(resp_data: Dict[str, Any]) -> None:
                if navigation_frames:
                    resp_data["_navigation"] = navigation_frames
                _drain_http_side_channels(self, resp_data)

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
                # event=True: what only the view's post() runs applies here.
                redirect_url = check_view_auth(self, request, event=True)
            except PermissionDenied:
                # check_view_auth *raises* for an authenticated user who lacks
                # the view's permission_required (only the unauthenticated case
                # returns a URL). Uncaught, the broad handler below would report
                # a permission failure as a 500.
                logger.warning(
                    "Auth denied for %s: missing view permission (HTTP POST)",
                    type(self).__name__,
                )
                return JsonResponse(
                    {"error": "Permission denied", "code": "permission_denied"}, status=403
                )
            if redirect_url:
                logger.info(
                    "Auth denied for %s: login required (HTTP POST)",
                    type(self).__name__,
                )
                return JsonResponse({"redirect": redirect_url}, status=403)

            # #4108: a resync request. The client's page no longer matches what
            # the server last sent it (a patch answer did not fit its DOM), and
            # it asks for the page as the saved state renders it now. No event:
            # the view is restored as for any event, every view-level and
            # object-level check below runs, no handler does, and the answer is
            # the whole page with its digest. (The page GET is no substitute:
            # it is a new page load, which mounts again and saves that state.)
            render_only = request.headers.get(RESYNC_HEADER) == "1"

            data = json.loads(request.body) if (request.body or not render_only) else {}
            if render_only:
                data = {"event": RESYNC_EVENT, "params": {}}
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
            if not render_only and not is_safe_event_name(event_name):
                return JsonResponse({"error": "Invalid event name"}, status=400)

            legacy_exposure = uses_legacy_exposure(self)

            # ADR-035: bound before mount() so the route, not the body, supplies it.
            bind_route = getattr(self, "_djust_bind_route_kwargs", None)
            if callable(bind_route):
                bind_route(kwargs)

            self._initialize_temporary_assigns()

            # Run on_mount hooks (auth guards, etc.) before mount
            hook_redirect = run_on_mount_hooks(self, request, **kwargs)
            if hook_redirect:
                return JsonResponse({"redirect": hook_redirect}, status=403)

            # True when mount() ran on a view with nothing restored over it: the
            # work it (and handle_params) queued has to finish before the event
            # is applied. It runs right before the handler, after every refusal
            # (object permission, handler permission, rate limit, parameter
            # validation), so a refused request starts none of it.
            mount_work_pending = False
            # Whether an explicit view's declared state was put back (#3989).
            explicit_restored = False
            # One budget for every wait on background work in this request
            # (#3634); ``None`` unless ``http_fallback_background_timeout`` is set.
            bg_budget = _http_background_budget()
            from .. import _automatic_state
            from .._exposure_sessions import load_saved_view

            # The 1.3 rule (#4107): a view whose state is saved is REBUILT from
            # it, and mount() does not run again; mount() runs only when
            # nothing is saved, or when something of the view could not be
            # saved (an unsaveable attribute, a component, work mount() queued),
            # and then the saved state goes back over what mount() built. What
            # is put back was validated by the store; nothing the client sent
            # is read here.
            # The tab the page says it is (#4106). It selects one of this
            # session's own states; none or a malformed one selects nothing.
            self.__dict__["_djust_tab"] = _automatic_state.tab_id(
                request.headers.get(_automatic_state.TAB_HEADER)
            )
            saved_view = load_saved_view(self, request)

            def _assign(values: Dict[str, Any]) -> None:
                for key, value in values.items():
                    safe_setattr(self, key, value, allow_private=False, raise_on_blocked=True)

            if saved_view.must_mount:
                call_hook_sync(self.mount, request, **kwargs)
                cast(Any, self)._snapshot_user_private_attrs()
                mount_work_pending = True
                # Declared persist="server" fields, then the URL: the URL this
                # request carries wins over a declared field (#3624), as on the
                # socket mount.
                _assign(saved_view.declared)
                _run_http_handle_params(self, request)
            else:
                # Rebuilt from the saved state, no mount(). handle_params()
                # still runs, because 2.0 pins that the URL THIS request
                # carries wins over a declared persist="server" field (#3624,
                # ``test_http_fallback_handle_params_3624``): the saved state
                # goes on first so the hook can read it, and the undeclared
                # part goes back again below.
                _assign(saved_view.declared)
                _assign(saved_view.public)
                _automatic_state.apply_private(self, saved_view.private)
                _run_http_handle_params(self, request)
            # The rest of the saved state goes over what mount() and
            # handle_params() built, as 1.3 put it back: what a handler set (a
            # tab the URL does not carry) is not reset by handle_params().
            # ``put_back`` decides what goes back (never a saved model dict over
            # the live instance mount() just loaded) and restores the private
            # attributes and the components' state.
            _assign(
                _automatic_state.put_back(
                    self,
                    saved_view.public,
                    saved_view.private,
                    saved_view.components,
                    over_mount=saved_view.must_mount,
                )
            )
            # The view is as the next handler finds it: a save records as
            # changed every name that differs from this.
            _automatic_state.settle(self)
            explicit_restored = saved_view.found and bool(
                saved_view.declared
                or saved_view.public
                or saved_view.private
                or saved_view.components
            )

            self._assign_component_ids()

            # --- Authorization layer 3 of 3: object-level (ADR-017) ----------
            # Runs here, after mount() and the declared-state restore above, because
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
                return JsonResponse(
                    {"error": "Access denied for this object.", "code": "permission_denied"},
                    status=403,
                )

            # A view whose saved state was put back on top of mount() reads
            # again what that state does not hold (#3989). After the object
            # check, so it never reads for a denied object.
            if explicit_restored:
                handle_restore = getattr(self, "handle_restore", None)
                if callable(handle_restore):
                    call_hook_sync(handle_restore, request)

            # Call the event handler — only @event_handler-decorated methods
            # can be invoked via POST (matches WS security)
            t_handler_ms = 0.0
            observation_before = None
            # #4108: whether the pre-handler render is the page the client shows.
            baseline_matched = False
            # A view object that has rendered before this request (a test that
            # calls get() and post() on one instance; ``as_view()`` never does)
            # already holds a diff baseline. It keeps the answer it always got:
            # the patches from that baseline, numbered by its renderer.
            reused_renderer = getattr(self, "_rust_view", None) is not None
            # ADR-031: an event carrying ``component_id`` targets the
            # registered component, as ``runtime._dispatch_component_event``
            # does over WebSocket (#1646 — the HTTP fallback must not differ).
            owner: Any = self
            # #3104: an event carrying another view's ``view_id`` belongs to an
            # embedded ``{% live_render %}`` child. It is routed to that child,
            # as the socket runtime routes it: the page renders once to register
            # its children (the same ids on every request, ``_prepare_child_ids``),
            # and the child's handler is then validated, authorized and run
            # exactly as the page's own would be. An id that names no child of
            # this page (forged, stale, an explicit-exposure child, which this
            # transport does not route) is refused, not run on the parent.
            # Same rule as ``ViewRuntime`` (pop, then route only a truthy id
            # other than this view's own), so the transports cannot disagree.
            view_id = None
            if isinstance(params, dict):
                params = dict(params)
                view_id = params.pop("view_id", None)
            child_view = None
            if view_id and view_id != getattr(self, "_view_id", None):
                try:
                    child_view = self._http_embedded_child(request, view_id)
                except PermissionDenied:
                    # A child the render registers checks its own auth and object
                    # permission (``{% live_render %}``): refused, as the page's
                    # own layers are.
                    logger.warning(
                        "Auth denied for %s: embedded child permission (HTTP POST)",
                        type(self).__name__,
                    )
                    return JsonResponse(
                        {"error": "Permission denied", "code": "permission_denied"}, status=403
                    )
                if child_view is None:
                    logger.warning(
                        "HTTP POST refused event '%s' for embedded view %s on %s",
                        event_name,
                        sanitize_for_log(str(view_id)),
                        type(self).__name__,
                    )
                    return JsonResponse({"error": "Embedded view not found"}, status=400)
                owner = child_view
                # The page was mounted (and rendered) only to register its
                # children. What it queued while doing so is not this event's
                # work: discard it, so the work that runs after the child's
                # handler is only what the handler queued.
                _take_queued_background_work(self)
                from .._lazy_containers import MOUNT_EVENT

                if event_name == MOUNT_EVENT and view_id in getattr(
                    self, "_http_lazy_containers", {}
                ):
                    return self._http_child_response(request, child_view, view_id, event_name)
            component_id = (
                params.get("component_id")
                if isinstance(params, dict) and child_view is None
                else None
            )
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
            handler = None if render_only else getattr(owner, event_name, None)
            if render_only:
                # The page's own reads (``assign_async`` / ``start_async`` in
                # mount() or handle_params()) finish before the render, as they
                # do before an event's handler: the page the client asks for
                # is the loaded one.
                if mount_work_pending:
                    _run_http_background_work(self, bg_budget)
                    mount_work_pending = False
            elif handler is not None and is_push_only(owner, event_name, handler):
                # Server-push only (#3002): to a browser it is a method that
                # does not exist, as on the WebSocket and SSE paths.
                handler = None
            if not render_only and (handler is None or not callable(handler)):
                # Same answer for the page view as for a component: an event
                # that resolves to no callable must not fall through to a
                # render and a state save as if a handler had run (the socket
                # transports refuse it with an error frame).
                logger.warning(
                    "HTTP POST handler '%s' not found on %s",
                    event_name,
                    component_id if owner is not self else type(self).__name__,
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
                    return JsonResponse(
                        {"error": "Permission denied", "code": "permission_denied"}, status=403
                    )
                # --- Per-handler @rate_limit -------------------------------
                # The WS/SSE chokepoint (_validate_event_security) and the HTTP
                # API both enforce the handler's @rate_limit against the SHARED
                # per-caller store; this transport enforced nothing, so a
                # handler throttled on every other transport was unthrottled
                # here, and POST did not consume the shared budget (F27's
                # "regardless of transport" was therefore false for this
                # path). Same helper as the HTTP API, so the two cannot drift
                # apart again.
                from ..rate_limit import http_handler_rate_check

                if not http_handler_rate_check(request, owner, event_name, handler):
                    logger.warning(
                        "HTTP POST rate-limited handler '%s' on %s",
                        event_name,
                        type(self).__name__,
                    )
                    return JsonResponse(
                        {"error": "Rate limit exceeded", "code": "rate_limited"}, status=429
                    )
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
                # Every refusal above has passed: the reads the page makes after
                # its first render (``assign_async``, ``start_async`` in mount()
                # or handle_params()) finish now, so the event meets the loaded
                # page and the answer does not bring back a loading state
                # (#3624). Not for an event with no handler: that one passes no
                # @rate_limit check, so it must not start the page's reads.
                if mount_work_pending:
                    _run_http_background_work(self, bg_budget)
                    mount_work_pending = False
                # #4108: the view is now what the page GET (or the previous
                # answer) rendered. Render a copy of it before the handler; if
                # the client shows that render (it echoed its digest and its DOM
                # has that shape), the render after the handler diffs against it
                # and the answer is patches.
                # After every refusal, so a refused request renders nothing;
                # for the page's own handlers only (a component or embedded
                # child event is answered as before). Not for an ``@cache``
                # request: the client stores that answer's patches and replays
                # them later without asking, which no digest can vouch for.
                if owner is self and not reused_renderer and not params.get("_cacheRequestId"):
                    from .._http_fallback_baseline import prepare_baseline

                    baseline_matched = prepare_baseline(self, request)
                from ..components._interactive import DropdownMenu

                if isinstance(owner, DropdownMenu) and event_name == "observe_toggle":
                    from ..websocket import _snapshot_assigns, _compute_changed_keys

                    observation_before = _snapshot_assigns(self)
                t0_handler = time.perf_counter()
                if inspect.iscoroutinefunction(handler):
                    from asgiref.sync import async_to_sync

                    async_to_sync(handler)(*call_args, **call_kwargs)
                else:
                    from ..sync_turn import run_sync_step

                    run_sync_step(owner, "event", handler, *call_args, **call_kwargs)
                t_handler_ms = (time.perf_counter() - t0_handler) * 1000
                # The handler's ``start_async`` / ``@background`` work runs here,
                # before the render, so the one answer carries its result (#3624).
                _run_http_background_work(owner, bg_budget)
                if owner is not self:
                    _run_http_background_work(self, bg_budget)

            if child_view is not None:
                return self._http_child_response(
                    request,
                    child_view,
                    view_id,
                    event_name,
                    cache_request_id=params.get("_cacheRequestId"),
                )

            # A queued ``live_redirect`` / ``live_patch`` goes out with this
            # answer, as the WebSocket flushes it at turn end. The HTTP
            # fallback drained only flash and page metadata, so the navigation
            # was dropped (#3303). A ``live_redirect`` leaves this view, so
            # nothing is rendered or saved for it: after ``logout()`` the
            # request is anonymous and ``get_context_data()`` may not run. A
            # ``live_patch`` stays on the view, so its render still goes out.
            if hasattr(self, "_drain_navigation"):
                for command in self._drain_navigation():
                    navigation_frames.append(
                        {
                            "type": "navigation",
                            "action": command.get("type"),
                            **{k: v for k, v in command.items() if k != "type"},
                        }
                    )
            if any(frame["action"] == "live_redirect" for frame in navigation_frames):
                redirect_response: Dict[str, Any] = {}
                _inject_side_channels(redirect_response)
                return JsonResponse(redirect_response)

            # Explicit and legacy views alike (#4107).
            from .._exposure_sessions import StateLimitExceeded, save_server_state

            try:
                save_server_state(self, request)
            except StateLimitExceeded as refused:
                # Refused before writing (#4095): the stored state is the
                # last one that fit, and the next request restores it. This
                # request's view is discarded, so nothing else to put back.
                logger.warning(
                    "HTTP event state save refused: field %s over its %s limit",
                    sanitize_for_log(refused.field),
                    refused.limit,
                )
                return JsonResponse(
                    {"error": refused.user_message, "code": "state_too_large"}, status=413
                )

            # Fields every response of this turn carries besides its payload.
            response_fields: Dict[str, Any] = {}

            if (
                observation_before is not None
                and not _compute_changed_keys(observation_before, _snapshot_assigns(self))
                and not getattr(self, "_force_full_html", False)
                and not getattr(self, "_async_tasks", None)
            ):
                noop_response = {"type": "noop", "event_name": event_name, **response_fields}
                _inject_side_channels(noop_response)
                return JsonResponse(noop_response)

            # A handler (view or ``component_id`` route) that set
            # ``self._skip_render = True`` asked for no render this turn. The
            # WebSocket / SSE routes resolve that through
            # ``_resolve_skip_render`` (#2834, #2924); the HTTP fallback never
            # consulted it, so the same flag rendered here and was never reset
            # (#3038, the #1646 parallel-path class). An explicit view's
            # declared state above is already saved. The answer is an empty patch list with no ``version``: nothing was
            # rendered, so the client's VDOM cursor must not move. Side channels
            # (flash, page metadata) still go out, as they do with the WS noop.
            # No ``cache_request_id``: the WS/SSE noop carries none, so a
            # skipped turn is never stored as an ``@cache`` hit on any transport.
            from ..websocket import _resolve_skip_render

            if _resolve_skip_render(self):
                skip_response: Dict[str, Any] = {"patches": [], **response_fields}
                contract_fields = _http_parameter_contract_fields(self, request)
                if contract_fields is None:
                    return _contract_error_response()
                skip_response.update(contract_fields)
                _inject_side_channels(skip_response)
                return JsonResponse(skip_response)

            # Apply context processors so the render includes auth context
            # (user, perms, messages, etc.). Without this, template conditionals
            # like {% if user.is_authenticated %} evaluate to false and the HTTP
            # fallback returns logged-out HTML. Fixes #705.
            # Unified via _processor_context context manager (#717).
            with self._processor_context(request):
                from .._child_rendering import render_view_with_diff

                t0_render = time.perf_counter()
                self._prepare_child_ids()
                cold_children = not getattr(self, "_explicit_child_vdom_tracking", False)
                html, patches_json, version = render_view_with_diff(self, request)
                if cold_children and getattr(self, "_explicit_child_vdom_tracking", False):
                    response_fields["owned_children"] = [
                        [child._view_id, node_id]
                        for node_id, child in self._explicit_child_vdom_bindings.items()
                    ]
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

            import json as json_module

            PATCH_THRESHOLD = 100

            # #4108: every render answer names the digest of the render it
            # delivers; the client echoes it with its next fallback POST.
            from .._render_digest import ANSWER_FIELD, render_token

            answer_digest = render_token(self, request, html)
            if answer_digest:
                response_fields[ANSWER_FIELD] = answer_digest

            cache_request_id = params.get("_cacheRequestId")

            # Inject debug info for the debug panel (HTTP-only mode)
            from ..config import debug_panel_enabled

            def _inject_debug(resp_data: Dict[str, Any]) -> None:
                if debug_panel_enabled():
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

                # #4108: patches exist only when the pre-handler render was
                # the page the client shows (``baseline_matched``); a fresh
                # renderer has nothing to diff against. They carry no
                # ``version``: each request numbers its own renders, so a
                # counter says nothing here, and the digest the client echoed
                # is what tied these patches to its DOM. They are addressed by
                # path (``path_addressed``). Zero patches is a render with no
                # DOM change: an empty list, and the same digest.
                if reused_renderer and 0 <= patch_count <= PATCH_THRESHOLD:
                    response_data = {
                        "patches": patches,
                        "version": version,
                        **contract_fields,
                        **response_fields,
                    }
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    _inject_side_channels(response_data)
                    _inject_debug(response_data)
                    return JsonResponse(response_data)
                if baseline_matched and 0 <= patch_count <= PATCH_THRESHOLD:
                    from .._render_digest import path_addressed

                    response_data = {
                        "patches": path_addressed(patches),
                        **contract_fields,
                        **response_fields,
                    }
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    _inject_side_channels(response_data)
                    _inject_debug(response_data)
                    return JsonResponse(response_data)
                else:
                    # More patches than a whole page is worth: the whole page,
                    # numbered as every whole-page answer of this transport is
                    # (v1; the client re-bases on it).
                    self._rust_view.reset()
                    response_data = {
                        "html": html,
                        "version": version if reused_renderer else 1,
                        **contract_fields,
                        **response_fields,
                    }
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    _inject_side_channels(response_data)
                    _inject_debug(response_data)
                    return JsonResponse(response_data)
            else:
                response_data = {
                    "html": html,
                    "version": version,
                    **contract_fields,
                    **response_fields,
                }
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
            from .._exposure import ExposureConfigurationError
            from .._exposure_diagnostics import diagnostics_policy_allows

            if isinstance(e, ExposureConfigurationError):
                # A configuration error is framework-authored and value-free:
                # it reaches the developer as on the GET path
                # (``protected_http_outcome``), not as a generic answer (#4092).
                raise
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
