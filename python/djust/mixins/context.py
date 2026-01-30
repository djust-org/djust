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
        if JIT_AVAILABLE:
            try:
                template_content = self._get_template_content()
                if template_content:
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
        tc = template_content if JIT_AVAILABLE and "template_content" in dir() else None
        for key, value in list(context.items()):
            if key in jit_serialized_keys:
                continue
            if isinstance(value, dict):
                if tc:
                    context[key] = self._deep_serialize_dict(value, tc, key)
                else:
                    context[key] = self._deep_serialize_dict_fallback(value)
            elif isinstance(value, models.Model):
                context[key] = json.loads(json.dumps(value, cls=DjangoJSONEncoder))
            elif isinstance(value, list) and value and isinstance(value[0], models.Model):
                context[key] = [
                    json.loads(json.dumps(item, cls=DjangoJSONEncoder)) for item in value
                ]

        self._jit_serialized_keys = jit_serialized_keys

        return context

    def _deep_serialize_dict(self, d: dict, template_content, var_name: str) -> dict:
        """Recursively walk a dict, JIT-serializing any Model/QuerySet values found."""
        from django.db.models import QuerySet

        result = {}
        for k, v in d.items():
            if isinstance(v, models.Model):
                if template_content:
                    result[k] = self._jit_serialize_model(v, template_content, f"{var_name}.{k}")
                else:
                    result[k] = json.loads(json.dumps(v, cls=DjangoJSONEncoder))
            elif isinstance(v, QuerySet):
                if template_content:
                    result[k] = self._jit_serialize_queryset(v, template_content, f"{var_name}.{k}")
                else:
                    result[k] = [json.loads(json.dumps(item, cls=DjangoJSONEncoder)) for item in v]
            elif isinstance(v, list) and v and isinstance(v[0], models.Model):
                if template_content:
                    result[k] = [
                        self._jit_serialize_model(item, template_content, f"{var_name}.{k}")
                        for item in v
                    ]
                else:
                    result[k] = [json.loads(json.dumps(item, cls=DjangoJSONEncoder)) for item in v]
            elif isinstance(v, dict):
                result[k] = self._deep_serialize_dict(v, template_content, f"{var_name}.{k}")
            else:
                result[k] = v
        return result

    def _deep_serialize_dict_fallback(self, d: dict) -> dict:
        """Recursively walk a dict, fallback-serializing any Model instances."""
        from django.db.models import QuerySet

        result = {}
        for k, v in d.items():
            if isinstance(v, models.Model):
                result[k] = json.loads(json.dumps(v, cls=DjangoJSONEncoder))
            elif isinstance(v, QuerySet):
                result[k] = [json.loads(json.dumps(item, cls=DjangoJSONEncoder)) for item in v]
            elif isinstance(v, list) and v and isinstance(v[0], models.Model):
                result[k] = [json.loads(json.dumps(item, cls=DjangoJSONEncoder)) for item in v]
            elif isinstance(v, dict):
                result[k] = self._deep_serialize_dict_fallback(v)
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
                    context.update(processor_context)
            except Exception as e:
                logger.warning(f"Failed to apply context processor {processor_path}: {e}")

        return context
