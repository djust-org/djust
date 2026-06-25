"""
ContextMixin - Context data management for LiveView.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from django.db import models
from django.test.signals import setting_changed

from ..serialization import normalize_django_value
from ..utils import is_model_list

logger = logging.getLogger(__name__)

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

try:
    from importlib import import_module as _im

    _im("djust.optimization.query_optimizer")
    _im("djust.optimization.codegen")
    JIT_AVAILABLE = True
    del _im
except ImportError:
    JIT_AVAILABLE = False


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


class ContextMixin:
    """Context methods: get_context_data, _get_context_processors, _apply_context_processors."""

    if TYPE_CHECKING:
        # Cooperating attributes/methods supplied by the host class (LiveView)
        # and sibling mixins. Declared type-only so the strict-island mypy run
        # resolves them on this mixin without a runtime change — the real
        # definitions live on LiveView / the other mixins (this mixin is never
        # instantiated standalone). See streaming.py for the same pattern.
        _cached_context: Optional[Dict[str, Any]]

        def _register_component(self, component: Any) -> None: ...

        def _get_template_content(self) -> Optional[str]: ...

        def _jit_serialize_queryset(
            self, queryset: Any, template_content: str, variable_name: str
        ) -> List[Any]: ...

        def _jit_serialize_model(
            self, obj: Any, template_content: str, variable_name: str
        ) -> Dict[str, Any]: ...

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Get the context data for rendering. Override to customize context.

        Returns:
            Dictionary of context variables
        """
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

        from ..components.base import Component, LiveComponent
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
        _seen = set()
        _framework_bases = set()
        for base in ContextMixin.__mro__:
            _framework_bases.add(base)

        _all_items = list(self.__dict__.items())
        for cls in type(self).__mro__:
            if cls in _framework_bases:
                break
            for key, value in vars(cls).items():
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

        for key, value in _all_items:
            if key.startswith("_"):
                continue
            if key in _static_skip:
                continue
            if callable(value):
                continue
            if isinstance(value, (Component, LiveComponent)):
                if isinstance(value, LiveComponent):
                    self._register_component(value)
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

        # JIT auto-serialization for QuerySets and Models
        jit_serialized_keys = set()
        template_content = None

        # Short-circuit: skip JIT pipeline if no DB objects in context (#278).
        # NOTE: This scan only checks top-level context values. DB objects nested
        # inside dicts will NOT trigger JIT; they are handled later by
        # _deep_serialize_dict() which falls back to DjangoJSONEncoder when
        # template_content is None.
        has_db_values = False
        if JIT_AVAILABLE:
            for val in context.values():
                if isinstance(val, (QuerySet, models.Model)):
                    has_db_values = True
                    break
                if is_model_list(val):
                    has_db_values = True
                    break

        if has_db_values:
            try:
                template_content = self._get_template_content()
                if template_content:
                    # Extract variable paths once for list[Model] optimization
                    from ..mixins.jit import _cached_extract_template_variables

                    variable_paths_map = _cached_extract_template_variables(template_content)

                    # Compute template hash once for codegen cache keys
                    import hashlib
                    from ..session_utils import _jit_serializer_cache, _get_model_hash
                    from ..optimization.codegen import generate_serializer_code, compile_serializer

                    template_hash = hashlib.sha256(template_content.encode()).hexdigest()[:8]

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

                            if optimization and (
                                optimization.select_related
                                or optimization.prefetch_related
                                or optimization.annotations
                            ):
                                pks = [obj.pk for obj in value]
                                qs = model_class._default_manager.filter(pk__in=pks)
                                qs = optimize_queryset(qs, optimization)
                                pk_map = {obj.pk: obj for obj in qs}
                                value = [pk_map[pk] for pk in pks if pk in pk_map]

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
                            else:
                                context[key] = [
                                    self._jit_serialize_model(item, template_content, key)
                                    for item in value
                                ]
                            jit_serialized_keys.add(key)
            except Exception as e:
                logger.warning("JIT auto-serialization failed: %s", e, exc_info=True)

        # Auto-add count for plain lists
        for key, value in list(context.items()):
            if isinstance(value, list) and not key.endswith("_count"):
                count_key = f"{key}_count"
                if count_key not in context:
                    context[count_key] = len(value)

        # Single pass: deep-serialize dicts and fallback-serialize remaining Models
        tc = template_content
        for key, value in list(context.items()):
            if key in jit_serialized_keys:
                continue
            if isinstance(value, dict):
                context[key] = self._deep_serialize_dict(value, tc, key)
            elif isinstance(value, models.Model):
                context[key] = normalize_django_value(value)
            elif is_model_list(value):
                context[key] = [normalize_django_value(item) for item in value]

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

        return context

    def _deep_serialize_dict(
        self,
        d: Dict[str, Any],
        template_content: Optional[str] = None,
        var_name: str = "",
    ) -> Dict[str, Any]:
        """Recursively walk a dict, serializing any Model/QuerySet values found.

        When template_content is provided, uses JIT serialization; otherwise
        falls back to DjangoJSONEncoder.
        """
        from django.db.models import QuerySet

        result: Dict[str, Any] = {}
        for k, v in d.items():
            child_name = f"{var_name}.{k}" if var_name else k
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
                    result[k] = [
                        self._jit_serialize_model(item, template_content, child_name) for item in v
                    ]
                else:
                    result[k] = [normalize_django_value(item) for item in v]
            elif isinstance(v, dict):
                result[k] = self._deep_serialize_dict(v, template_content, child_name)
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

        # Prefer DjustTemplateBackend, fall back to DjangoTemplates
        _BACKENDS = (
            "djust.template_backend.DjustTemplateBackend",
            "django.template.backends.django.DjangoTemplates",
        )
        for template_config in getattr(settings, "TEMPLATES", []):
            if template_config.get("BACKEND") in _BACKENDS:
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
