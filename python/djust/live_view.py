"""
LiveView base class and decorator for reactive Django views
"""

import io
import json
import logging
import socket
import threading
import weakref
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Set, Union, cast

if TYPE_CHECKING:
    from .auth.any_of import PermissionRequired

from django.utils.decorators import classonlymethod
from django.views import View

from ._class_snapshot import namespace as _class_namespace
from ._context_provider import ContextProviderMixin  # noqa: F401  # re-exported for back-compat
from ._deprecation import class_statement_stacklevel, warn_deprecated
from .change_detection import CHANGE_DETECTION_IGNORED, deep_fingerprint, fingerprints_by_content
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
# ``_exposure.reserved_state_names()``. This name stays only for the legacy
# exporters that still filter the attribute walk with it (``get_state``,
# ``_capture_snapshot_state``, the private-state helpers, time travel, the
# legacy debug panel and the exposure inventory); it goes with them (rows 7, 9).
_FRAMEWORK_INTERNAL_ATTRS: frozenset = CHANGE_DETECTION_IGNORED


# Component-level analog (#1041): framework-internal LiveComponent
# attrs that DON'T start with ``_`` but aren't user state. Excluded
# from the ``__components__`` snapshot to keep the time-travel state
# focused on actual user state. Mirrors the parent's
# :data:`_FRAMEWORK_INTERNAL_ATTRS` for component fields.
#
# ``component_id`` in particular is the registry key — restoring it
# from stale snapshot state would desync the registry from the
# instance's ``component_id`` attribute.
_COMPONENT_INTERNAL_ATTRS: frozenset = frozenset(
    {
        "component_id",
        "template",
        "template_name",
        "assigns",
        "slots",
    }
)


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
        return any(_holds_model(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_holds_model(v) for v in value)
    return False


def _shadows_component_method(component: Any, key: str) -> bool:
    """Whether ``key`` names a callable on the component's class (#3046).

    Checks the component's own class and, for a class-level component bound
    to a view (``BoundComponent``, ADR-031), the descriptor's class too, since
    that is where its handlers live. Class-level lookup only: an instance
    value never counts.
    """
    classes = [type(component)]
    descriptor = getattr(component, "__dict__", {}).get("_descriptor")
    if descriptor is not None:
        classes.append(type(descriptor))
    for cls in classes:
        try:
            member = getattr(cls, key, None)
        except Exception:  # noqa: BLE001 — a raising class attr is not a method
            continue
        if callable(member):
            return True
    return False


def restore_components_snapshot(view: Any, components_state: Any, *, source: str) -> bool:
    """Apply a ``__components__`` snapshot map to ``view``'s components.

    Used by the time-travel restore (``time_travel.py``, #2896).

    Each ``{component_id: {field: value}}`` entry goes only to a component the
    view already knows: one in ``view._components``, or a class-level component
    DECLARED on the view class (bound on demand, as the first ``view.<name>``
    access would). Unknown ids, non-dict entries and blocked field names
    (private, dunder, :data:`_COMPONENT_INTERNAL_ATTRS`) are skipped. Values go
    through ``safe_setattr``.

    Returns ``True`` when every entry applied cleanly.
    """
    from .security import safe_setattr

    if not components_state:
        return True
    if not isinstance(components_state, dict):
        logger.warning("%s: __components__ is not a mapping; ignoring", source)
        return False
    registry = getattr(view, "_components", None)
    if not isinstance(registry, dict):
        registry = {}
    declared = _descriptor_fields(type(view))
    ok = True
    for component_id, component_snap in components_state.items():
        component = registry.get(component_id)
        if (
            component is None
            and isinstance(component_id, str)
            and declared.get(component_id, (None,))[0] == "component"
        ):
            component = getattr(view, component_id, None)
        if component is None:
            logger.warning("%s: component %r in snapshot but not in registry", source, component_id)
            ok = False
            continue
        if not isinstance(component_snap, dict):
            logger.warning("%s: component %r snapshot is not a mapping", source, component_id)
            ok = False
            continue
        # An interactive component restores through its concrete state
        # schema, never field-by-field (ADR-038); None keeps the legacy path.
        from .time_travel import _restore_interactive_state

        restored = _restore_interactive_state(component, component_snap)
        if restored is not None:
            ok = restored and ok
            continue
        for key, value in component_snap.items():
            if not isinstance(key, str) or key in _COMPONENT_INTERNAL_ATTRS:
                continue
            if _shadows_component_method(component, key):
                # A snapshot field named like a method (``render``, ``mount``,
                # a handler) would shadow it on the instance (#3046). No real
                # state field has such a name, so skip it.
                logger.warning(
                    "%s: component restore skipped id=%s key=%s (names a method)",
                    source,
                    component_id,
                    key,
                )
                ok = False
                continue
            try:
                applied = safe_setattr(component, key, value, allow_private=False)
            except Exception as exc:  # noqa: BLE001 — log + degrade, never break a restore
                from ._exposure_diagnostics import log_failure_for

                # A setter is application code; its error text can carry
                # state, so it is value-free for a nonlegacy owner (ADR-038).
                log_failure_for(
                    logger,
                    (view, component),
                    exc,
                    "%s: component restore failed for id=%s key=%s",
                    source,
                    component_id,
                    key,
                    traceback=True,
                )
                ok = False
                continue
            if not applied:
                logger.warning(
                    "%s: component restore blocked for id=%s key=%s", source, component_id, key
                )
                ok = False
    return ok


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
    """ADR-038 D-a: a nonlegacy class's HTTP entry fails with a value-free 500.

    The constructor, ``setup``, ``on_mount`` hooks, ``mount``, ``handle_params``,
    ``get_context_data`` and render all run under Django's view callable with no
    protected scope, so under ``DEBUG`` Django's technical 500 page would show
    the exception and its frames' locals. The class owns the policy before an
    instance exists. A legacy class keeps Django's callable itself, unchanged;
    a class that became legacy after ``as_view`` re-raises as before.
    """
    from asgiref.sync import iscoroutinefunction, markcoroutinefunction

    from ._exposure import uses_legacy_exposure

    if uses_legacy_exposure(cls):
        return view

    from ._exposure_diagnostics import (
        diagnostic_scope,
        diagnostics_allowed,
        protected_http_outcome,
        protected_server_error,
        restrict_diagnostics,
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
                    del exc
            # Outside the except block: the signal's exception has no context.
            return await sync_to_async(protected_server_error)(request, logger, outcome)

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
                del exc
        return protected_server_error(request, logger, outcome)

    _PROTECTED_HTTP_ENTRIES.add(protected)
    return protected


#: Where the deprecation message sends a reader.
EXPLICIT_EXPOSURE_GUIDE_URL = "https://docs.djust.org/state/explicit-exposure/"

#: Classes whose own ``exposure_policy = "legacy"`` has been warned about, and
#: classes whose MRO has been searched for one. Weak: tests assert that view
#: classes can be collected.
_LEGACY_POLICY_WARNED: "weakref.WeakSet[type]" = weakref.WeakSet()
_LEGACY_POLICY_SEARCHED: "weakref.WeakSet[type]" = weakref.WeakSet()


def _is_framework_class(cls: type) -> bool:
    """Whether ``cls`` is shipped in ``djust`` (its test modules are not)."""
    module = getattr(cls, "__module__", "") or ""
    return module == "djust" or (
        module.startswith("djust.") and not module.startswith("djust.tests.")
    )


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


def _warn_legacy_exposure_policy(cls: type, *, at_class_statement: bool) -> None:
    """Warn, once per class, that the class states ``exposure_policy = "legacy"``.

    The warning is about the class that *states* the policy, because that is
    the line to change: a subclass that inherits the statement adds no second
    warning. It is raised when the class is created, pointing at its ``class``
    statement. A policy assigned to the class afterwards, or stated on a plain
    mixin (which has no ``__init_subclass__`` of ours), is caught the first
    time a view of that class is constructed (``at_class_statement=False``),
    and points at the code that constructed it.

    .. deprecated:: 2.0
       ``exposure_policy = "legacy"``. Removed in 2.0.0.

    A class shipped in djust that states ``"legacy"`` (the admin change list,
    ...) does not warn, here or through a subclass:
    that statement is not the application's to change.
    """
    if at_class_statement:
        owners = [cls] if "exposure_policy" in cls.__dict__ else []
    else:
        if cls in _LEGACY_POLICY_SEARCHED:
            return
        owners = [base for base in cls.__mro__ if "exposure_policy" in base.__dict__][:1]
    for owner in owners:
        policy = owner.__dict__["exposure_policy"]
        if type(policy) is not str or policy != "legacy":
            continue
        if owner in _LEGACY_POLICY_WARNED or _is_framework_class(owner):
            continue
        warn_deprecated(
            'exposure_policy = "legacy" (stated on %s.%s)'
            % (getattr(owner, "__module__", "?"), owner.__qualname__),
            since="2.0",
            removed_in="2.0.0",
            instead=(
                'exposure_policy = "explicit" (the default: declare state() fields and '
                "supply template context in get_context_data(); see %s). A legacy view "
                "keeps no state between requests in 2.0: the page-POST fallback mounts "
                'it again on every POST; declare state(persist="server") for what must '
                "survive" % EXPLICIT_EXPOSURE_GUIDE_URL
            ),
            # Both walk out of djust's own frames: to the ``class`` statement,
            # or to the code that constructed the view.
            stacklevel=class_statement_stacklevel(),
        )
        # Recorded only once the warning has been raised and returned. Under
        # warnings-as-errors it raises instead, and then every construction
        # (and a re-import of the module) fails, not just the first.
        _LEGACY_POLICY_WARNED.add(owner)
    if not at_class_statement:
        _LEGACY_POLICY_SEARCHED.add(cls)


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

    .. deprecated:: 1.3
        Defining ``_drain_announcements`` or ``_drain_focus`` on a subclass is
        deprecated and the WebSocket/SSE flush that drains them is removed no
        earlier than djust 2.0.0. Render a ``role="status"`` live region and
        call ``JS.focus()`` through ``push_commands()`` instead. A hook added
        after the class exists (``setattr``, a decorator, an instance
        attribute) is not detected and does not warn.
    """

    template_name: Optional[str] = None
    template: Optional[str] = None
    tick_interval: Optional[int] = None  # Periodic tick in ms (e.g. 2000 for 2s)
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
    # when the view declares it. ``"legacy"`` (every public attribute is
    # exported) is still accepted when a class states it, and is deprecated:
    # see ``_warn_legacy_exposure_policy``.
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

    # Time-travel debugging (v0.6.1 — dev-only).
    #
    # Opt-in per-view flag that enables a per-instance ring buffer of
    # :class:`djust.time_travel.EventSnapshot` entries — one for every
    # ``@event_handler`` dispatch, capturing ``state_before`` /
    # ``state_after``. The debug panel exposes a "Time Travel" tab that
    # lets developers scrub through history and jump back to any past
    # state; the server restores the snapshot + re-renders.
    #
    # Safe default (``False``) costs zero when disabled. Gated on
    # ``DEBUG=True`` at the WebSocket consumer so enabling it in a
    # release build silently no-ops. See
    # :mod:`djust.time_travel` for the recording machinery.
    time_travel_enabled: bool = False

    # Top-level public-state keys that must NEVER leave this view inside a
    # shared bug capture (#1561). ``encode_view_state()`` applies these
    # BEFORE any caller-supplied ``scrub`` callable, so the redaction does
    # not depend on every call site remembering to pass one — the per-call
    # ``scrub`` argument stays available for arbitrary policies, and this
    # is the default safety net underneath it.
    #
    # Declarative rather than behavioural on purpose: it is a list a
    # reviewer can read, and the ``djust.V014`` system check reads it too,
    # warning when a time-travel-enabled view has model or form fields whose
    # names look like PII and are not named here.
    #
    # Scope matches ``scrub_fields``: TOP-LEVEL public-state keys only. A
    # nested ``self.profile["ssn"]`` is not reached by naming ``"ssn"``;
    # pass your own ``scrub`` callable for that.
    time_travel_excluded_fields: List[str] = []

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
        """Refuse exposure configurations that would widen or silently fall back.

        ``"legacy"`` and ``"explicit"`` are the supported policies (ADR-038).
        Anything else is refused rather than treated as legacy. A legacy view
        cannot declare exposure grants it would not honor. Only class
        dictionaries are inspected: no property, factory, ORM query or
        component descriptor is evaluated.
        """
        from ._exposure import ExposureConfigurationError

        policy = self.exposure_policy
        if type(policy) is not str or policy not in ("legacy", "explicit"):
            raise ExposureConfigurationError(
                "Invalid exposure_policy. Supported policies are 'legacy' and 'explicit'; "
                "unknown policies cannot fall back to legacy exposure."
            )
        if policy == "explicit":
            return
        _warn_legacy_exposure_policy(type(self), at_class_statement=False)
        from ._state import StateProperty

        # Inspect class dictionaries only: checking configuration must not
        # evaluate properties, factories, ORM queries or component descriptors.
        seen_state_names: set[str] = set()
        for owner in type(self).__mro__:
            for name, declaration in _class_namespace(owner).items():  # #3151
                if name in seen_state_names:
                    continue
                seen_state_names.add(name)
                if issubclass(type(declaration), StateProperty) and (
                    declaration.exposure.persist not in (None, False) or declaration.exposure.client
                ):
                    raise ExposureConfigurationError(
                        "state() exposure grants require exposure_policy='explicit'. "
                        "Legacy views cannot honor these grants."
                    )
                # The legacy walk renders every public field, so it cannot
                # honour render=False either (#4050).
                if issubclass(type(declaration), StateProperty) and declaration.render is False:
                    raise ExposureConfigurationError(
                        "state(render=False) requires exposure_policy='explicit'. "
                        "Legacy views render every public attribute."
                    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        from ._component_subscriptions import compile_subscriptions
        from ._removed_settings import reject_removed_view_attributes

        reject_removed_view_attributes(cls)
        # #4045: a plain component in an explicit view's class body is one
        # object shared by every session. The mount path checks again
        # (``_params_form._refuse_removed_configuration``).
        from ._exposure_providers import class_level_plain_component_error

        shared = class_level_plain_component_error(cls)
        if shared is not None:
            raise shared

        cls._component_subscriptions = compile_subscriptions(cls)
        _warn_legacy_exposure_policy(cls, at_class_statement=True)
        # #4070: a state() field may not take a reserved name.
        _refuse_reserved_state_names(cls)

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
        # _snapshot_user_private_attrs after mount, or _restore_private_state).
        self._user_private_keys: Set[str] = set()

        # Time-travel debugging (v0.6.1) — lazy per-instance ring buffer.
        # Allocated only when the subclass opts in via the class attr so
        # the 99% of views that don't use it pay zero memory cost.
        self._time_travel_buffer = None
        # Branched-timeline tracking (#1151, v0.9.4). Default branch is
        # "main" — the canonical recorded timeline. Forward-replay from
        # a non-tip cursor allocates a fresh branch id from the counter.
        # Both fields are inert when the buffer isn't allocated.
        self._time_travel_branch_id = "main"
        self._time_travel_branch_counter = 0
        if getattr(self.__class__, "time_travel_enabled", False):
            try:
                from djust.time_travel import TimeTravelBuffer
                from djust.config import config as _djust_config

                self._time_travel_buffer = TimeTravelBuffer(
                    max_events=_djust_config.get("time_travel_max_events", 100)
                )
            except Exception:  # noqa: BLE001
                # Never let a misconfigured buffer break __init__.
                logger.exception("time_travel: failed to allocate buffer")
                self._time_travel_buffer = None

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
        self._djust_auto_settled: Any = None
        # The tab this view belongs to: minted by the page GET, sent back by
        # that page's client on its socket mount and fallback POSTs (#4106).
        self._djust_tab = ""
        self._djust_restore_changed_view = True
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
            if k.startswith("_") or k in static_skip:
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
            # a class-level component's State likewise (#2900).
            fp, truncated = deep_fingerprint(v)
            return ("c", id(v), object() if truncated else fp)
        return ("id", id(v))

    def _capture_dirty_baseline(self) -> None:
        """Snapshot current public assigns as the dirty-tracking baseline.

        Called once after ``mount()`` completes (by the WS consumer) and again
        whenever the user calls :meth:`mark_clean`.
        """
        baseline = self._dirty_fingerprint()
        from . import _per_turn

        if _per_turn.on("shared_fingerprints"):
            # One tree per attribute (#3998): where the last render's
            # fingerprint of the same attribute is equal, the baseline holds
            # that object. A fingerprint is immutable and shares nothing with
            # the state, so an equal one is interchangeable.
            held = self.__dict__.get("_prev_context_fingerprints") or {}
            for name, entry in baseline.items():
                if entry[0] == "c" and name in held and held[name] == entry[2]:
                    baseline[name] = (entry[0], entry[1], held[name])
        self._dirty_baseline = baseline
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
        ``_async_tasks``, a lifecycle flag such as ``_djust_waiters_closed``)
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
            if k.startswith("_")
            and k not in framework
            and k not in meta_attrs
            and k not in _FRAMEWORK_INTERNAL_ATTRS
        }

    def _framework_storage_slots(self) -> Set[str]:
        """``_``-prefixed instance slots that are framework storage, not user
        private state, and so are never saved in the private session (#2959).

        - the ``_state_<name>`` slot of a PUBLIC ``state()`` field: its value
          reaches the session through the public state (the context reads it
          through the descriptor, and every restore path sets it back through
          the descriptor), so a second copy here is redundant. A ``_``-named
          ``state()`` field, or one listed in ``static_assigns``, never reaches
          the public context, so its slot stays private.
        - ``_reactive_state``: the descriptor's own bookkeeping; any restore
          through the descriptor rebuilds it.

        :meth:`_get_private_state` still saves a public ``state()`` slot whose
        value holds a Django model: the public path flattens a model to a dict,
        this one re-hydrates it (#1994).
        """
        static_skip = set(getattr(self, "static_assigns", []) or [])
        slots = {"_reactive_state"}
        for name, (kind, slot) in _descriptor_fields(type(self)).items():
            if kind == "state" and not name.startswith("_") and name not in static_skip:
                slots.add(slot)
        return slots

    def _get_private_state(self) -> Dict[str, Any]:
        """Return serializable user-defined _private attributes (not framework internals).

        Only persists attrs tracked in ``_user_private_keys`` — a set populated
        by ``_snapshot_user_private_attrs()`` (after mount) and
        ``_restore_private_state()``. Event handlers that add NEW private attrs
        should add the name to ``self._user_private_keys`` directly, e.g.
        ``self._user_private_keys.add('_name')``, or the attr will not be
        persisted in subsequent save cycles.

        Non-serializable values (locks, file handles, etc.) are silently skipped.
        This is a legacy-only API; explicit persistence uses a bound adapter.

        2.0: the page-POST fallback and the socket no longer save private
        attributes; the one remaining caller is the legacy ``lazy=True`` child
        on the page-POST fallback (``_lazy_containers.py``), which goes with
        that path (exposure-flip plan row 9).
        """
        from ._exposure import require_legacy_state_api

        require_legacy_state_api(self)
        result: Dict[str, Any] = {}
        user_keys: Set[str] = getattr(self, "_user_private_keys", set())
        # A session written before #2959 may have restored these into
        # ``_user_private_keys``; they are still not re-saved.
        not_private = self._framework_storage_slots()
        for key in user_keys:
            # A framework attribute is never user private state, even when a
            # session written before #3248 restored it into the tracked set.
            if key not in self.__dict__ or key in _FRAMEWORK_INTERNAL_ATTRS:
                continue
            value = self.__dict__[key]
            if key in not_private and not (key != "_reactive_state" and _holds_model(value)):
                # The public state carries this value — except a Django model,
                # which the public path flattens to a dict while this one
                # re-hydrates it (#1994), so a model-holding slot stays.
                continue
            # Skip callables (bound methods, lambdas stored as attrs)
            if callable(value):
                continue
            # ADR-031: a class-level component's slot holds a BoundComponent;
            # persist its State (a dict) so ``__get__`` rehydrates it on restore.
            from .components.base import BoundComponent

            if isinstance(value, BoundComponent):
                value = dict(value.state)
            # #1994: encode Django models to a re-hydratable ref so a private
            # model attr survives the session round-trip AS A MODEL (re-fetched
            # on restore) instead of the lossy client dict normalize_django_value
            # would otherwise produce. No-op for non-model values.
            value = encode_private_model_refs(value)
            # Attempt serialization — skip if not possible
            try:
                json.dumps(value, cls=DjangoJSONEncoder)
                result[key] = value
            except (TypeError, ValueError, OverflowError):
                logger.debug(
                    "Skipping non-serializable private attr %s.%s (%s)",
                    type(self).__name__,
                    key,
                    type(value).__name__,
                )
                continue
        return result

    def _restore_private_state(self, private_state: Dict[str, Any]) -> None:
        """Restore legacy private attributes; explicit views require a bound adapter.

        A ``"lifecycle"`` step of the ``sync_turn_wrapper`` (#3646): a private model
        reference is re-fetched here (``decode_private_model_refs``, ``Model.objects.get``)
        and a private attribute can be a property whose setter is application code. Its
        one remaining caller (2.0) is the legacy lazy container on the page-POST
        fallback; the step is entered here.

        A guarded step: a wrapper whose ``__exit__`` swallows
        an exception would otherwise turn a failing restore into a silent PARTIAL one
        (the attributes before the failing key applied, the rest skipped). The failure
        propagates as ``PermissionDenied`` instead.
        """
        from ._exposure import require_legacy_state_api
        from .sync_turn import run_guarded_step

        require_legacy_state_api(self)
        run_guarded_step(self, "lifecycle", lambda: self._apply_private_state(private_state))

    def _apply_private_state(self, private_state: Dict[str, Any]) -> None:
        """The body of :meth:`_restore_private_state`, run inside its step."""
        from .security import DANGEROUS_ATTRIBUTES

        framework: frozenset[str] = getattr(self, "_framework_attrs", frozenset())
        meta_attrs = {"_framework_attrs", "_user_private_keys"}
        for key, value in private_state.items():
            if not isinstance(key, str):
                continue
            # The session is trusted, but this path used a raw setattr with no
            # screen at all, unlike the public one (#3046). A dunder
            # (``__class__``, ``__dict__``) is never user private state.
            if key in DANGEROUS_ATTRIBUTES or (key.startswith("__") and key.endswith("__")):
                logger.warning("Skipping restore of reserved private attribute %r", key)
                continue
            if (
                key.startswith("_")
                and key not in framework
                and key not in meta_attrs
                # A framework attribute set during mount is not in the
                # init-time ``framework`` set; never restore one (#3248).
                and key not in _FRAMEWORK_INTERNAL_ATTRS
            ):
                # #1994: re-hydrate model refs back to model instances (fresh DB
                # fetch) so a private model attr comes back a MODEL, not a dict.
                value = decode_private_model_refs(value)
                # #2252: un-tag Decimals written by decimal_for_state_roundtrip
                # so a private DecimalField attr comes back a DECIMAL, not a
                # tag dict. This is the single decode point for all three
                # callers of this method (mixins/request.py, runtime.py,
                # mixins/sticky.py).
                value = decode_state_roundtrip(value)
                setattr(self, key, value)
                # Track restored attrs as user-defined so they persist
                # through subsequent save cycles.
                user_keys = getattr(self, "_user_private_keys", None)
                if user_keys is not None:
                    user_keys.add(key)

    # ============================================================================
    # STATE SNAPSHOT
    # ============================================================================

    def _capture_snapshot_state(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of public view state.

        Explicit policy returns only declared ``persist="client"`` values,
        detached and bounded. It does not infer any component state. The
        returned dict is not a signed restore capability; explicit restoration
        uses the bound snapshot adapter.

        Legacy policy filters out private (``_``-prefixed) attributes, framework-internal
        attrs enumerated in ``_FRAMEWORK_INTERNAL_ATTRS``, callables, and any
        value that fails a ``DjangoJSONEncoder`` round-trip. Time-travel
        recording uses it; the snapshot is lossy by design (a model comes
        back as a disconnected dict).

        Component state (#1041, v0.9.0): when this view has registered
        :class:`~djust.components.LiveComponent` instances in
        ``self._components`` (the registry populated by
        :meth:`_assign_component_ids`), each component's public state
        is captured under the special ``"__components__"`` key as a
        ``{component_id: {field: value}}`` nested dict. The reserved
        name keeps component snapshots out of the parent's flat state
        namespace and gives the time-travel debug panel a clean shape
        to render per-component scrubbers.
        """
        from ._exposure import explicit_state_projection, uses_legacy_exposure

        if not uses_legacy_exposure(self):
            return explicit_state_projection(self, "snapshot")

        result: Dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                continue
            if key in _FRAMEWORK_INTERNAL_ATTRS:
                continue
            if callable(value):
                continue
            try:
                # json.dumps serves as the serializability check; the
                # accompanying json.loads round-trips to a *disconnected*
                # value so later in-place mutations to the source object
                # (e.g. ``self.items.append(...)``) do not retroactively
                # rewrite this snapshot. Without the round-trip the
                # time-travel state_before / state_after fields aliased
                # the live view attrs — see Stage 11 Fix B.
                result[key] = json.loads(json.dumps(value, cls=StateRoundtripJSONEncoder))
            except (TypeError, ValueError, OverflowError):
                # Skip non-serializable — matches _get_private_state pattern.
                continue

        # Component-level state (#1041). Each registered component
        # contributes its own public-state dict under
        # ``__components__`` keyed by ``component_id``.
        components_state = self._capture_components_snapshot()
        if components_state:
            result["__components__"] = components_state
        return result

    def _capture_components_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Return a ``{component_id: {field: value}}`` snapshot map.

        Walks ``self._components`` (the registry of LiveComponent
        instances populated by ``_assign_component_ids``) and captures
        each component's public state. Returns an empty dict when the
        registry is empty or absent — no key is added to the parent
        snapshot in that case.

        Capture rules per component:
        - Public (non-``_``-prefixed) attrs from ``component.__dict__``.
        - Skip callables and non-serializable values (same rules as
          parent state).
        - Skip ``_descriptor_*`` machinery and other framework-
          internal attrs that begin with ``_``.
        - Skip framework-internal config attrs that DON'T start with
          ``_`` but aren't user state (see
          :data:`_COMPONENT_INTERNAL_ATTRS`). ``component_id`` in
          particular is the registry key — restoring it from stale
          state would desync the registry from the instance attr.

        Failures on individual components are logged and the bad
        component is skipped — degrade gracefully rather than break
        the whole snapshot.

        This legacy reflective helper is unavailable under explicit policy.
        A component descriptor does not grant permission to export its state.
        """
        from ._exposure import require_legacy_state_api

        require_legacy_state_api(self)
        registry = getattr(self, "_components", None)
        if not registry:
            return {}
        components_state: Dict[str, Dict[str, Any]] = {}
        from .components.base import BoundComponent
        from .components._interactive import DropdownMenu

        for component_id, component in registry.items():
            try:
                comp_state: Dict[str, Any] = {}
                # ADR-031: a bound component's public state IS its State dict.
                items = (
                    component.state.items()
                    if isinstance(component, (BoundComponent, DropdownMenu))
                    else component.__dict__.items()
                )
                for key, value in items:
                    if key.startswith("_"):
                        continue
                    if key in _COMPONENT_INTERNAL_ATTRS:
                        continue
                    if callable(value):
                        continue
                    try:
                        comp_state[key] = json.loads(
                            json.dumps(value, cls=StateRoundtripJSONEncoder)
                        )
                    except (TypeError, ValueError, OverflowError):
                        # Skip non-serializable — matches parent rule.
                        continue
                components_state[component_id] = comp_state
            except Exception:  # noqa: BLE001 — dev-only, log + degrade
                logger.exception(
                    "time_travel: component snapshot failed for id=%s",
                    component_id,
                )
        return components_state

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

        It runs on a WebSocket/SSE mount of a view whose declared
        ``state(persist=...)`` fields were restored (after ``mount()``, with
        the restored values in place), on an explicit child embedded with
        ``{% live_render %}`` the same way, and on the HTTP page-POST fallback
        (after the object permission check, before the event handler). It does
        not run after a plain ``mount()``. May be ``async def``.

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

    # ============================================================================
    # STATE SERIALIZATION VALIDATION
    # ============================================================================

    @staticmethod
    def _is_serializable(value: Any) -> bool:
        """Check if a value can be safely serialized to JSON for state transfer.

        Returns True for primitives, collections, Django models/QuerySets, and
        any value that json.dumps can handle. Returns False for service instances,
        connections, file handles, threads, and other non-serializable objects.
        """
        # Primitives are always fine
        if value is None or isinstance(value, (str, int, float, bool)):
            return True

        # Collections: check recursively would be expensive; allow them and
        # let actual serialization catch nested issues
        if isinstance(value, (list, tuple, set, frozenset)):
            return True

        if isinstance(value, dict):
            return True

        # Django models and QuerySets are serialized by JIT pipeline
        try:
            from django.db import models
            from django.db.models import QuerySet

            if isinstance(value, (models.Model, QuerySet)):
                return True
        except ImportError:
            pass  # Django ORM not available; skip model/queryset check

        # ADR-033 D6: a plain component is its state, and the state round
        # trip carries it (``component_for_state_roundtrip``).
        from .components.base import Component

        if isinstance(value, Component):
            return True

        # Non-serializable types: file handles, threads, locks, sockets
        _non_serializable: tuple[type, ...] = (io.IOBase, threading.Thread, socket.socket)
        try:
            # threading.Lock() returns _thread.lock which isn't directly a type
            import _thread

            _non_serializable = _non_serializable + (_thread.LockType,)
        except (ImportError, AttributeError):
            pass  # _thread.LockType unavailable on some platforms; skip lock detection
        if isinstance(value, _non_serializable):
            return False

        # Detect common service/client patterns by type name
        type_name = type(value).__name__.lower()
        _suspect_names = ("service", "client", "session", "connection", "api")
        if any(name in type_name for name in _suspect_names):
            return False

        # Detect objects with generic repr like '<ClassName object at 0x...>'
        try:
            obj_repr = repr(value)
            if " object at 0x" in obj_repr:
                return False
        except Exception:
            return False

        # Final check: try to actually serialize it
        try:
            json.dumps(value, cls=DjangoJSONEncoder)
            return True
        except (TypeError, ValueError, OverflowError):
            return False

    def get_state(self) -> Dict[str, Any]:
        """Get serializable state from this LiveView instance.

        Explicit policy exports only declared ``client=True`` fields using the
        bounded JSON contract. Rendering context and server-only state are not
        inputs. Returned values are detached; failures never use legacy fallback.

        Legacy policy iterates over public (non-underscore) instance attributes and validates
        that each value can be serialized. In DEBUG mode, raises TypeError with
        a helpful message for non-serializable values. In production, logs an
        error and skips the attribute.

        Returns:
            Client-permitted declared values under explicit policy; serializable
            public state under legacy policy.
        """
        from ._exposure import explicit_state_projection, uses_legacy_exposure

        if not uses_legacy_exposure(self):
            return explicit_state_projection(self, "client")

        from django.conf import settings

        state = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                continue

            # #762: Skip framework-internal attrs so they don't pollute
            # user-facing reactive state.
            if key in _FRAMEWORK_INTERNAL_ATTRS:
                continue

            if callable(value):
                continue

            if not self._is_serializable(value):
                class_name = self.__class__.__name__
                value_type = type(value).__name__
                msg = (
                    f"Non-serializable value in {class_name}.{key}: "
                    f"{value_type} cannot be stored in LiveView state. "
                    f"Service instances, connections, and file handles must "
                    f"be created in event handlers or accessed via utility "
                    f"functions — not stored as instance attributes. "
                    f"See: https://djust.org/docs/guides/services.md"
                )
                if getattr(settings, "DEBUG", False):
                    raise TypeError(msg)
                else:
                    logger.error(msg)
                    continue

            state[key] = value

        return state

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


def live_view(
    template_name: Optional[str] = None, template: Optional[str] = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to convert a function-based view into a LiveView.

    Usage:
        @live_view(template_name='counter.html')
        def counter_view(request):
            count = 0

            def increment():
                nonlocal count
                count += 1

            def decrement():
                nonlocal count
                count -= 1

            return locals()

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
                # The decorated function's returned locals are set as view
                # attributes below and rendered by the attribute walk.
                exposure_policy = "legacy"

            if template_name:
                DynamicLiveView.template_name = template_name
            if template:
                DynamicLiveView.template = template

            view = DynamicLiveView()

            # Execute the function to get initial state
            result = func(request, *args, **kwargs)
            if isinstance(result, dict):
                for key, value in result.items():
                    if not callable(value):
                        setattr(view, key, value)
                    else:
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
