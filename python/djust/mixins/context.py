"""
ContextMixin - Context data management for LiveView.
"""

import itertools
import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from django.db import models
from django.test.signals import setting_changed
from django.utils.datastructures import MultiValueDict

from .._class_snapshot import namespace as _class_namespace
from .._exposure_providers import STREAMS_PROVIDER, actions_provider, components_provider
from ..serialization import _crosses_as_encoded, normalize_django_value
from ..utils import is_model_list

logger = logging.getLogger(__name__)


def _rows_lack_optimization(rows: List[models.Model], optimization: Any) -> bool:
    """Whether any row in ``rows`` is missing a relation cache / annotation
    the template paths need (#2536). ``select_related`` paths are checked
    through ``_state.fields_cache`` segment by segment, ``prefetch_related``
    through ``_prefetched_objects_cache``, annotations as attributes."""
    for row in rows:
        for path in optimization.select_related:
            obj: Any = row
            for seg in path.split("__"):
                cache = getattr(getattr(obj, "_state", None), "fields_cache", None)
                if cache is None or seg not in cache:
                    return True
                obj = cache[seg]
                if obj is None:
                    break
        prefetched = getattr(row, "_prefetched_objects_cache", None) or {}
        for path in optimization.prefetch_related:
            if path.split("__", 1)[0] not in prefetched:
                return True
        for name in optimization.annotations:
            if not hasattr(row, name):
                return True
    return False


def _graft_optimization_caches(
    rows: List[models.Model], fetched_by_pk: Dict[Any, models.Model], optimization: Any
) -> None:
    """Copy the relation caches / annotations from freshly-fetched instances
    onto the caller's instances, in place (#2536). The caller's instances stay
    the ones rendered, so a mutation made on them survives; a row the query
    no longer returns keeps its in-memory state, as Django's engine would."""
    for row in rows:
        fetched = fetched_by_pk.get(row.pk)
        if fetched is None:
            continue
        src_cache = getattr(fetched._state, "fields_cache", None) or {}
        dst_cache = row._state.fields_cache
        for path in optimization.select_related:
            seg = path.split("__", 1)[0]
            if seg in src_cache:
                dst_cache[seg] = src_cache[seg]
        src_prefetch = getattr(fetched, "_prefetched_objects_cache", None)
        if src_prefetch:
            dst_prefetch = getattr(row, "_prefetched_objects_cache", None)
            if dst_prefetch is None:
                row._prefetched_objects_cache = dict(src_prefetch)
            else:
                dst_prefetch.update(src_prefetch)
        for name in optimization.annotations:
            if hasattr(fetched, name):
                setattr(row, name, getattr(fetched, name))


# Module-level cache for context processors, keyed by TEMPLATES config tuple
_context_processors_cache: Dict[Any, List[Any]] = {}

# Module-level cache for resolved processor callables, keyed by tuple of processor paths
_resolved_processors_cache: Dict[Tuple[Any, ...], List[Callable[..., Any]]] = {}


def _clear_processor_caches(**kwargs: Any) -> None:
    """Clear caches when settings change (e.g., during @override_settings in tests)."""
    if kwargs.get("setting") == "TEMPLATES":
        _context_processors_cache.clear()
        _resolved_processors_cache.clear()


setting_changed.connect(_clear_processor_caches)


#: Types that can never hold a Model or QuerySet. A scan of a long list of
#: plain rows exits on this one ``type()`` lookup per value instead of three
#: ``isinstance`` calls (#3788).
_NEVER_DB_TYPES = frozenset({str, int, float, bool, bytes, type(None)})

#: How many containers deep ``_holds_db_value`` looks. ``{"d": {"rows":
#: [{"users": qs}]}}`` is three; past this the value is left as the view built
#: it, as ``_normalize_db_values`` does at its own bound.
_NESTED_DB_DEPTH = 4


#: Set once the bound has been reported (#3801); the warning is per process.
_DEPTH_BOUND_REPORTED: dict[str, bool] = {"reported": False}

#: How many values past the bound are looked at to learn whether a Model or
#: QuerySet is there. The report is a hint, so it stays cheap on a long list.
_DEPTH_BOUND_PROBE = 64


def _warn_once_if_deeper_db_value(items: Any) -> None:
    """Say, once, that a Model / QuerySet sits past ``_NESTED_DB_DEPTH`` (#3801).

    Past the bound a container is left as the view built it -- a flat field dict
    whose relations render empty -- and nothing said so. Only the values directly
    inside the first container past the bound are looked at (at most
    ``_DEPTH_BOUND_PROBE``): finding a deeper one would mean the scan the bound
    exists to avoid, so a value deeper still is not reported.
    """
    if _DEPTH_BOUND_REPORTED["reported"]:
        return
    for item in itertools.islice(items, _DEPTH_BOUND_PROBE):
        if isinstance(item, (models.Model, models.QuerySet)):
            _DEPTH_BOUND_REPORTED["reported"] = True
            logger.warning(
                "A %s is nested more than %s containers deep in the context, past what the "
                "template serializer looks at: it is sent as a flat field dict and its "
                "relations render empty. Hoist it to a shallower name.",
                type(item).__name__,
                _NESTED_DB_DEPTH,
            )
            return


def _holds_db_value(value: Any, depth: int = 0) -> bool:
    """Whether a dict / list holds a Model or QuerySet at any bounded depth (#3788).

    ``{"d": {"users": qs}}`` and ``[{"label": "a", "users": qs}]`` are the
    shapes a view builds to group rows. The JIT only looked at top-level
    context values, so a QuerySet reached through a container was serialized
    as a flat field dict and every relation the template walked
    (``{% for g in x.groups.all %}``) came out empty. Plain data answers on a
    single ``type()`` lookup per value.
    """
    t = type(value)
    if t in _NEVER_DB_TYPES:
        return False
    items: Any
    if t is dict:
        items = value.values()
    elif t is list:
        items = value
    elif isinstance(value, (models.Model, models.QuerySet)):
        return True
    elif isinstance(value, dict):
        if isinstance(value, MultiValueDict) or _crosses_as_encoded(value):
            return False
        items = value.values()
    elif isinstance(value, list):
        items = value
    else:
        return False
    if depth >= _NESTED_DB_DEPTH:
        _warn_once_if_deeper_db_value(items)
        return False
    depth += 1
    for item in items:
        # Inlined leaf test: a call per value is what a long list of plain rows
        # would otherwise pay (measured 20 ms for 10,000 rows of 8 values).
        if type(item) not in _NEVER_DB_TYPES and _holds_db_value(item, depth):
            return True
    return False


def _jit_serialization_enabled() -> bool:
    """``LIVEVIEW_CONFIG['jit_serialization']`` (default ``True``).

    ``False`` skips JIT serialization (#2984; the key used to be read by
    nothing): top-level Models, QuerySets and model lists then skip the JIT
    serializer. Models are serialized
    by ``normalize_django_value`` (every concrete field, minus the sensitive
    denylist) instead of the fields the template reads, and no
    ``select_related`` / ``prefetch_related`` is added.
    """
    from ..config import config

    return config.get("jit_serialization", True) is not False


def _contains_orm_value(value: Any, seen: set[int]) -> bool:
    """Find ORM inputs without evaluating QuerySets or recursing through cycles."""
    from django.db.models import QuerySet

    if isinstance(value, (models.Model, QuerySet)):
        return True
    if isinstance(value, (dict, list, tuple)):
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        members = value.values() if isinstance(value, dict) else value
        return any(_contains_orm_value(member, seen) for member in members)
    return False


def _guard_unserialized_recursive_models(
    view: Any, context: Dict[str, Any], *, nested_only: bool
) -> None:
    """Fail closed when recursive ORM inputs bypass scoped JIT serialization."""
    from django.db.models import QuerySet

    name = getattr(view, "template_name", None)
    sources = (
        getattr(view, "_named_partial_jit_source", None),
        getattr(view, "_full_template", None),
        getattr(view, "template", None),
    )
    available_sources = [source for source in sources if isinstance(source, str)]
    possible_partial = (isinstance(name, str) and "#" in name) or any(
        any(word in source for word in ("partial", "#", "include", "extends"))
        for source in available_sources
    )
    if not possible_partial and available_sources:
        # Known source without partials or dependencies cannot contain a
        # recursive partial; don't scan its context on this hot path.
        return
    if not possible_partial and not isinstance(name, str):
        return
    # An as-yet-unloaded file might include or inherit a named partial. Only
    # resolve it if the context scan below finds an ORM input at risk.

    content = None
    for key, value in context.items():
        if nested_only and (isinstance(value, (models.Model, QuerySet)) or is_model_list(value)):
            continue
        if not _contains_orm_value(value, set()):
            continue
        if content is None:
            from ..mixins.jit import _jit_template_content

            content = _jit_template_content(view) or ""
            if "djust:recursive-partial" not in content:
                return
        from ..mixins.jit import _recursive_queryset_relation

        try:
            related = bool(
                _recursive_queryset_relation(content, key)
                or _recursive_queryset_relation(content, key, single=True)
            )
        except ValueError:
            # The recognizer detected an unsupported use of this root.
            related = True
        if related:
            raise RuntimeError(
                "Recursive named partial ORM values require scoped JIT serialization; "
                "nested contexts, explicit exposure and disabled JIT are unsupported"
            )


def _is_json_serializable(value: Any) -> bool:
    """Return True if *value* can survive a JSON round-trip.

    Used to filter class-level attributes out of template context when
    they are not serializable (#694).  Primitive types, dicts, and lists
    are checked structurally; everything else goes through a fast
    ``json.dumps`` probe.
    """
    # Fast path for common types
    if isinstance(value, (str, int, float, bool, type(None))):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_serializable(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_serializable(v) for k, v in value.items())
    # Fallback: try encoding — catches dataclasses, custom objects, etc.
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError, OverflowError):
        return False


def _keeps_out_of_state(value: Any) -> bool:
    """Whether a REQUEST-SCOPED context value must stay out of the Rust state.

    Two kinds: a value that is not JSON data (``WSGIRequest``, ``PermWrapper``,
    ``FallbackStorage``, a lazy ``user``, a lazy HTML chunk), which the normalizer
    would stringify, and plain data a processor marked ``RequestScopedList``
    because storing it is the cost (#3631). Both ride the raw-value sidecar.
    Every site that decides this asks here, so the dj-root render, the page-shell
    render and the sidecar builder cannot disagree (#1646).
    """
    from ..request_scoped import is_request_scoped_data

    return is_request_scoped_data(value) or not _is_json_serializable(value)


def _request_scoped_keys(view: Any) -> set:
    """Context keys that are request-scoped, not LiveView state (#1786, #3061).

    ``request``, every key ``_apply_context_processors`` added on its last
    call (``self._context_processor_keys``: auth ``user`` / ``perms``, the
    messages storage, ``csrf_token``, ...), and the processor keys the HTTP
    POST fallback injected as attributes. A key the view supplied itself is
    in neither set, since processors only add missing keys.
    """
    from .._exposure_providers import PROCESSOR_KEYS_ATTR

    keys = set(getattr(view, "_context_processor_keys", ()) or ())
    # The HTTP POST fallback injects processor output as view attributes for
    # the duration of its render (``RequestMixin._processor_context``, #717);
    # ``_apply_context_processors`` then adds nothing, so those keys come
    # from the marker instead.
    keys.update(view.__dict__.get(PROCESSOR_KEYS_ATTR, ()) if hasattr(view, "__dict__") else ())
    keys.add("request")
    return keys


def legacy_render_only_keys(view: Any) -> frozenset:
    """Context keys a legacy session save must drop: they render, never persist."""
    keys = getattr(view, "_djust_render_only_context_keys", None)
    return frozenset(keys()) if callable(keys) else frozenset()


def legacy_unsaved_keys(view: Any, context: Dict[str, Any]) -> frozenset:
    """The keys of ``context`` a legacy session save drops.

    A legacy save keeps the view's state: the keys the base
    ``get_context_data()`` built from the view's attributes. It drops

    - the render-only keys (:meth:`ContextMixin.get_render_only_context`, the
      framework's private ``_djust_render_only_context`` hooks), and
    - every key an override added on top of ``super().get_context_data()``.
      Such a value is computed again on every render, so saving it only
      copied derived data (a query set's rows with every column, content
      that must not reach the session) into ``django_session`` and set it
      back as an attribute on restore (Point Guard N-31 and Phase 2
      decision 185).

    Every legacy save site (the HTTP GET, the page-POST fallback, the socket
    turn's save, a lazy child) filters through this one function.
    """
    unsaved = set(legacy_render_only_keys(view))
    recorded = getattr(view, "__dict__", {})
    unsaved.update(recorded.get("_djust_render_only_keys", ()))
    base = recorded.get("_djust_state_context_keys")
    if base is not None:
        unsaved.update(key for key in context if key not in base)
    return frozenset(unsaved)


def _drop_request_scoped_values(view: Any, context: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``context`` without its non-serializable request-scoped values.

    Those values (``WSGIRequest`` / ``ASGIRequest``, ``PermWrapper``,
    ``FallbackStorage``, a lazy ``user``) are not LiveView state: running
    them through ``normalize_django_value`` logs a misleading
    "non-serializable value" warning and stringifies them. Every render path
    that feeds a context to the normalizer uses this, so the dj-root render
    (``_sync_state_to_rust``) and the page-shell render
    (``render_full_template``) agree (#3061, the #1646 parallel-path class).
    The values still reach the template through the raw-value sidecar.
    Serializable processor output (``csrf_token``, ``DEFAULT_MESSAGE_LEVELS``,
    a theming ``SafeString``) is kept, except plain data a processor marked
    ``RequestScopedList`` (``theme_presets``, #3631): it is left out here and
    reaches the template through the sidecar, as in ``_sync_state_to_rust``.
    """
    scoped = _request_scoped_keys(view)
    return {
        key: value
        for key, value in context.items()
        if key not in scoped or not _keeps_out_of_state(value)
    }


_LIVE_VIEW_MRO: "Optional[frozenset]" = None


def _live_view_mro() -> frozenset:
    """``LiveView``'s MRO: the framework-owned classes whose attributes are
    configuration, never legacy template context (#2960). Cached; imported
    lazily because ``live_view`` imports this module."""
    global _LIVE_VIEW_MRO
    if _LIVE_VIEW_MRO is None:
        from ..live_view import LiveView

        _LIVE_VIEW_MRO = frozenset(LiveView.__mro__)
    return _LIVE_VIEW_MRO


class ContextMixin:
    """Context methods: get_context_data, _get_context_processors, _apply_context_processors."""

    # ADR-038 E2-0: the providers the explicit base context renders.
    _djust_context_providers = (components_provider, actions_provider, STREAMS_PROVIDER)

    if TYPE_CHECKING:
        # Cooperating attributes/methods supplied by the host class (LiveView)
        # and sibling mixins. Declared type-only so the strict-island mypy run
        # resolves them on this mixin without a runtime change — the real
        # definitions live on LiveView / the other mixins (this mixin is never
        # instantiated standalone). See streaming.py for the same pattern.
        _cached_context: Optional[Dict[str, Any]]

        def _register_component(self, component: Any, attr_name: Optional[str] = None) -> None: ...

        def _get_template_content(self) -> Optional[str]: ...

        def _jit_serialize_queryset(
            self, queryset: Any, template_content: str, variable_name: str
        ) -> List[Any]: ...

        def _jit_serialize_model(
            self, obj: Any, template_content: str, variable_name: str
        ) -> Dict[str, Any]: ...

        def _jit_serialize_model_list(
            self, items: Any, template_content: str, variable_name: str
        ) -> List[Any]: ...

    def _context_for_render(
        self, preloaded_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """The context a render pass starts from, without evaluating it twice (#3725).

        The one place the render path decides where its context comes from,
        in this order:

        1. ``preloaded_context``: the caller already has it (the async path
           awaited it).
        2. ``self._cached_context``: the HTTP GET evaluated ``get_context_data()``
           once (``RequestMixin._get_turn_body``) and cached it for exactly this
           render, to keep QuerySets and the view's own work from running again.
           The stock ``get_context_data()`` honours that cache itself, but the
           short-circuit sits inside the stock method, so a view that defines
           its own ``get_context_data()`` ran its whole body a second time.
           Reading the cache here, before the method is reached, serves both.
           A legacy view gets a shallow copy of the cached dict; an explicit
           view gets a fresh render context rebuilt from it (provider
           ownership intact).
        3. A real evaluation, in the sync-turn ``render`` step. This is every
           render after the GET: an event handler changed state, so the context
           must be computed again. The GET clears the cache when it finishes
           for that reason.
        """
        if preloaded_context is not None:
            return preloaded_context
        cached = getattr(self, "_cached_context", None)
        if cached is not None:
            from .._exposure import uses_legacy_exposure

            if uses_legacy_exposure(self):
                # A copy: the render adds keys to the dict it is handed and
                # replaces models in it with their serialized form. The cache
                # stays what the view returned, because the page shell reads
                # RAW values from it for its sidecar (``_set_shell_sidecar``,
                # #2589). Every evaluation returned a fresh dict before this
                # read the cache; a copy keeps that for a view with its own method.
                return dict(cached)
            return self._get_explicit_context_data()
        from ..sync_turn import run_sync_step

        return run_sync_step(self, "render", self.get_context_data)

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Get the context data for rendering. Override to customize context.

        Returns:
            Dictionary of context variables
        """
        from .._exposure import uses_legacy_exposure

        if not uses_legacy_exposure(self):
            explicit_context = self._get_explicit_context_data(**kwargs)
            _guard_unserialized_recursive_models(self, explicit_context, nested_only=False)
            return explicit_context
        # Return cached context if available (set during GET request to avoid
        # redundant QuerySet evaluation across sync_state_to_rust/render_with_diff)
        if hasattr(self, "_cached_context") and self._cached_context is not None:
            return self._cached_context
        # Reset the per-render ``unique_id()`` counter (v0.5.1) so IDs are
        # stable across successive renders of the same logical position.
        reset_ids = getattr(self, "reset_unique_ids", None)
        if callable(reset_ids):
            reset_ids()
        # Reset context providers at the start of each render so
        # ``provide_context`` from a previous render doesn't leak into this one.
        clear_providers = getattr(self, "clear_context_providers", None)
        if callable(clear_providers):
            clear_providers()

        from ..components.base import BoundComponent, Component, LiveComponent
        from django.db.models import QuerySet

        context: Dict[str, Any] = {}

        # Skip static assigns after first render — Rust retains them
        _static_skip = set()
        if getattr(self, "_static_assigns_sent", False):
            _static_skip = set(getattr(self, "static_assigns", []))

        # Collect non-private, non-callable attributes as context.
        #
        # Two sources:
        #   1. Instance attributes (self.__dict__) — set in mount(), event handlers, etc.
        #   2. Class-level attributes from user's view class(es) — class Foo(LiveView): x = 1
        #
        # We avoid dir(self) which traverses the ENTIRE MRO including Django's
        # View hierarchy (~300 inherited attrs, ~50ms overhead from getattr calls
        # triggering descriptors).
        import types
        from django.http import HttpRequest

        _SKIP_TYPES = (
            types.FunctionType,
            types.MethodType,
            types.ModuleType,
            type,
            types.BuiltinFunctionType,
            HttpRequest,
        )

        # Build set of all candidate keys: instance attrs + user class attrs.
        # Walk MRO up to (but not including) ContextMixin and its bases —
        # these are framework classes whose attrs are never template context.
        # #2960: ``LiveView`` and the mixins listed before ``ContextMixin`` in
        # its bases come EARLIER in the MRO, so they are skipped too — their
        # attributes are configuration defaults (``template``,
        # ``login_required``, ``use_actors``, ``sticky``...), not view data.
        # This is the boundary the ADR-038 inventory draws (``_user_bases``);
        # classes the application declares are still walked.
        _seen = set()
        _framework_bases = set(ContextMixin.__mro__)
        _framework_owned = _live_view_mro()

        _all_items = list(self.__dict__.items())
        for cls in type(self).__mro__:
            if cls in _framework_bases:
                break
            if cls in _framework_owned:
                continue
            # A snapshot: another thread may be writing a first-use cache to
            # this class while it is walked (#3151).
            for key, value in _class_namespace(cls).items():
                if key not in _seen and key not in self.__dict__:
                    _seen.add(key)
                    # Skip framework-defined derived properties (``is_dirty``,
                    # ``changed_fields``) — they are derived state and must not
                    # round-trip through session storage. Detected via an
                    # opt-in marker ``_djust_framework_derived = True`` set on
                    # the property descriptor by ``live_view.py``. User-defined
                    # ``@property`` attributes and ``@computed`` properties are
                    # unaffected — they flow to template context as before.
                    if getattr(value, "_djust_framework_derived", False):
                        continue
                    # For descriptors (LiveComponent with __get__), resolve
                    # through the instance so __get__ returns the State dict
                    # instead of the descriptor itself.
                    if hasattr(value, "__get__") and hasattr(value, "__set_name__"):
                        try:
                            value = getattr(self, key)
                        except Exception as exc:
                            # Descriptor resolution is best-effort; keep raw class-level value on failure.
                            logger.debug("Descriptor resolution failed for %s: %s", key, exc)
                    _all_items.append((key, value))

        # Collect keys that came from class-level attrs (not instance __dict__)
        _class_level_keys = _seen

        # Mixin configuration and framework slots a mixin declares (ADR-035's
        # edit adapter: ``model``, ``queryset``, ``kwargs``, ...) are never
        # inferred rendering assigns, so never persisted from this context.
        _configuration: frozenset = getattr(type(self), "_djust_configuration_names", frozenset())
        for key, value in _all_items:
            if key.startswith("_"):
                continue
            if key == "exposure_policy" or key in _configuration:
                # Policy configuration is never an inferred rendering assign.
                continue
            if key in _static_skip:
                continue
            if callable(value):
                continue
            if isinstance(value, (Component, LiveComponent)):
                if isinstance(value, LiveComponent):
                    self._register_component(value, attr_name=key)
                context[key] = value
            elif getattr(type(value), "_djust_component_collection", False):
                # ADR-034 C3: a bound keyed collection renders its members
                # (``{% for menu in row_menus.values %}``).
                context[key] = value
            elif isinstance(value, BoundComponent):
                # ADR-031: a class-level component resolved through ``__get__``
                # above. It is not JSON-serializable itself (the #694 gate
                # below would drop it); ``normalize_django_value`` carries it
                # as its State.
                context[key] = value
            elif not isinstance(value, _SKIP_TYPES):
                # For class-level attributes, skip values that are not
                # JSON-serializable (#694). Prevents non-serializable objects
                # (e.g. TutorialStep dataclasses) from being converted to
                # their str() repr by the serializer, which corrupts state.
                # Django QuerySets and Models are exempt — they have their
                # own serialization pipeline (JIT / normalize_django_value).
                if key in _class_level_keys:
                    if not isinstance(value, (QuerySet, models.Model)) and not is_model_list(value):
                        if not _is_json_serializable(value):
                            continue
                context[key] = value

        # Render-only values: a mixin's (ADR-035's managed ``object``) and the
        # view's own (``get_render_only_context``). Added before model
        # serialization so they render like any other model;
        # ``legacy_unsaved_keys`` keeps them out of every session save.
        render_only_keys: set = set()
        render_only = getattr(self, "_djust_render_only_context", None)
        if callable(render_only):
            items = render_only()
            context.update(items)
            render_only_keys.update(items)
        items = self.get_render_only_context()
        if items:
            context.update(items)
            render_only_keys.update(items)

        # Whole-model fallback cannot correctly represent recursive partials.
        # Reject only inputs outside the scoped top-level JIT path.
        _guard_unserialized_recursive_models(
            self, context, nested_only=_jit_serialization_enabled()
        )

        # JIT auto-serialization for QuerySets and Models
        jit_serialized_keys = set()
        template_content = None
        # The raw rows a template's QuerySet-method read is answered from
        # (#3801); rebuilt every render, never state.
        self.__dict__["_all_method_rows"] = {}

        # Short-circuit: skip JIT pipeline if no DB objects in context (#278).
        # NOTE: This scan only checks top-level context values. DB objects nested
        # inside dicts will NOT trigger JIT; they are handled later by
        # _deep_serialize_dict() which falls back to DjangoJSONEncoder when
        # template_content is None.
        has_db_values = False
        if _jit_serialization_enabled():
            for val in context.values():
                if isinstance(val, (QuerySet, models.Model)):
                    has_db_values = True
                    break
                if is_model_list(val):
                    has_db_values = True
                    break

        if has_db_values:
            try:
                from ..mixins.jit import _jit_template_content

                template_content = _jit_template_content(self)
                if template_content:
                    # Extract variable paths once for list[Model] optimization
                    from ..mixins.jit import _cached_extract_template_variables

                    variable_paths_map = _cached_extract_template_variables(template_content)

                    # Template hash once for codegen cache keys
                    from ..mixins.jit import _template_hash
                    from ..session_utils import _jit_serializer_cache, _get_model_hash
                    from ..optimization.codegen import (
                        all_methods_need_rows,
                        compile_serializer,
                        generate_serializer_code,
                    )
                    from ..mixins.jit import keep_rows_for_all_methods

                    template_hash = _template_hash(template_content)

                    for key, value in list(context.items()):
                        if isinstance(value, QuerySet):
                            serialized = self._jit_serialize_queryset(value, template_content, key)
                            context[key] = serialized
                            jit_serialized_keys.add(key)

                            if isinstance(serialized, list):
                                count_key = f"{key}_count"
                                if count_key not in context:
                                    context[count_key] = len(serialized)

                        elif isinstance(value, models.Model):
                            context[key] = self._jit_serialize_model(value, template_content, key)
                            jit_serialized_keys.add(key)

                        elif is_model_list(value):
                            # Recursive partial rows must use the same scoped,
                            # cycle-checked serializer as QuerySets.
                            from ..mixins.jit import _recursive_queryset_relation

                            if _recursive_queryset_relation(template_content, key):
                                context[key] = self._jit_serialize_model_list(
                                    value, template_content, key
                                )
                                jit_serialized_keys.add(key)
                                continue
                            # Re-fetch with select_related/prefetch_related/annotations
                            # to avoid N+1 queries during serialization
                            from ..optimization.query_optimizer import (
                                analyze_queryset_optimization,
                                optimize_queryset,
                            )

                            model_class = value[0].__class__
                            paths = variable_paths_map.get(key, []) if variable_paths_map else []
                            optimization = (
                                analyze_queryset_optimization(model_class, paths) if paths else None
                            )

                            # #2536: re-query ONLY when some row lacks what the
                            # template paths need, and graft the fetched
                            # relation caches / annotations onto the USER'S
                            # instances instead of replacing them. A static
                            # list then costs one query for the whole session
                            # (zero when the view already ``select_related``),
                            # and an in-memory row mutation is rendered rather
                            # than discarded by a fresh instance winning.
                            if (
                                optimization
                                and (
                                    optimization.select_related
                                    or optimization.prefetch_related
                                    or optimization.annotations
                                )
                                and _rows_lack_optimization(value, optimization)
                            ):
                                pks = [obj.pk for obj in value]
                                qs = model_class._default_manager.filter(pk__in=pks)
                                qs = optimize_queryset(qs, optimization)
                                pk_map = {obj.pk: obj for obj in qs}
                                _graft_optimization_caches(value, pk_map, optimization)

                            if paths:
                                # Use codegen serializer directly — avoids DjangoJSONEncoder fallback
                                model_hash = _get_model_hash(model_class)
                                cache_key = (template_hash, key, model_hash, "list")
                                if cache_key in _jit_serializer_cache:
                                    serializer, _ = _jit_serializer_cache[cache_key]
                                else:
                                    func_name = f"serialize_{key}_{template_hash}"
                                    code = generate_serializer_code(
                                        model_class.__name__, paths, func_name
                                    )
                                    serializer = compile_serializer(code, func_name)
                                    _jit_serializer_cache[cache_key] = (serializer, None)
                                context[key] = [serializer(item) for item in value]
                                if all_methods_need_rows(model_class, paths):
                                    keep_rows_for_all_methods(self, key, value)
                            else:
                                # No field path for this list: the QuerySet
                                # contract, not the single-model identity map
                                # (#2736).
                                context[key] = self._jit_serialize_model_list(
                                    value, template_content, key
                                )
                            jit_serialized_keys.add(key)
            except Exception as e:
                if template_content and "djust:recursive-partial" in template_content:
                    # Falling back to whole-model normalization here would
                    # silently lose recursion and expose unreferenced fields.
                    raise
                logger.warning("JIT auto-serialization failed: %s", e, exc_info=True)

        # Auto-add count for plain lists
        for key, value in list(context.items()):
            if isinstance(value, list) and not key.endswith("_count"):
                count_key = f"{key}_count"
                if count_key not in context:
                    context[count_key] = len(value)

        # Single pass: deep-serialize dicts and fallback-serialize remaining Models
        tc = template_content
        # The JIT's template source for values reached THROUGH a container is
        # looked up on first need (#3788), not by a pre-scan of every context
        # value: a context with no nested DB value pays nothing.
        self._nested_template_source: List[Optional[str]] = [] if tc is None else [tc]
        for key, value in list(context.items()):
            if key in jit_serialized_keys:
                continue
            if isinstance(value, MultiValueDict):
                # A `QueryDict` / `MultiValueDict` stays raw (#2556): the
                # `dict` rebuild below would keep only the last value per
                # key, and `{% querystring my_qd a=2 %}` needs the object —
                # its multi-values and `.urlencode()`. `_sync_state_to_rust`
                # carries it in the raw sidecar and hands the template the
                # last-value dict (`normalize_django_value`), the same split
                # `build_render_sidecar` gives the non-LiveView paths.
                continue
            if isinstance(value, dict):
                if _crosses_as_encoded(value):
                    # #2899: a dict SUBCLASS with its own spelling (an
                    # `OrderedDict`, a user class with `__str__`) stays raw
                    # for the same reason the `QueryDict` above does — the
                    # renderer's carrier spells `{{ v }}` as `str(o)`, as
                    # Django does, and reads items through the live object.
                    # The `dict` rebuild below would erase the type.
                    continue
                context[key] = self._deep_serialize_dict(value, tc, key)
            elif isinstance(value, models.Model):
                context[key] = normalize_django_value(value)
            elif is_model_list(value):
                context[key] = [normalize_django_value(item) for item in value]
            elif isinstance(value, list) and _holds_db_value(value):
                # A list of dicts that hold QuerySets / Models (#3788):
                # ``[{"label": "a", "users": qs}]``.
                context[key] = self._deep_serialize_sequence(value, tc, key)

        self._jit_serialized_keys = jit_serialized_keys

        # v0.8.0 — `@action` server-action state injection. Each action's
        # name becomes a context variable so templates can read
        # ``{{ create_todo.pending }}``, ``{{ create_todo.error }}``,
        # ``{{ create_todo.result }}``. Action state is populated by the
        # @action decorator's wrapper at handler entry/exit; this just
        # exposes it to the renderer.
        #
        # Done AFTER the public-attribute walk + JIT serialization so an
        # action name that collides with a user-defined attribute wins
        # — actions are always the canonical reading of that name. Conflict
        # is unlikely (action names are method names) but the precedence
        # is documented.
        action_state = getattr(self, "_action_state", None)
        if action_state:
            for action_name, state in action_state.items():
                context[action_name] = state

        # Streams (#2112). ``self.stream("messages", ...)`` filled
        # ``_streams["messages"].items``, and ``_get_streams_context()`` exists
        # to expose it — its docstring says "Get streams data for template
        # context" — but nothing ever called it, so the documented pattern in
        # docs/website/guides/large-lists.md stored data and rendered nothing.
        #
        # Exposed under a ``streams`` namespace, matching the documented
        # template contract -- ``{% for msg in streams.messages %}`` in
        # docs/website/guides/large-lists.md and in Stream's own docstring.
        # NOT splatted as top-level names: that would be a second, undocumented
        # spelling and would collide with ordinary view attributes.
        #
        # Precedence: LIVE stream data wins whenever the view actually has
        # streams. Deferring to an existing key looks safer but is not — a
        # stale ``streams`` attribute (e.g. restored from a session written by
        # an older release, before the snapshot exclusion in
        # mixins/request.py) would shadow the live data permanently, making
        # every subsequent insert invisible. Silent data loss is worse than
        # overriding a name that is now framework-owned and documented.
        #
        # A view with NO streams is left completely alone, so an app that uses
        # ``self.streams`` for its own purposes and never calls ``stream()``
        # is unaffected.
        get_streams = getattr(self, "_get_streams_context", None)
        if callable(get_streams):
            streams_ctx = get_streams()
            if streams_ctx:
                context["streams"] = streams_ctx

        # What a legacy session save may keep (``legacy_unsaved_keys``): the
        # keys built here from the view's attributes, not the render-only
        # ones (nor the counts derived from them), and not what an override
        # adds after this returns.
        derived = {key + "_count" for key in render_only_keys}
        self._djust_render_only_keys = frozenset(render_only_keys | derived)
        self._djust_state_context_keys = frozenset(
            key for key in context if key not in render_only_keys and key not in derived
        )

        return context

    def get_render_only_context(self) -> Dict[str, Any]:
        """Template values that render but are never saved. Override to add some.

        The returned dict joins the context of every render. Its models,
        QuerySets and lists of models go through the JIT like public
        attributes (only the paths the template reads, with
        ``djust_exclude_fields`` applied), but no session save keeps them:
        not the HTTP GET's, not the page-POST fallback's, not a socket turn's.
        Use it for content that must not persist, or for rows a handler
        already holds on a private attribute::

            def get_render_only_context(self):
                return {"chat": self._rows.chat, "messages": self._rows.messages}

        A key here wins over a public attribute of the same name, and that
        attribute is then not saved either. Called once per render, before
        the JIT runs.
        """
        return {}

    def _get_explicit_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        """Render-only context from deliberate additions and known providers.

        The ``exposure_policy="explicit"`` context (ADR-038). It performs
        no public attribute walk, state projection, or JSON conversion: native
        Django model/queryset values deliberately supplied by the application
        remain native inputs to the renderer. This context must not be reused
        by persistence, client metadata, or debug exporters.
        """
        from inspect import getattr_static

        from .._exposure import ExposureError
        from .._exposure_providers import (
            STREAMS_PROVIDER,
            UPLOADS_PROVIDER,
            instance_assigned_component_error,
            new_render_context,
            registered_components,
        )
        from ..components.base import LiveComponent

        policy = getattr(self, "exposure_policy", None)
        if type(policy) is not str or policy != "explicit":
            raise ExposureError("Unknown context exposure policy")
        if "view" in kwargs or "streams" in kwargs:
            raise ExposureError("Explicit context contains a reserved framework name")
        # Every provider key is declared in the class's manifest (E2-0). The
        # render context enforces ownership so a later application write or
        # kwarg can never silently replace a provider value, or be replaced.
        context = new_render_context(self)
        manifest = context._djust_manifest
        if any(key in manifest for key in kwargs):
            raise ExposureError("Explicit context provider collision")
        cached = getattr(self, "_cached_context", None)
        if cached is not None:
            if type(cached) is not dict or "view" in cached:
                raise ExposureError("Invalid or reserved explicit context cache")
            providers: frozenset[str] = getattr(
                self, "_explicit_context_provider_keys", frozenset()
            )
            if providers.intersection(kwargs):
                raise ExposureError("Explicit context provider collision")
            # Cached provider values keep their owner, so the mixin that
            # provided them may provide them again on this pass.
            for key, value in cached.items():
                if key in providers and key in manifest:
                    context._provide(manifest[key], key, value)
                else:
                    dict.__setitem__(context, key, value)
            context.update(kwargs)
            return context

        reset_ids = getattr(self, "reset_unique_ids", None)
        if callable(reset_ids):
            reset_ids()
        clear_providers = getattr(self, "clear_context_providers", None)
        if callable(clear_providers):
            clear_providers()

        # D-h: a component assigned on the instance is never discovered; say
        # so at the first explicit render instead of rendering nothing.
        misplaced = instance_assigned_component_error(self)
        if misplaced is not None:
            raise misplaced

        # The descriptor registry is a framework declaration manifest, not an
        # attribute discovery mechanism. Reject stale/shadowed entries BEFORE
        # evaluating their descriptor (especially a property shadow). Each
        # declaration's ``__get__`` returns this view's own binding (E2-7).
        for name, declaration in registered_components(type(self)).items():
            if name in ("view", "streams"):
                raise ExposureError("Component context provider uses a reserved name")
            if (
                not (
                    isinstance(declaration, LiveComponent)
                    or getattr(type(declaration), "_djust_component_collection", False)
                )
                or getattr_static(type(self), name) is not declaration
            ):
                raise ExposureError("Component context provider no longer matches its declaration")
            context._provide("djust.components", name, declaration.__get__(self, type(self)))

        # Action/stream render namespaces are registered by their framework
        # APIs. They grant rendering permission only, never persistence/client.
        for name, action_state in (getattr(self, "_action_state", None) or {}).items():
            if type(name) is not str:
                raise ExposureError("Undeclared explicit context provider key")
            context._provide("djust.actions", name, action_state)
        get_streams = getattr(self, "_get_streams_context", None)
        if callable(get_streams):
            streams = get_streams()
            if streams:
                context._provide(STREAMS_PROVIDER.name, "streams", streams)
        # ADR-038 E2-6: UploadMixin's render projection. Resolved here rather
        # than in a mixin get_context_data so both documented MRO orders
        # (``UploadMixin, LiveView`` and ``LiveView, UploadMixin``) render it.
        get_uploads = getattr(self, "_get_upload_context", None)
        if callable(get_uploads):
            for name, value in get_uploads().items():
                context._provide(UPLOADS_PROVIDER.name, name, value)

        self._explicit_context_provider_keys = context.provider_keys()
        # Explicit exposure saves declared server fields only, so the
        # render-only values are just more render context.
        render_only_items = self.get_render_only_context()
        if render_only_items:
            if any(key in context.provider_keys() for key in render_only_items):
                raise ExposureError("Explicit context provider collision")
            context.update(render_only_items)
        context.update(kwargs)
        self._jit_serialized_keys = set()
        return context

    def _nested_template_content(self) -> Optional[str]:
        """The JIT's template source for a value reached through a container.

        ``get_context_data`` only reads the template when a TOP-LEVEL value is
        a Model / QuerySet / model list; a QuerySet inside a dict has none of
        that, and ``_deep_serialize_dict`` then flattened it to a field dict
        (#3788). Read once per render, on the first nested DB value.
        """
        memo: Optional[List[Optional[str]]] = getattr(self, "_nested_template_source", None)
        if memo:
            return memo[0]
        content: Optional[str] = None
        if _jit_serialization_enabled():
            try:
                from ..mixins.jit import _jit_template_content

                content = _jit_template_content(self)
            except Exception as exc:  # noqa: BLE001 - fall back to the flat serializer
                from .._exposure_diagnostics import log_failure_for

                log_failure_for(
                    logger,
                    (self,),
                    exc,
                    "JIT template source unavailable for a nested value",
                    level="debug",
                )
        if memo is not None:
            memo.append(content)
        return content

    def _deep_serialize_sequence(
        self,
        items: List[Any],
        template_content: Optional[str] = None,
        var_name: str = "",
    ) -> List[Any]:
        """A list whose dicts hold Models / QuerySets, serialized item by item (#3788).

        Each dict is named by the LIST's name: the extractor folds the loop
        variable onto its iterable, so ``{% for r in rows %}{% for x in r.users
        %}`` is ``rows: ["users", "users.groups.all", ...]`` -- paths relative
        to one element.
        """
        result: List[Any] = []
        for item in items:
            if isinstance(item, dict) and not _crosses_as_encoded(item):
                result.append(self._deep_serialize_dict(item, template_content, var_name))
            elif isinstance(item, list) and _holds_db_value(item):
                result.append(self._deep_serialize_sequence(item, template_content, var_name))
            elif isinstance(item, models.Model):
                result.append(normalize_django_value(item))
            else:
                result.append(item)
        return result

    def _deep_serialize_dict(
        self,
        d: Dict[str, Any],
        template_content: Optional[str] = None,
        var_name: str = "",
    ) -> Dict[str, Any]:
        """Recursively walk a dict, serializing any Model/QuerySet values found.

        When template_content is provided, uses JIT serialization; otherwise
        falls back to DjangoJSONEncoder. A Model / QuerySet / model list with
        no template source yet asks for it here (#3788): the top-level scan in
        ``get_context_data`` does not see a value inside a dict, and the flat
        fallback has no relation for ``x.groups.all`` to walk.
        """
        from django.db.models import QuerySet

        result: Dict[str, Any] = {}
        for k, v in d.items():
            child_name = f"{var_name}.{k}" if var_name else k
            if template_content is None and (
                isinstance(v, (models.Model, QuerySet)) or is_model_list(v)
            ):
                template_content = self._nested_template_content()
            if isinstance(v, models.Model):
                if template_content:
                    result[k] = self._jit_serialize_model(v, template_content, child_name)
                else:
                    result[k] = normalize_django_value(v)
            elif isinstance(v, QuerySet):
                if template_content:
                    result[k] = self._jit_serialize_queryset(v, template_content, child_name)
                else:
                    result[k] = [normalize_django_value(item) for item in v]
            elif is_model_list(v):
                if template_content:
                    result[k] = self._jit_serialize_model_list(v, template_content, child_name)
                else:
                    result[k] = [normalize_django_value(item) for item in v]
            elif isinstance(v, dict) and _crosses_as_encoded(v):
                # #2899: a nested dict subclass with its own spelling stays
                # raw at every depth, as the top-level one does (#1646).
                result[k] = v
            elif isinstance(v, dict):
                result[k] = self._deep_serialize_dict(v, template_content, child_name)
            elif isinstance(v, list) and _holds_db_value(v):
                result[k] = self._deep_serialize_sequence(v, template_content, child_name)
            else:
                result[k] = v
        return result

    def _get_context_processors(self) -> List[Any]:
        """
        Get context processors from template backend settings.

        Checks DjustTemplateBackend first, then falls back to the standard
        Django template backend so that apps using the default backend still
        get context processors (user, request, messages, etc.) applied.
        """
        from django.conf import settings

        # Use a stable cache key based on actual TEMPLATES config, not id()
        # which can be reused by Python for different objects
        templates = getattr(settings, "TEMPLATES", [])
        cache_key = tuple(t.get("BACKEND", "") for t in templates) if templates else ()

        if cache_key in _context_processors_cache:
            return _context_processors_cache[cache_key]

        # Any DjustTemplateBackend / DjangoTemplates entry, matched by class so
        # every dotted spelling of the backend counts (#3307).
        from ..utils import is_app_dirs_template_backend

        for template_config in getattr(settings, "TEMPLATES", []):
            if is_app_dirs_template_backend(template_config.get("BACKEND")):
                processors: List[Any] = template_config.get("OPTIONS", {}).get(
                    "context_processors", []
                )
                if processors:
                    _context_processors_cache[cache_key] = processors
                    return processors

        _context_processors_cache[cache_key] = []
        return []

    def _apply_context_processors(self, context: Dict[str, Any], request: Any) -> Dict[str, Any]:
        """
        Apply Django context processors to the context.

        Records the set of keys added by context processors on
        ``self._context_processor_keys`` (#1786). These are request-scoped,
        framework-provided values (auth ``user`` / ``perms``, messages
        storage, the ``request`` itself, etc.) that the view never assigns
        to ``self``. ``_sync_state_to_rust`` uses this set — together with
        the ``request`` key — to keep those values out of the persisted
        change-detection fingerprint (``_prev_context_refs``) and out of the
        non-serializable-value warning path: they still reach the Rust
        renderer (serializable ones via ``update_state``; non-serializable
        ones such as ``PermWrapper`` / ``FallbackStorage`` /
        ``SimpleLazyObject`` via the raw-value sidecar), so templates keep
        rendering ``{{ user }}`` / ``{{ perms }}`` / ``{{ messages }}``.
        """
        from .._exposure import ExposureError, uses_legacy_exposure

        if not uses_legacy_exposure(self):
            policy = getattr(self, "exposure_policy", None)
            if type(policy) is not str or policy != "explicit":
                raise ExposureError("Unknown context exposure policy")
            if "view" in context:
                raise ExposureError("Explicit context contains a reserved framework name")
            result = dict(context)
            added: set[str] = set()
            reserved = {"view", "streams"} | set(
                getattr(self, "_explicit_context_provider_keys", ())
            )
            if request is not None:
                for processor in self._get_resolved_processors(self._get_context_processors()):
                    supplied = processor(request)
                    if not supplied:
                        continue
                    for key in reserved.intersection(supplied):
                        # A processor may repeat what the provider supplied:
                        # the same object, or an equal one (the stock
                        # djust.tenants.context_processor re-resolves the
                        # tenant next to TenantMixin). The provider's value is
                        # kept either way; anything else is a collision.
                        if key not in result or not _same_provider_value(
                            supplied[key], result[key]
                        ):
                            raise ExposureError("Context processor reserved provider collision")
                    for key, value in supplied.items():
                        if key not in result:
                            result[key] = value
                            added.add(key)
            self._context_processor_keys = added
            return result

        if request is None:
            return context

        processor_paths = self._get_context_processors()
        resolved = self._get_resolved_processors(processor_paths)

        # Track which keys context processors actually contribute this cycle,
        # so downstream change-detection / serialization can exclude them.
        added_keys: set = set()

        for processor in resolved:
            try:
                processor_context = processor(request)
                if processor_context:
                    # Only add keys not already set by the view — view context
                    # takes precedence over context processors (e.g. Django's
                    # messages processor should not overwrite a view's 'messages').
                    for k, v in processor_context.items():
                        if k not in context:
                            context[k] = v
                            added_keys.add(k)
            except Exception as e:
                module = getattr(processor, "__module__", "")
                qualname = getattr(processor, "__qualname__", "")
                if module and qualname:
                    proc_name = module + "." + qualname
                elif module or qualname:
                    proc_name = module or qualname
                else:
                    proc_name = repr(processor)
                logger.warning(
                    "Failed to apply context processor %s: %s",
                    proc_name,
                    e,
                )

        # Expose the processor-added keys for change-detection / serialization
        # exclusion (#1786). Stored as an instance attr so ``_sync_state_to_rust``
        # can read it after this call. The ``request`` key (added by
        # ``django.template.context_processors.request``) is included here when
        # that processor is configured; ``_sync_state_to_rust`` additionally
        # excludes ``request`` unconditionally as a belt-and-suspenders guard.
        self._context_processor_keys = added_keys

        return context

    @staticmethod
    def _get_resolved_processors(processor_paths: list) -> list:
        """
        Resolve processor dotted paths to callable objects, caching the result.

        Uses tuple(processor_paths) as an immutable, hashable cache key so that
        import_string() is only called once per unique set of processor paths.

        Only caches when ALL imports succeed. If any import fails, the resolved
        list is returned but not cached, so failed imports are retried next call.
        """
        cache_key = tuple(processor_paths)
        if cache_key in _resolved_processors_cache:
            return _resolved_processors_cache[cache_key]

        from django.utils.module_loading import import_string

        resolved = []
        all_succeeded = True
        for path in processor_paths:
            try:
                resolved.append(import_string(path))
            except Exception as e:
                logger.warning("Failed to import context processor %s: %s", path, e)
                all_succeeded = False

        if all_succeeded:
            _resolved_processors_cache[cache_key] = resolved
        return resolved


def _same_provider_value(supplied: Any, provided: Any) -> bool:
    """Whether a context processor merely repeats a provider's own value."""
    if supplied is provided:
        return True
    try:
        return bool(supplied == provided) and type(supplied) is type(provided)
    except Exception:  # noqa: BLE001 — an unequal-by-error value is a collision
        return False
