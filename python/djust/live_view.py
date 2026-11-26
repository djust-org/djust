"""
LiveView base class and decorator for reactive Django views
"""

import logging
import weakref
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Set, Union, cast

if TYPE_CHECKING:
    from .auth.any_of import PermissionRequired

from django.utils.decorators import classonlymethod
from django.views import View

from ._class_snapshot import namespace as _class_namespace
from ._mapping import mapping_values
from ._context_provider import ContextProviderMixin  # noqa: F401  # re-exported for back-compat
from .change_detection import CHANGE_DETECTION_IGNORED, deep_digest, fingerprints_by_content
from .serialization import (  # noqa: F401
    DjangoJSONEncoder,
    StateRoundtripJSONEncoder,
    decode_private_model_refs,
    decode_state_roundtrip,
    encode_private_model_refs,
)
from .session_utils import (  # noqa: F401
    DEFAULT_SESSION_TTL,
    cleanup_expired_sessions,
    get_session_stats,
    _jit_serializer_cache,
    _get_model_hash,
    clear_jit_cache,
    Stream,
)

from .mixins import (
    AsyncWorkMixin,
    ServerPushMixin,
    StreamsMixin,
    StreamingMixin,
    TemplateMixin,
    ComponentMixin,
    JITMixin,
    ContextMixin,
    RustBridgeMixin,
    HandlerMixin,
    RequestMixin,
    PostProcessingMixin,
    ModelBindingMixin,
    PushEventMixin,
    NavigationMixin,
    FlashMixin,
    PageMetadataMixin,
    LayoutMixin,
    CommandsMixin,
    WaiterMixin,
    NotificationMixin,
    StickyChildRegistry,
    ActivityMixin,
)
from .mixins.request import UNDECLARED_STATUS_MAP

from ._rust import (
    RustLiveView,
    extract_template_variables,  # noqa: F401 — re-exported, used by JIT and template tests
)

# Configure logger
logger = logging.getLogger(__name__)

__all__ = [
    "LiveView",
    "live_view",
    "DjangoJSONEncoder",
    "DEFAULT_SESSION_TTL",
    "cleanup_expired_sessions",
    "get_session_stats",
    "_jit_serializer_cache",
    "_get_model_hash",
    "clear_jit_cache",
    "Stream",
    "extract_template_variables",
]


# ``_FRAMEWORK_INTERNAL_ATTRS``: the framework names on a view's instance dict
# that are not user state. Exposure-flip plan row 10 (section 5, G1) split it:
# the change-detection skip list is ``change_detection.CHANGE_DETECTION_IGNORED``
# (this set, same membership) and the reserved ``state()`` names are
# ``_exposure.reserved_state_names()``. This name stays for the
# ``_snapshot_user_private_attrs`` capture and the exposure inventory.
_FRAMEWORK_INTERNAL_ATTRS: frozenset = CHANGE_DETECTION_IGNORED


def _descriptor_fields(cls: type) -> Dict[str, tuple]:
    """``{public_name: (kind, slot_name)}`` for the descriptor-backed fields a
    view class declares: ``state()`` fields (kind ``"state"``, slot
    ``_state_<name>``) and class-level components (kind ``"component"``, slot
    ``_component_<name>``).

    Their values live in ``_``-prefixed instance slots, so every walk that
    skips ``_`` keys (dirty tracking, #2956/#2912) or treats them as user
    private state (#2959) needs this map to see them for what they are. The
    nearest definition in the MRO wins, as attribute lookup does.
    """
    cached = cls.__dict__.get("_djust_descriptor_fields_cache")
    if cached is not None:
        return cast(Dict[str, tuple], cached)
    from .components.base import LiveComponent as _DescriptorComponent

    # Built into a new dict from namespace SNAPSHOTS, then published with one
    # setattr: on a free-threaded build another thread may be writing a
    # first-use cache to one of these classes right now (#3151).
    fields: Dict[str, tuple] = {}
    for klass in reversed(cls.__mro__):
        for name, value in _class_namespace(klass).items():
            # Type checks only: a class attribute may be lazy
            # (``SimpleLazyObject``, whose ``__class__`` is proxied, so not
            # even ``isinstance``) and must not be evaluated here.
            if getattr(type(value), "_djust_state_field", False):
                slot = getattr(value, "attr_name", None)
                if slot:
                    fields[name] = ("state", slot)
                    continue
            if issubclass(type(value), _DescriptorComponent):
                slot = value.__dict__.get("_descriptor_storage_key")
                if slot and getattr(type(value), "State", None) is not None:
                    fields[name] = ("component", slot)
                    continue
            # A plain attribute further down the MRO shadows the descriptor.
            fields.pop(name, None)
    try:
        setattr(cls, "_djust_descriptor_fields_cache", fields)
    except (AttributeError, TypeError):  # pragma: no cover — immutable class
        # Uncacheable (an immutable class): recomputed on every call instead.
        logger.debug("descriptor-field map not cached on %s", cls.__name__)
    return fields


def _holds_model(value: Any) -> bool:
    """Does ``value`` hold a Django model instance, at any depth?"""
    from django.db import models

    if isinstance(value, models.Model):
        return True
    if isinstance(value, dict):
        return any(_holds_model(v) for v in mapping_values(value))
    if isinstance(value, (list, tuple)):
        return any(_holds_model(v) for v in value)
    return False


#: The wrappers :func:`_protect_http_entry` returns, by identity. A wrapper made
#: with ``functools.wraps`` copies the wrapped callable's ``__dict__``, so an
#: attribute marker would be inherited by any decorator applied around it;
#: membership here cannot be.
_PROTECTED_HTTP_ENTRIES: "weakref.WeakSet[Any]" = weakref.WeakSet()


def is_protected_http_entry(callback: Any) -> bool:
    """Whether ``callback`` is the error-handling wrapper ``as_view()`` returned.

    That wrapper is how an explicit view reports a failure without values
    (ADR-038 D-a). It is not an access gate, and code that reads a route's
    callback to decide whether a decorator guards it (the client route map,
    #1758) must not mistake it for one. Only the exact object counts: a
    decorator applied around it is a different callable and is not in the set.
    """
    try:
        return callback in _PROTECTED_HTTP_ENTRIES
    except TypeError:  # not weak-referenceable: not one of ours
        return False


def _protect_http_entry(cls: type, view: Callable[..., Any]) -> Callable[..., Any]:
    """ADR-038 D-a: a class's HTTP entry fails with a value-free 500.

    The constructor, ``setup``, ``on_mount`` hooks, ``mount``, ``handle_params``,
    ``get_context_data`` and render all run under Django's view callable with no
    protected scope, so under ``DEBUG`` Django's technical 500 page would show
    the exception and its frames' locals. The class owns the policy before an
    instance exists.
    """
    from asgiref.sync import iscoroutinefunction, markcoroutinefunction

    from ._exposure_diagnostics import (
        diagnostic_scope,
        diagnostics_allowed,
        protected_http_outcome,
        protected_server_error,
        restrict_diagnostics,
        server_exception_detail_enabled,
    )

    if iscoroutinefunction(view):
        from asgiref.sync import sync_to_async

        @wraps(view)
        async def protected_async(request: Any, *args: Any, **kwargs: Any) -> Any:
            with diagnostic_scope():
                restrict_diagnostics(cls)
                try:
                    return await view(request, *args, **kwargs)
                except Exception as exc:
                    outcome = protected_http_outcome(exc)
                    if diagnostics_allowed() or outcome == "raise":
                        raise
                    # #4180: the server-side opt-in keeps the real exception for
                    # the log and the signal; the response stays generic.
                    failure = exc if server_exception_detail_enabled() else None
                    del exc
            # Outside the except block: the signal's exception has no context.
            return await sync_to_async(protected_server_error)(
                request, logger, outcome, exc=failure
            )

        markcoroutinefunction(protected_async)
        _PROTECTED_HTTP_ENTRIES.add(protected_async)
        return protected_async

    @wraps(view)
    def protected(request: Any, *args: Any, **kwargs: Any) -> Any:
        with diagnostic_scope():
            restrict_diagnostics(cls)
            try:
                return view(request, *args, **kwargs)
            except Exception as exc:
                outcome = protected_http_outcome(exc)
                if diagnostics_allowed() or outcome == "raise":
                    raise
                failure = exc if server_exception_detail_enabled() else None
                del exc
        return protected_server_error(request, logger, outcome, exc=failure)

    _PROTECTED_HTTP_ENTRIES.add(protected)
    return protected


def reserved_state_field_error(view_class: type, owner: type, name: str) -> Exception:
    """The error for a ``state()`` field declared under a reserved name (#4070)."""
    from ._exposure import ExposureConfigurationError

    where = (
        ""
        if owner is view_class
        else " (declared on %s.%s)"
        % (
            owner.__module__,
            owner.__qualname__,
        )
    )
    return ExposureConfigurationError(
        "%s.%s declares a state() field named %r%s, a name djust reserves for a "
        "framework setting, a view method or per-request identity. Rename the field, "
        "for example to %r."
        % (view_class.__module__, view_class.__qualname__, name, where, "%s_value" % name)
    )


def _refuse_reserved_state_names(cls: type) -> None:
    """Refuse a ``state()`` field ``cls`` declares under a reserved name (#4070).

    Reads the fields the way the exposure contract compiles them: most-derived
    first across the MRO, so a field a plain mixin declares is refused on the
    view that uses it, and a mixin field a subclass replaces with a plain value
    is not a field. Runs when the class is created (``__init_subclass__``) and
    again on the mount path (``_params_form._refuse_removed_configuration``),
    which catches what class creation cannot: a mixin whose
    ``__init_subclass__`` skips ``super()``, or a ``state()`` assigned to the
    class afterwards. ``StateProperty.__set_name__`` refuses a field in a view's
    own class body even when ``__init_subclass__`` does not run.
    """
    from ._exposure import reserved_state_names
    from ._state import StateProperty

    reserved = reserved_state_names()
    seen: set[str] = set()
    for owner in cls.__mro__:
        for name, value in _class_namespace(owner).items():
            if name in seen:
                continue
            seen.add(name)
            if name in reserved and issubclass(type(value), StateProperty):
                raise reserved_state_field_error(cls, owner, name)


def _warn_ignored_sticky_update_inputs(cls: type) -> None:
    """Warn when a class states ``sticky_update_inputs`` where it is ignored (#4181).

    Only a sticky child uses it: on a non-sticky child it does nothing.
    """
    if not cls.__dict__.get("sticky_update_inputs"):
        return
    if getattr(cls, "sticky", False) is True:
        return
    import warnings

    warnings.warn(
        "%s.%s sets sticky_update_inputs, which only an explicit-exposure sticky "
        "child (sticky = True) uses; it is ignored here." % (cls.__module__, cls.__qualname__),
        UserWarning,
        stacklevel=3,
    )


class LiveView(
    ContextProviderMixin,
    StreamsMixin,
    StreamingMixin,
    TemplateMixin,
    ComponentMixin,
    JITMixin,
    ContextMixin,
    RustBridgeMixin,
    HandlerMixin,
    RequestMixin,
    PostProcessingMixin,
    ModelBindingMixin,
    PushEventMixin,
    NavigationMixin,
    FlashMixin,
    PageMetadataMixin,
    LayoutMixin,
    CommandsMixin,
    WaiterMixin,
    AsyncWorkMixin,
    ServerPushMixin,
    NotificationMixin,
    StickyChildRegistry,
    ActivityMixin,
    View,
):
    """
    Base class for reactive LiveView components.

    Usage:
        class CounterView(LiveView):
            template_name = 'counter.html'

            # A declared state() field renders in the template; other
            # attributes do not (explicit exposure, the default).
            count = state(0)

            @event_handler()
            def increment(self):
                self.count += 1

            @event_handler()
            def decrement(self):
                self.count -= 1

    ``self.count`` stays on the server between events of one connection. To
    keep it across a reconnect or send it to the browser as data, declare it
    with ``state()`` (see the explicit exposure guide).

    Memory Optimization with temporary_assigns:
        For views with large collections (chat messages, feed items, etc.),
        use temporary_assigns to clear data from server memory after each render.

        class ChatView(LiveView):
            template_name = 'chat.html'
            temporary_assigns = {'messages': []}  # Clear after each render

            def mount(self, request, **kwargs):
                self.messages = Message.objects.all()[:50]

            def handle_new_message(self, content):
                msg = Message.objects.create(content=content)
                self.messages = [msg]  # Only new messages sent to client

        IMPORTANT: When using temporary_assigns, use dj-update="append" in your
        template (the container and each item need an ``id``) so the new items
        are added after the ones already on the page instead of replacing them:

            <ul dj-update="append" id="messages">
                {% for msg in messages %}
                    <li id="msg-{{ msg.id }}">{{ msg.content }}</li>
                {% endfor %}
            </ul>

    Streams API (recommended for collections):
        For a more ergonomic API, use streams instead of temporary_assigns:

        class ChatView(LiveView):
            template_name = 'chat.html'

            def mount(self, request, **kwargs):
                self.stream('messages', Message.objects.all()[:50])

            def handle_new_message(self, content):
                msg = Message.objects.create(content=content)
                self.stream_insert('messages', msg)

        Template:
            <ul dj-stream="messages">
                {% for msg in streams.messages %}
                    <li id="messages-{{ msg.id }}">{{ msg.content }}</li>
                {% endfor %}
            </ul>

    Background Work with start_async():
        For long-running operations (LLM calls, file processing), use start_async()
        to run work in the background without blocking the UI:

        class SpecGeneratorView(LiveView):
            template_name = 'generator.html'

            def mount(self, request, **kwargs):
                self.generating = False
                self.spec = ""
                self.error = None

            @event_handler
            def generate(self, prompt: str = "", **kwargs):
                self.generating = True  # Show loading state immediately
                self.start_async(self._generate_spec, prompt=prompt, name="generation")

            def _generate_spec(self, prompt: str):
                '''Runs in background thread.'''
                self.spec = call_llm_api(prompt)  # Slow operation
                self.generating = False
                # View auto-re-renders when this completes

            def handle_async_result(self, name: str, result=None, error=None):
                '''Optional: handle completion or errors.'''
                if error:
                    self.error = f"Generation failed: {error}"
                    self.generating = False

            @event_handler
            def cancel_generation(self, **kwargs):
                self.cancel_async("generation")
                self.generating = False

        Or use the @background decorator for simpler syntax:

        class SimpleGeneratorView(LiveView):
            template_name = 'generator.html'

            @event_handler
            @background
            def generate(self, prompt: str = "", **kwargs):
                '''Entire handler runs in background.'''
                self.generating = True
                self.spec = call_llm_api(prompt)
                self.generating = False

        Multiple concurrent tasks are supported with named tasks. Phoenix LiveView users
        will find this API familiar.
    """

    template_name: Optional[str] = None
    template: Optional[str] = None
    tick_interval: Optional[int] = None  # Periodic tick in ms (e.g. 2000 for 2s)
    # #4108: over the page-POST fallback an event is answered with patches when
    # the server can show the browser displays the render it diffs against,
    # which costs a second render of the view per event (``get_context_data()``
    # runs twice). ``False`` keeps the whole-page answer and the single render
    # for this view; ``LIVEVIEW_CONFIG["http_fallback_patches"]`` does so for all.
    http_fallback_patches: bool = True
    # Scoped server push (#3004): the scope(s) this session receives
    # ``push_to_view(..., scope=...)`` for — a str or int (a room, a document
    # id), a list/tuple/set of them (at most 64), or None (default: only
    # view-wide pushes). Usually set in mount(); reassigning it in a handler,
    # push hook, handle_tick or handle_info moves the session.
    push_scope: Any = None

    # Class-level marker for abstract base LiveView classes (#1605).
    # When a subclass sets ``abstract = True`` on its own class body, the djust
    # system checks (V001 missing template_name, V005 not in allowed modules,
    # and the other per-class V/Q checks) skip that class.
    # Not inherited: the check consults ``cls.__dict__.get("abstract")``, so
    # subclasses of an abstract base are still validated as concrete unless
    # they redeclare ``abstract = True`` themselves. Mirrors Django's
    # ``Meta.abstract`` model semantics.
    abstract: bool = False

    # Memory optimization: assigns to clear after each render
    # Format: {'assign_name': default_value, ...}
    # Example: {'messages': [], 'feed_items': [], 'notifications': []}
    temporary_assigns: Dict[str, Any] = {}

    # Render optimization: assigns sent to Rust only on first render.
    # Rust retains them via state merging (update_state extends, not replaces).
    # Use for large, unchanging context (pre-rendered HTML, static config).
    # Pair with dj-update="ignore" on the template element for full optimization.
    static_assigns: List[str] = []

    # Memory optimization for a page that renders once and is not expected to
    # change (#3626): keep no diff baseline. After every render the rendered
    # tree and the context copy the renderer holds are dropped, in this process
    # and in the state backend (whose entry keeps only the template and the
    # version counter), so any later render (an event, a push, a reconnect)
    # sends the whole page instead of patches. See the Best Practices guide,
    # "Large LiveView state".
    static_render: bool = False

    # The HTTP status a page answers for a state it renders itself (#3688).
    # ``{"not_found": 404, "gone": 410}``: the default ``get_http_status()``
    # reads the attribute ``state_attribute`` names (``self.state`` by default)
    # and answers the status mapped to its value. Set it once, on a base view;
    # the default is empty, so a view that declares nothing keeps answering 200.
    # Only the HTTP response has a status. A view that overrides
    # ``get_http_status()`` keeps its own answer.
    #
    # A project-wide default is ``LIVEVIEW_CONFIG["http_status_of_state"]`` (#3706).
    # The view's own attribute wins, then the config, never merged: the config is
    # read only while this attribute is still the framework's own
    # ``UNDECLARED_STATUS_MAP`` (an empty read-only map, equal to ``{}``), so a
    # view that declares ``{}`` opts out of the project's map.
    http_status_of_state: Mapping[str, int] = UNDECLARED_STATUS_MAP
    state_attribute: str = "state"

    # URL parameters a view declares as a Django form (#3689). When set, the
    # framework binds the URL's query parameters to it and calls
    # ``handle_params(cleaned_data, uri)``; invalid input calls
    # ``handle_invalid_params(form, uri)`` instead, and the messages are in the
    # template as ``params_errors``. The same decision runs for the HTTP GET, the
    # socket and SSE mount, ``url_change`` and the HTTP fallback POST. ``None``
    # (the default) keeps ``handle_params`` receiving the raw dict.
    params_form: Optional[type] = None

    # What the default ``handle_invalid_params`` hands ``handle_params`` when the
    # form rejects the URL (#3874). ``"initial"`` (the default): the initial value
    # of every field. ``"fields"``: the fields that validated, with only the ones
    # in ``form.errors`` replaced by their initial value. Anything else raises
    # ``ImproperlyConfigured`` (and is a V023 error), on every request of a view
    # that declares ``params_form``, whatever ``handle_invalid_params`` does. A view
    # that overrides it without calling ``super()`` never uses the mode. The result
    # is always a combination the form's ``clean()`` accepts, or the plain initials.
    # A mixin that sets it must come BEFORE ``LiveView`` in the bases.
    params_form_fallback: str = "initial"

    # Service-worker caching of this page's HTML (#4176). True lets the opt-in service
    # worker keep the page in its VDOM and shell caches: only for a page that renders the
    # same HTML for every visitor (djust.V037 reports one that reads the user, the session
    # or persisted state). Read from the class; ``self.sw_cacheable = True`` does nothing.
    sw_cacheable: bool = False

    # Per-view query budget (#3686, #3701). ``None`` (the default) leaves the
    # view on the smoke test's class-wide ``max_queries`` and measures nothing
    # at run time. An int caps mount + first render combined; a dict caps the
    # phases on their own: ``{"mount": 2, "render": 1, "event": 3,
    # "events": {"export": 20}}``. ``LiveViewSmokeTest`` enforces it, and with
    # ``DEBUG`` on a view that declares one is measured per phase while it runs
    # (``djust._query_budget``). A budget nothing can enforce (any other key, a
    # bool, a negative number) is an error, not ignored.
    # ``view_config[View]["max_queries"]`` overrides mount/render per test class.
    query_budget: Optional[Union[int, Dict[str, Any]]] = None

    # Authentication & authorization
    login_required: Optional[bool] = None  # True = must be authenticated
    permission_required: Optional["PermissionRequired"] = None  # str, list (all of) or AnyOf
    login_url: Optional[str] = None  # Override settings.LOGIN_URL

    # on_mount hooks — cross-cutting mount logic (Phoenix on_mount parity)
    on_mount: List[Any] = []

    # HTTP API exposure (ADR-008) — opt-in; see djust.api and the
    # ``docs/website/guides/http-api.md`` guide.
    #
    # ``api_name`` is the stable URL slug under ``/djust/api/<slug>/``. If left
    # ``None``, the slug is derived from the module path + lowercased class name,
    # but the derived slug changes when the class is moved or renamed — set
    # ``api_name`` explicitly for any view with ``expose_api=True`` handlers.
    #
    # ``api_auth_classes`` is a list of auth classes (instances or classes) tried
    # in order; the first one whose ``authenticate(request)`` returns a non-None
    # user wins. CSRF is enforced unless the winning class sets
    # ``csrf_exempt = True``. When ``None``, djust uses ``[SessionAuth]``.
    api_name: Optional[str] = None
    api_auth_classes: Optional[List[Any]] = None

    # Sticky LiveViews (Phase B of v0.6.0 Sticky LiveViews).
    #
    # When ``sticky = True`` and the view is embedded via
    # ``{% live_render "dotted.path" sticky=True %}``, the instance, its
    # DOM subtree, form values, scroll/focus, and background tasks
    # SURVIVE ``live_redirect`` navigations — provided the destination
    # layout contains a matching ``<div dj-sticky-slot="<id>">`` element.
    # ``sticky_id`` is the stable identifier shared between server and
    # client; if left ``None``, the template tag errors at render time
    # because there is no slot key to match.
    sticky: bool = False
    sticky_id: Optional[str] = None

    # ADR-038: a value reaches a template, the session or the browser only
    # when the view declares it. ``"explicit"`` is the only policy. Stating it
    # is a no-op kept through 2.x; ``"legacy"`` and any other value are refused
    # when the class is created (``_removed_settings.REMOVED_VIEW_ATTRIBUTE_VALUES``,
    # #4199).
    exposure_policy: str = "explicit"

    # Streaming initial render (v0.6.1 — Phase 1).
    #
    # Opt-in per-view flag that returns a ``StreamingHttpResponse`` from the
    # HTTP GET path instead of an ``HttpResponse``. The response body is
    # flushed in three chunks — shell-open (everything before the
    # ``<div dj-root>``), main content (the ``<div dj-root>...</div>`` block),
    # and shell-close (``</body></html>`` + trailing markup). Browsers can
    # begin parsing the ``<head>`` and loading CSS/JS while the server is
    # still computing the main content — competitive with Next.js
    # ``renderToPipeableStream``.
    #
    # Backward-compatible default (``False``) preserves the existing
    # ``HttpResponse`` path. When ``True``, the response omits the
    # ``Content-Length`` header (HTTP chunked transfer) and sets
    # ``X-Djust-Streaming: 1`` for observability.
    #
    # Caveats: some reverse proxies buffer chunked responses by default;
    # middleware that inspects response bodies must be streaming-aware.
    # Lazy-child streaming (the full Next.js-style partial hydration) is
    # tracked for v0.6.2 as Phase 2.
    streaming_render: bool = False

    # ============================================================================
    # AS_VIEW DISPATCH (PR-B for v0.9.0 streaming, ADR-015)
    # ============================================================================

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., Any]:
        """Override Django's ``View.as_view`` to route GET to :meth:`aget`
        when ``streaming_render = True`` AND we're running on ASGI.

        Django's stock dispatch routes by ``request.method.lower()`` so
        GET → sync ``self.get()``. Adding async ``aget()`` next to sync
        ``get()`` doesn't change the routing — Django's
        ``view_is_async`` only checks handlers in ``http_method_names``.

        For ``streaming_render = True`` views we therefore return a
        custom async view callable that:

        * Routes GET → ``await self.aget(...)`` when in ASGI context.
        * Routes everything else (POST, PUT, etc., AND GET on WSGI)
          through ``await sync_to_async(self.dispatch)(...)``.

        For ``streaming_render = False`` (default) we delegate to
        ``super().as_view()`` so non-streaming views run through stock
        Django dispatch with zero overhead and no behavior change.
        """
        if not getattr(cls, "streaming_render", False):
            return _protect_http_entry(cls, cast(Callable[..., Any], super().as_view(**initkwargs)))

        from asgiref.sync import markcoroutinefunction, sync_to_async

        async def view(request: Any, *args: Any, **kwargs: Any) -> Any:
            self = cls(**initkwargs)
            self.setup(request, *args, **kwargs)
            if not hasattr(self, "request"):
                raise AttributeError(
                    "%s instance has no 'request' attribute. Did you override "
                    "setup() and forget to call super()?" % cls.__name__
                )
            if request.method == "GET" and self._is_asgi_context(request):
                return await self.aget(request, *args, **kwargs)
            return await sync_to_async(self.dispatch)(request, *args, **kwargs)

        view.view_class = cls  # type: ignore[attr-defined]
        view.view_initkwargs = initkwargs  # type: ignore[attr-defined]
        view.__doc__ = cls.__doc__
        view.__module__ = cls.__module__
        view.__annotations__ = cls.dispatch.__annotations__
        # Copy possible attributes set by decorators (e.g. ``@csrf_exempt``)
        # from dispatch — mirrors Django's stock as_view behavior.
        view.__dict__.update(cls.dispatch.__dict__)
        markcoroutinefunction(view)
        return _protect_http_entry(cls, view)

    # ============================================================================
    # INITIALIZATION & SETUP
    # ============================================================================

    def _validate_exposure_configuration(self) -> None:
        """Refuse a removed exposure configuration on this instance or its class.

        ``"explicit"`` is the only policy (ADR-038); ``"legacy"`` and any other
        value are refused with ``ImproperlyConfigured`` (#4199). Class creation
        refuses them first; this catches ``LiveView(exposure_policy=...)`` and a
        policy assigned to the class after it was created. Only the instance and
        class dictionaries are read: no property or descriptor is evaluated.
        """
        from ._removed_settings import reject_removed_view_attributes

        reject_removed_view_attributes(self)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        from ._component_subscriptions import compile_subscriptions
        from ._removed_settings import reject_removed_view_attributes, upgrade_inspection_active

        reject_removed_view_attributes(cls)
        # #4045: a plain component in an explicit view's class body is one
        # object shared by every session. The mount path checks again
        # (``_params_form._refuse_removed_configuration``). 1.3 code has that
        # shape, so djust_exposure_inventory's permit lets the class be created.
        from ._exposure_providers import class_level_plain_component_error

        shared = None if upgrade_inspection_active() else class_level_plain_component_error(cls)
        if shared is not None:
            raise shared

        cls._component_subscriptions = compile_subscriptions(cls)
        # #4070: a state() field may not take a reserved name.
        _refuse_reserved_state_names(cls)
        _warn_ignored_sticky_update_inputs(cls)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._validate_exposure_configuration()
        self._rust_view: Optional[RustLiveView] = None
        self._rust_view_explicit: bool = False
        self._session_id: Optional[str] = None
        self._handler_metadata: Optional[Dict[str, Dict[str, Any]]] = (
            None  # Cache for decorator metadata
        )
        self._components: Dict[str, Any] = {}  # Registry of child components by ID
        self._component_bindings: Dict[str, Any] = {}  # Per-owner interactive descriptor cache
        self._temporary_assigns_initialized: bool = False  # Track if temp assigns are set up
        self._streams: Dict[str, Stream] = {}  # Stream collections
        self._stream_operations: list = []  # Pending stream operations for this render
        # Bumped by StreamsMixin._record_stream_op each time a mutation changed a
        # stream. ``_streams`` is a framework attribute, so the pre/post-handler
        # snapshot (``websocket._snapshot_assigns``) cannot see into it; this
        # counter is how a handler whose only effect is a stream mutation is told
        # apart from one that changed nothing (#3639). Framework state: set
        # before the ``_framework_attrs`` snapshot below.
        self._stream_epoch: int = 0
        # Initialize navigation support (live_patch, live_redirect)
        self._init_navigation()

        # Initialize child-view registry (Phase A of Sticky LiveViews).
        # Required before any {% live_render %} tag tries to register.
        self._init_sticky()

        # Initialize activity registry (v0.7.0 — React 19.2 <Activity> parity).
        # Required before any {% dj_activity %} tag tries to register.
        self._init_activity()

        # Phase B: per-instance stash of sticky children preserved across
        # a live_redirect. Populated by the consumer's
        # ``handle_live_redirect_mount`` flow via
        # :meth:`_preserve_sticky_children` before the old view is torn
        # down. Re-registered onto the new parent once its template
        # surfaces matching ``[dj-sticky-slot]`` elements.
        self._sticky_preserved: Dict[str, Any] = {}

        # Track user-defined _private attr names (populated by
        # _snapshot_user_private_attrs after mount, or a restore of automatic state).
        self._user_private_keys: Set[str] = set()

        # Object-permission cache (ADR-017, v0.9.5-1a). Populated by
        # check_object_permission() post-mount when get_object is overridden.
        # None means "not yet fetched" or "no primary object". Allocated
        # BEFORE the _framework_attrs snapshot so it's treated as a framework
        # slot, not user-private state — keeps it out of msgpack-serialized
        # state, so post-restore the cache is reset and get_object() runs
        # fresh (handles "object reassigned during disconnect").
        self._object: Any = None

        # Current Django/Channels request (#1545). Reassigned to the live
        # request by the HTTP post() path (mixins/request.py:489) and by
        # the WS path (websocket.py:1940); this placeholder exists only so
        # the attribute is captured in `_framework_attrs` BELOW — treating
        # it as framework state, not user state. Without this line `request`
        # falls outside `_framework_attrs`, and the state snapshot writes
        # the (non-msgpack-serializable) `ASGIRequest` through the
        # `serialization.py:557` non-serializable fallback, logging a
        # "non-serializable value: ASGIRequest" warning on every mount /
        # event for every LiveView (cosmetic but noisy; also dilutes the
        # warning's signal when it catches a genuine app-author bug).
        self.request: Any = None

        # dj-model auto-allowlist (CWE-915 mass-assignment guard). Populated
        # each render by ModelBindingMixin._record_dj_model_fields_from_rust()
        # with the set of fields bound via static dj-model="<field>" in the
        # TEMPLATE SOURCE (collected from the Rust template AST's Text-node
        # literals — immune to rendered-output poisoning). Assigned HERE —
        # BEFORE the _framework_attrs snapshot — so it is treated as a framework
        # slot: recomputed every render, reset on reconnect, and EXCLUDED from
        # user-private state serialization (it must never be persisted; it is
        # derived from the template each render).
        self._dj_model_fields: frozenset = frozenset()

        # Snapshot framework-set attrs so we can distinguish them from
        # user-defined _private attrs set in mount() or event handlers.
        #
        # _framework_attrs snapshot-order invariant (#1393):
        #   BEFORE this snapshot → framework state (excluded from
        #   user-private serialization, reset on reconnect). Examples:
        #   self._object cache (v0.9.5-1a), self._async_tasks.
        #   AFTER this snapshot → user state (included in change tracking,
        #   persisted across reconnects). Examples: self._action_state
        #   (PR #1324), self._<user_attr>.
        # Any new framework slot must be assigned BEFORE this line.
        self._defer_lazy_registration = False
        # Root planning and pending page renders are framework-owned and must
        # never be serialized as application private state.
        self._authenticated_partial_root = False
        self._authenticated_partial_root_origin = None
        self._authenticated_template_source = None
        self._named_partial_jit_source = None
        self._prepared_authenticated_partial_root = False
        self._prepared_authenticated_page_root = False
        self._pending_authenticated_page = False
        self._explicit_child_vdom_bindings: dict[str, Any] = {}
        self._explicit_child_vdom_tracking = False
        self._http_lazy_containers = {}
        self._http_lazy_validated_html = None
        # #4107: what the session store holds of this view's automatic state
        # (``_automatic_state``): bookkeeping, so before the snapshot below.
        self._djust_auto_basis: Any = None
        self._djust_auto_bytes = 0
        # The keys the turn's change detection found changed, for the save.
        self._djust_auto_changed: Any = None
        self._djust_auto_restore_same = False
        self._djust_auto_reapply: Any = None
        self._djust_auto_forgot = False
        self._djust_auto_reapply_declared: Any = None
        self._djust_auto_settled: Any = None
        # The tab this view belongs to: minted by the page GET, sent back by
        # that page's client on its socket mount and fallback POSTs (#4106).
        self._djust_tab = ""
        # The query handle_params() last read, the one the restored state was
        # saved under, and whether they differ (``_automatic_state.note_url``).
        self._djust_auto_url: Any = None
        self._djust_auto_saved_url: Any = None
        self._djust_auto_url_changed = False
        self._djust_restore_changed_view = True
        # #4164: the turn serial of the state the page is shown and of the
        # stored one, the reconnect's "not restored" flag, the tab's flush
        # group (named at the mount) and whether it was joined, and a fallback
        # request's claim waiting for a changing save.
        self._djust_turn_serial = 0
        self._djust_saved_serial = 0
        self._djust_not_restored = False
        self._djust_flush_group: Any = None
        self._djust_flush_group_joined = False
        self._djust_auto_pending_claim: Any = None
        self._framework_attrs: frozenset = frozenset(self.__dict__.keys())

        # v0.8.0 — @action server-action state. Initialized AFTER
        # _framework_attrs capture so it is treated as user-private
        # state and persisted across reconnects (#1284).
        self._action_state: Dict[str, Dict[str, Any]] = {}

    # ============================================================================
    # DIRTY TRACKING — cumulative since mount or last mark_clean() (v0.5.1)
    # ============================================================================

    def _dirty_fingerprint(self) -> Dict[str, Any]:
        """Shallow fingerprint of public assigns for dirty tracking.

        Mirrors the WS consumer's ``_snapshot_assigns`` (same structural
        ``deep_fingerprint``, #2664) but scoped to public attributes only (no
        leading underscore), so dirty tracking never reports framework-internal
        changes.

        Descriptor-backed public fields store their value in a ``_``-prefixed
        slot, so they are read through the descriptor under their PUBLIC name:
        ``state()`` fields (``_state_<name>``, #2956) and class-level
        components (``_component_<name>``, #2912). Reading a component binds it
        on first access, exactly as a render would.
        """
        static_skip = set(getattr(self, "static_assigns", []))
        fp: Dict[str, Any] = {}
        for k, v in self.__dict__.items():
            # A key that is not a ``str`` is not an attribute name (#4143).
            if type(k) is not str or k.startswith("_") or k in static_skip:
                continue
            fp[k] = self._dirty_value_fingerprint(v)
        for name in _descriptor_fields(type(self)):
            if name.startswith("_") or name in static_skip or name in fp:
                continue
            try:
                value = getattr(self, name)
            except Exception as exc:  # noqa: BLE001 — a broken descriptor is not "dirty"
                from ._exposure_diagnostics import log_failure_for

                # A descriptor getter or component bind is application code;
                # its error text can carry state (ADR-038).
                log_failure_for(
                    logger,
                    (self,),
                    exc,
                    "dirty tracking: could not read %s",
                    name,
                    level="debug",
                    traceback=True,
                )
                continue
            fp[name] = self._dirty_value_fingerprint(value)
        return fp

    @staticmethod
    def _dirty_value_fingerprint(v: Any) -> tuple:
        if isinstance(v, (int, float, bool, str, bytes)) or v is None:
            return ("v", v)
        if fingerprints_by_content(v):
            # Structural (#2664): ``self.items[0]["qty"] = 2`` is dirty;
            # a class-level component's State likewise (#2900). The baseline
            # keeps the fingerprint's 16-byte digest, not its tree (#3998);
            # a truncated walk is a fresh ``object()``, equal to nothing.
            digest, _truncated = deep_digest(v)
            return ("c", id(v), digest)
        return ("id", id(v))

    def _capture_dirty_baseline(self) -> None:
        """Snapshot current public assigns as the dirty-tracking baseline.

        Called once after ``mount()`` completes (by the WS consumer) and again
        whenever the user calls :meth:`mark_clean`.
        """
        # A digest per container attribute (#3998), so the baseline's size
        # does not follow the state's.
        self._dirty_baseline = self._dirty_fingerprint()
        # Snapshotted stand-in for the (excluded, large) baseline: a
        # ``mark_clean()``-only handler flips ``is_dirty`` and must re-render
        # (#2682 review). Assigned here, not in ``__init__``, so it is not in
        # the ``_framework_attrs`` set and ``_snapshot_assigns`` sees it.
        self._dirty_baseline_version = getattr(self, "_dirty_baseline_version", 0) + 1

    def mark_clean(self) -> None:
        """Reset the dirty-tracking baseline to the current state.

        Call this after persisting the view's state (e.g., after a successful
        save handler) so subsequent mutations show up as ``is_dirty``.
        """
        self._capture_dirty_baseline()

    class _FrameworkProperty(property):
        """Marker subclass so ``get_context_data`` can skip framework-derived properties."""

        _djust_framework_derived = True

    @_FrameworkProperty
    def changed_fields(self) -> set:
        """Set of public attribute names that have changed since the baseline.

        The baseline is captured after ``mount()`` completes, and reset by
        :meth:`mark_clean`. Returns an empty set if the baseline hasn't been
        captured yet (e.g., during ``mount()`` itself).
        """
        baseline = getattr(self, "_dirty_baseline", None)
        if baseline is None:
            return set()
        current = self._dirty_fingerprint()
        changed = set()
        for k in set(baseline) | set(current):
            if k not in baseline or k not in current:
                changed.add(k)
            elif baseline[k] != current[k]:
                changed.add(k)
        return changed

    @_FrameworkProperty
    def is_dirty(self) -> bool:
        """True if any public attribute has changed since the baseline.

        Use for "unsaved changes" UI patterns, conditional save buttons, and
        ``beforeunload`` warnings. Combine with :meth:`mark_clean` to reset
        after a successful save.
        """
        return bool(self.changed_fields)

    # ============================================================================
    # STABLE UNIQUE IDs — React 19 useId equivalent (v0.5.1)
    # ============================================================================

    def unique_id(self, suffix: str = "") -> str:
        """Return a deterministic per-view ID stable across renders.

        Each call within the same mount-render cycle returns a new unique ID,
        but the sequence is stable: if the template renders ``unique_id()``
        three times, the same three IDs are generated on every render. Useful
        for ``aria-labelledby``, form field IDs, and any element that needs a
        stable identifier without depending on DOM ordering.

        The ID format is ``djust-<view-slug>-<n>[-<suffix>]`` where ``<n>`` is a
        monotonically incrementing per-call counter that resets at the start of
        each render cycle (``reset_unique_ids()``).
        """
        counter = self.__dict__.setdefault("_djust_id_counter", 0)
        self._djust_id_counter = counter + 1
        slug = getattr(self, "_djust_id_slug", None)
        if slug is None:
            slug = type(self).__name__.lower()
            self._djust_id_slug = slug
        base = f"djust-{slug}-{counter}"
        return f"{base}-{suffix}" if suffix else base

    def reset_unique_ids(self) -> None:
        """Reset the ``unique_id()`` counter — called at the start of each render.

        The WS consumer calls this before invoking ``get_context_data``; tests
        can call it manually to assert stable-across-renders behavior.
        """
        self._djust_id_counter = 0

    # Component context sharing methods are provided by ``ContextProviderMixin``
    # below (declared at module scope) and mixed into both LiveView and
    # LiveComponent so ``provide_context`` / ``consume_context`` work across
    # the full render tree.

    def _snapshot_user_private_attrs(self) -> None:
        """Snapshot current _-prefixed attrs as user-defined private state names.

        Called after mount() completes. Any ``_``-prefixed attr that exists now
        but was NOT present after ``__init__`` is a user-defined private attr.
        Later render-cycle attrs won't be included because they haven't been
        set yet.

        A framework attribute first set DURING mount (``start_async()``'s
        ``_async_tasks``, a lifecycle flag such as ``_djust_child_disposed``)
        is not in the init-time ``_framework_attrs``; the
        ``_FRAMEWORK_INTERNAL_ATTRS`` list every other capture path honours
        excludes it here too (#3248).
        """
        framework: frozenset[str] = getattr(self, "_framework_attrs", frozenset())
        # Exclude the tracking attrs themselves — they are infrastructure, not
        # user state, and must never leak into the persisted private state.
        meta_attrs = {"_framework_attrs", "_user_private_keys", "_reactive_state"}
        self._user_private_keys = {
            k
            for k in self.__dict__
            if type(k) is str  # #4143: not an attribute name otherwise
            and k.startswith("_")
            and k not in framework
            and k not in meta_attrs
            and k not in _FRAMEWORK_INTERNAL_ATTRS
        }

    def _should_restore_snapshot(self, request: Any) -> bool:
        """Return True to allow the signed client snapshot to be restored.

        Asked on a mount that carries the browser's signed token for this
        view's ``state(persist="client", client=True)`` fields (a
        back-navigation or a reconnect). Returning False mounts without those
        values, as if the browser had sent no token. Override to refuse on a
        feature-flag change or a freshness rule of your own; the token's
        signature, age and binding to the session, user, tenant and route are
        checked separately.

        Runs AFTER Django auth / ``check_view_auth`` — a returning user
        whose permissions were revoked will already have been redirected
        away before this hook fires.
        """
        return True

    def handle_tick(self) -> None:
        """Override for periodic server-side updates. Called every tick_interval ms."""
        pass

    def save_state(self) -> None:
        """Save this view's state before the current turn's reply.

        With ``DJUST_STATE_SAVE_INTERVAL`` (2 seconds by default) or the
        view's ``state_save_interval`` above 0, a WebSocket or SSE turn's state may be
        written up to that many seconds after its reply. Call this from a
        handler, a background callback or ``handle_tick()`` when this turn's
        state must be in storage before the page shows it (a step another
        system relies on, say). With the interval at 0 every turn is saved
        before its reply anyway, and this changes nothing. The same as
        ``@event_handler(save=True)`` for one turn.
        """
        from ._save_policy import request_save

        request_save(self)

    def handle_restore(self, request: Any) -> None:
        """Called after the view's saved state was put back on a new instance.

        A reconnect, a Back or an HTTP fallback request gives the user a new
        view object. ``mount()`` runs first, then the saved state is applied
        (on the legacy HTTP fallback, a view whose state was all saved skips
        ``mount()``). Either way ``mount()`` never sees the restored state, so
        rows a view keeps out of the saved state (not JSON, or content that
        must not be saved) are not read again. Override this to read them from
        the restored ids and cursors::

            def handle_restore(self, request):
                self._rows = Chat.objects.filter(pk=self.chat_id)   # chat_id was restored

        It runs on a WebSocket/SSE mount of a view whose saved state was
        restored, on an explicit child embedded with ``{% live_render %}``, and
        on the HTTP page-POST fallback, at the same point on each (#4088):
        after ``mount()`` (when it runs) and the restore, after
        ``handle_params()`` and the saved state's re-apply, after the object
        permission check, before the first render or the event handler. It
        does not run after a plain ``mount()``. May be ``async def``.
        ``handle_params()`` and ``get_object()`` run before it, so they must not
        read rows that only ``handle_restore()`` loads.

        Keep it idempotent: what it changes is not saved until the next event,
        so it runs again on every reconnect until one lands. It also cannot
        tell a reconnect from a second tab opening the same page in the same
        session, which restores the same state.

        Background work does not survive a reconnect or a restart: a job
        started with ``start_async`` / ``@background`` belongs to the connection
        that started it. A view that persists a "running" flag must clear it
        here, or the page comes back showing a job that no longer exists::

            def handle_restore(self, request):
                if self.generating:          # restored, but the job is gone
                    self.generating = False
                    self.error = "Interrupted. Please try again."
        """
        pass

    def get_state(self) -> Dict[str, Any]:
        """Get the client-visible state of this LiveView instance.

        Exports only declared ``client=True`` fields using the bounded JSON
        contract. Rendering context and server-only state are not inputs.
        Returned values are detached; a failure raises ``ExposureError``
        without the offending value.

        Returns:
            The client-permitted declared values.
        """
        from ._exposure import explicit_state_projection

        return explicit_state_projection(self, "client")

    # ============================================================================
    # TEMPORARY ASSIGNS - Memory optimization for large collections
    # ============================================================================

    def _reset_temporary_assigns(self) -> None:
        """
        Reset temporary assigns to their default values after rendering.

        Called automatically after each render to free memory for large collections.
        """
        # Returning here, before ``_reset_streams()``, is load-bearing: a view
        # that uses streams WITHOUT temporary_assigns keeps its stream items so
        # the next render lists them. The differ turns that list into DOM changes
        # against the stored tree, so clearing it after every render would
        # remove every row from the page on the next unrelated event (measured in
        # ``test_stream_state_weight_3632.py``; the cost is the stream rows held
        # in the stored state, #3632).
        if not self.temporary_assigns:
            return

        for assign_name, default_value in self.temporary_assigns.items():
            if hasattr(self, assign_name):
                # Reset to default value (make a copy to avoid sharing state)
                if isinstance(default_value, list):
                    setattr(self, assign_name, list(default_value))
                elif isinstance(default_value, dict):
                    setattr(self, assign_name, dict(default_value))
                elif isinstance(default_value, set):
                    setattr(self, assign_name, set(default_value))
                else:
                    setattr(self, assign_name, default_value)

                logger.debug(
                    f"[LiveView] Reset temporary assign '{assign_name}' to {type(default_value).__name__}"
                )

        # Also reset streams
        self._reset_streams()

    def _initialize_temporary_assigns(self) -> None:
        """Initialize temporary assigns with their default values on first mount."""
        if self._temporary_assigns_initialized:
            return

        for assign_name, default_value in self.temporary_assigns.items():
            if not hasattr(self, assign_name):
                if isinstance(default_value, list):
                    setattr(self, assign_name, list(default_value))
                elif isinstance(default_value, dict):
                    setattr(self, assign_name, dict(default_value))
                elif isinstance(default_value, set):
                    setattr(self, assign_name, set(default_value))
                else:
                    setattr(self, assign_name, default_value)

        self._temporary_assigns_initialized = True

    # ============================================================================
    # OBJECT-LEVEL AUTHORIZATION (ADR-017, v0.9.5-1a)
    #
    # The pair below is djust's first-class lifecycle hook for per-object
    # auth — the structural counterpart to the role-level `permission_required`
    # class attribute and the custom `check_permissions(self, request)` hook.
    # See `docs/adr/017-object-permission-lifecycle.md`.
    # ============================================================================

    def get_object(self) -> Optional[Any]:
        """Return the view's primary object, or None if not applicable.

        Override in subclasses bound to a single object via URL kwarg
        (e.g. `/documents/<int:document_id>/`). The default returns
        `None` so views that don't override see zero behavior change —
        the object-permission lifecycle is opt-in.

        Called once by djust per mount, AFTER URL kwargs are bound to
        `self` via `mount()`. The result is cached as `self._object`
        for the WS-session lifetime; reuse it from event handlers and
        `get_context_data` rather than re-querying. If a handler
        mutates state that affects access (e.g., reassigning the FK
        that determines ownership), call `self._invalidate_object_cache()`
        so the next access re-fetches via `get_object()`.

        Returning `None` from a subclass override (e.g. when the object
        doesn't exist or shouldn't be enumerable) is treated as
        "no object to check" — `has_object_permission` is NOT called.
        This is the recommended OWASP IDOR-mitigation pattern: deny via
        404-shape rather than 403-shape so attackers can't enumerate.

        Keep `get_object()` minimal — just the FK lookup. Expensive I/O
        in this method becomes per-mount overhead.
        """
        return None

    def has_object_permission(self, request: Any, obj: Any) -> bool:
        """Return True if the request user may access `obj`.

        Override alongside `get_object()` to express object-level auth.
        Default returns `True` (no-op for views that don't override
        `get_object`).

        Called by djust at mount-time when `get_object` is overridden.
        (v0.9.5-1b extends this to per-event re-execution; -1a is
        mount-time only.)

        Raise `PermissionDenied` for an explicit denial with a message;
        return `False` for a silent denial. Both close the WS at mount
        time with code 4403 and a "Permission denied" error frame.
        """
        return True

    def _invalidate_object_cache(self) -> None:
        """Reset `self._object` to None; next get_object() call re-fetches.

        Call from event handlers that mutate state affecting access
        (e.g., reassigning the FK that determines ownership). Without
        this, a cached `self._object` lets a formerly-authorized user
        retain the cached pass until WS reconnect.

        The cache is also automatically reset on snapshot/state restore —
        `_object` is a framework slot, not user state, so it returns to
        `None` after either restore path. This handles the "object
        reassigned while user was disconnected" case automatically.
        """
        self._object = None


#: Access hooks djust reads from a view's CLASS, never from the instance: a
#: function view cannot supply them, and setting one on the view would be
#: ignored, leaving the page open while it looks gated (#4183).
_FUNCTION_VIEW_REFUSED = ("check_permissions", "get_object", "on_mount")


def _refuse_function_view_access_hooks(func: Callable[..., Any], result: Mapping[str, Any]) -> None:
    """Refuse a returned access hook a function view cannot apply (#4183)."""
    from django.core.exceptions import ImproperlyConfigured

    for name in _FUNCTION_VIEW_REFUSED:
        if name in result:
            raise ImproperlyConfigured(
                "@live_view function %s returns %r, which djust reads only from a LiveView "
                "class, so it would not protect the page. Write a LiveView subclass that "
                "defines %s, or return login_required / permission_required instead."
                % (getattr(func, "__qualname__", func), name, name)
            )


def _function_view_configuration(result: Mapping[str, Any]) -> frozenset:
    """The returned names that configure a function view instead of rendering (#4183).

    A public, non-callable value whose name is a ``LiveView`` class attribute
    (``login_required``, ``permission_required``, ``login_url``, ``template``,
    ``template_name`` ...) is set on the view, as it always was, before the
    view's access checks run, so a function view can still require a login or
    a permission or choose its template. It is not a template name.
    ``exposure_policy`` is not applied: a function view is explicit.
    """
    from ._class_snapshot import namespace

    view_names: Set[str] = set()
    for base in LiveView.__mro__:
        view_names.update(namespace(base))
    return frozenset(
        key
        for key, value in result.items()
        if type(key) is str
        and not key.startswith("_")
        and key != "exposure_policy"
        and not callable(value)
        and key in view_names
    )


def _function_view_context(result: Mapping[str, Any], view_class: type) -> Dict[str, Any]:
    """The template names a function view's returned mapping supplies (#4183).

    The names the attribute walk rendered when these values were set as view
    attributes: no private (``_``-prefixed) name, nothing callable, and no
    module, class or request object. Also left out: the view's configuration
    (:func:`_function_view_configuration`), and the names djust supplies itself
    (``view``, ``streams``, and the framework providers' ``csrf_token``,
    ``DATE_FORMAT`` and ``TIME_FORMAT``), which the template keeps reading
    from djust.
    """
    import types

    from django.http import HttpRequest

    from ._exposure import provider_owners

    skip = (types.ModuleType, type, HttpRequest)
    supplied = {"view", "streams", "exposure_policy"} | set(provider_owners(view_class))
    supplied |= _function_view_configuration(result)
    return {
        key: value
        for key, value in result.items()
        if type(key) is str
        and not key.startswith("_")
        and key not in supplied
        and not callable(value)
        and not isinstance(value, skip)
    }


def live_view(
    template_name: Optional[str] = None, template: Optional[str] = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to convert a function-based view into a LiveView.

    Usage:
        @live_view(template_name='counter.html')
        def counter_view(request):
            count = 0

            @event_handler
            def increment(**kwargs):
                ...

            return locals()

    The view uses explicit exposure (#4183): the values the function returns
    are its context, and the callables it returns are its handlers. A returned
    LiveView setting (``login_required``, ``permission_required``,
    ``template`` ...) configures the view before its access checks run. A function
    view runs over the page GET and the HTTP POST fallback. It is built per
    request, so it has no class a WebSocket or SSE mount can name: its page marks
    its root ``data-djust-transport="http"`` and the client sends events over
    the HTTP fallback directly, opening no socket (#4236). It keeps no state
    between requests: the function runs again for every request.

    Args:
        template_name: Path to Django template
        template: Inline template string

    Returns:
        View function
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(request: Any, *args: Any, **kwargs: Any) -> Any:
            # Create a dynamic LiveView class
            class DynamicLiveView(LiveView):
                # The decorated function's returned values are this view's
                # context; it declares no state (#4183).
                exposure_policy = "explicit"
                _djust_function_context: Dict[str, Any] = {}
                # No class a socket can mount: the page tells the client to
                # use the HTTP fallback directly (#4236).
                _djust_http_transport = True

                def get_context_data(self, **context_kwargs: Any) -> Dict[str, Any]:
                    context = super().get_context_data(**context_kwargs)
                    context.update(type(self)._djust_function_context)
                    return context

            if template_name:
                DynamicLiveView.template_name = template_name
            if template:
                DynamicLiveView.template = template

            view = DynamicLiveView()

            # Execute the function to get initial state
            result = func(request, *args, **kwargs)
            if isinstance(result, dict):
                _refuse_function_view_access_hooks(func, result)
                # The class is built for this request alone, so its context
                # is too. Callables stay view attributes: they are the
                # function's event handlers. So do the view's configuration
                # values (``login_required``, ``template`` ...), set here,
                # before get()/post() run the view's access checks.
                DynamicLiveView._djust_function_context = _function_view_context(
                    result, DynamicLiveView
                )
                configuration = _function_view_configuration(result)
                for key, value in result.items():
                    if callable(value) or key in configuration:
                        setattr(view, key, value)

            # Handle the request
            if request.method == "GET":
                return view.get(request, *args, **kwargs)
            elif request.method == "POST":
                return view.post(request, *args, **kwargs)

            return None

        return wrapper

    return decorator


def __getattr__(name: str) -> Any:  # PEP 562
    from ._removed_settings import refuse_removed_name

    refuse_removed_name(__name__, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
