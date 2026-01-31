"""
ContextMixin - Context data management for LiveView.
"""

import json
import logging
from typing import Any, Dict

from django.db import models

from ..serialization import DjangoJSONEncoder

logger = logging.getLogger(__name__)

# Module-level cache for context processors, keyed by settings object id
_context_processors_cache: Dict[int, list] = {}

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
        if JIT_AVAILABLE:
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
                            pass  # Rust extractor unavailable or failed; fall back to non-optimized path

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
                logger.debug(f"JIT auto-serialization failed: {e}", exc_info=True)

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
                context[key] = json.loads(json.dumps(value, cls=DjangoJSONEncoder))
            elif isinstance(value, list) and value and isinstance(value[0], models.Model):
                context[key] = [
                    json.loads(json.dumps(item, cls=DjangoJSONEncoder)) for item in value
                ]

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
                    result[k] = json.loads(json.dumps(v, cls=DjangoJSONEncoder))
            elif isinstance(v, QuerySet):
                if template_content:
                    result[k] = self._jit_serialize_queryset(v, template_content, child_name)
                else:
                    result[k] = [json.loads(json.dumps(item, cls=DjangoJSONEncoder)) for item in v]
            elif isinstance(v, list) and v and isinstance(v[0], models.Model):
                if template_content:
                    result[k] = [
                        self._jit_serialize_model(item, template_content, child_name) for item in v
                    ]
                else:
                    result[k] = [json.loads(json.dumps(item, cls=DjangoJSONEncoder)) for item in v]
            elif isinstance(v, dict):
                result[k] = self._deep_serialize_dict(v, template_content, child_name)
            else:
                result[k] = v
        return result

    def _get_context_processors(self) -> list:
        """
        Get context processors from DjustTemplateBackend settings.
        """
        from django.conf import settings

        cache_key = id(getattr(settings, "_wrapped", settings))

        if cache_key in _context_processors_cache:
            return _context_processors_cache[cache_key]

        for template_config in getattr(settings, "TEMPLATES", []):
            if template_config.get("BACKEND") == "djust.template_backend.DjustTemplateBackend":
                processors = template_config.get("OPTIONS", {}).get("context_processors", [])
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

        from django.utils.module_loading import import_string

        context_processors = self._get_context_processors()

        for processor_path in context_processors:
            try:
                processor = import_string(processor_path)
                processor_context = processor(request)
                if processor_context:
                    # Only add keys not already set by the view — view context
                    # takes precedence over context processors (e.g. Django's
                    # messages processor should not overwrite a view's 'messages').
                    for k, v in processor_context.items():
                        if k not in context:
                            context[k] = v
            except Exception as e:
                logger.warning(f"Failed to apply context processor {processor_path}: {e}")

        return context
