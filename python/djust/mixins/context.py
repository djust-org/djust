"""
ContextMixin - Context data management for LiveView.
"""

import logging
from typing import Any, Dict

from django.db import models
from django.test.signals import setting_changed

from ..serialization import normalize_django_value

logger = logging.getLogger(__name__)

# Module-level cache for context processors, keyed by TEMPLATES config tuple
_context_processors_cache: Dict[Any, list] = {}

# Module-level cache for resolved processor callables, keyed by tuple of processor paths
_resolved_processors_cache: Dict[tuple, list] = {}


def _clear_processor_caches(**kwargs):
    """Clear caches when settings change (e.g., during @override_settings in tests)."""
    if kwargs.get("setting") == "TEMPLATES":
        _context_processors_cache.clear()
        _resolved_processors_cache.clear()


setting_changed.connect(_clear_processor_caches)

try:
    import djust.optimization.query_optimizer  # noqa: F401
    import djust.optimization.codegen  # noqa: F401

    JIT_AVAILABLE = True
except ImportError:
    JIT_AVAILABLE = False


class ContextMixin:
    """Context methods: get_context_data, _get_context_processors, _apply_context_processors."""

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        """
        Get the context data for rendering. Override to customize context.

        Returns:
            Dictionary of context variables
        """
        # Return cached context if available (set during GET request to avoid
        # redundant QuerySet evaluation across sync_state_to_rust/render_with_diff)
        if hasattr(self, "_cached_context") and self._cached_context is not None:
            return self._cached_context

        from ..components.base import Component, LiveComponent
        from django.db.models import QuerySet

        context = {}

        # Add all non-private attributes as context
        for key in dir(self):
            if not key.startswith("_"):
                try:
                    value = getattr(self, key)
                    if not callable(value):
                        if isinstance(value, (Component, LiveComponent)):
                            if isinstance(value, LiveComponent):
                                self._register_component(value)
                            context[key] = value
                        else:
                            import types
                            from django.http import HttpRequest

                            if isinstance(
                                value,
                                (
                                    types.FunctionType,
                                    types.MethodType,
                                    types.ModuleType,
                                    type,
                                    types.BuiltinFunctionType,
                                    HttpRequest,
                                ),
                            ):
                                pass
                            else:
                                context[key] = value
                except (AttributeError, TypeError):
                    continue

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
                if isinstance(val, list) and val and isinstance(val[0], models.Model):
                    has_db_values = True
                    break

        if has_db_values:
            try:
                template_content = self._get_template_content()
                if template_content:
                    # Extract variable paths once for list[Model] optimization
                    from ..mixins.jit import extract_template_variables

                    variable_paths_map = None
                    if extract_template_variables:
                        try:
                            variable_paths_map = extract_template_variables(template_content)
                        except Exception:
                            logger.debug(
                                "Rust template variable extractor unavailable, using fallback"
                            )

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

                        elif (
                            isinstance(value, list) and value and isinstance(value[0], models.Model)
                        ):
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
            elif isinstance(value, list) and value and isinstance(value[0], models.Model):
                context[key] = [normalize_django_value(item) for item in value]

        self._jit_serialized_keys = jit_serialized_keys

        return context

    def _deep_serialize_dict(self, d: dict, template_content=None, var_name: str = "") -> dict:
        """Recursively walk a dict, serializing any Model/QuerySet values found.

        When template_content is provided, uses JIT serialization; otherwise
        falls back to DjangoJSONEncoder.
        """
        from django.db.models import QuerySet

        result = {}
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
            elif isinstance(v, list) and v and isinstance(v[0], models.Model):
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

    def _get_context_processors(self) -> list:
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
                processors = template_config.get("OPTIONS", {}).get("context_processors", [])
                if processors:
                    _context_processors_cache[cache_key] = processors
                    return processors

        _context_processors_cache[cache_key] = []
        return []

    def _apply_context_processors(self, context: Dict[str, Any], request) -> Dict[str, Any]:
        """
        Apply Django context processors to the context.
        """
        if request is None:
            return context

        processor_paths = self._get_context_processors()
        resolved = self._get_resolved_processors(processor_paths)

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
