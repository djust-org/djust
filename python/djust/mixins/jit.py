"""
JITMixin - JIT auto-serialization for QuerySets and Models.
"""

import hashlib
import json
import logging
import sys
from typing import Dict, Optional

from ..serialization import DjangoJSONEncoder
from ..session_utils import _jit_serializer_cache, _get_model_hash

logger = logging.getLogger(__name__)

try:
    from .._rust import extract_template_variables

except ImportError:
    extract_template_variables = None

try:
    from ..optimization.query_optimizer import analyze_queryset_optimization, optimize_queryset
    from ..optimization.codegen import generate_serializer_code, compile_serializer

    JIT_AVAILABLE = True
except ImportError:
    JIT_AVAILABLE = False


class JITMixin:
    """JIT serialization: _jit_serialize_queryset, _jit_serialize_model, _lazy_serialize_context, _get_template_content."""

    def _get_template_content(self) -> Optional[str]:
        """
        Get template source code for JIT variable extraction.
        """
        if hasattr(self, "template") and self.template:
            return self.template

        if hasattr(self, "template_name") and self.template_name:
            try:
                from django.template.loader import get_template

                django_template = get_template(self.template_name)

                if hasattr(django_template, "template") and hasattr(
                    django_template.template, "source"
                ):
                    return django_template.template.source
                elif hasattr(django_template, "origin") and hasattr(django_template.origin, "name"):
                    with open(django_template.origin.name, "r") as f:
                        return f.read()
            except Exception as e:
                logger.debug(f"Could not load template for JIT: {e}")
                return None

        return None

    def _jit_serialize_queryset(self, queryset, template_content: str, variable_name: str):
        """
        Apply JIT auto-serialization to a Django QuerySet.

        Automatically:
        1. Extracts variable access patterns from template
        2. Generates optimized select_related/prefetch_related calls
        3. Compiles custom serializer function
        4. Caches serializer for reuse
        """
        if not JIT_AVAILABLE or not extract_template_variables:
            return [json.loads(json.dumps(obj, cls=DjangoJSONEncoder)) for obj in queryset]

        try:
            variable_paths_map = extract_template_variables(template_content)
            paths_for_var = variable_paths_map.get(variable_name, [])

            if not paths_for_var:
                print(
                    f"[JIT] No paths found for '{variable_name}', using DjangoJSONEncoder fallback",
                    file=sys.stderr,
                )
                return [json.loads(json.dumps(obj, cls=DjangoJSONEncoder)) for obj in queryset]

            model_class = queryset.model
            template_hash = hashlib.sha256(template_content.encode()).hexdigest()[:8]
            model_hash = _get_model_hash(model_class)
            cache_key = (template_hash, variable_name, model_hash)

            if cache_key in _jit_serializer_cache:
                paths_for_var, optimization = _jit_serializer_cache[cache_key]
                print(
                    f"[JIT] Cache HIT for '{variable_name}' - using cached paths: {paths_for_var}",
                    file=sys.stderr,
                )
            else:
                optimization = analyze_queryset_optimization(model_class, paths_for_var)

                print(
                    f"[JIT] Cache MISS for '{variable_name}' ({model_class.__name__}) - generating serializer for paths: {paths_for_var}",
                    file=sys.stderr,
                )
                if optimization:
                    print(
                        f"[JIT] Query optimization: select_related={sorted(optimization.select_related)}, prefetch_related={sorted(optimization.prefetch_related)}",
                        file=sys.stderr,
                    )

                _jit_serializer_cache[cache_key] = (paths_for_var, optimization)

            if optimization:
                queryset = optimize_queryset(queryset, optimization)

            from djust._rust import serialize_queryset

            result = serialize_queryset(list(queryset), paths_for_var)

            from ..config import config

            if config.get("jit_debug"):
                logger.debug(
                    f"[JIT] Serialized {len(result)} {queryset.model.__name__} objects for '{variable_name}' using Rust"
                )
                logger.debug(f"[JIT DEBUG] Rust serializer returned {len(result)} items")
                if result:
                    logger.debug(f"[JIT DEBUG] First item keys: {list(result[0].keys())}")
            return result

        except Exception as e:
            import traceback

            logger.error(
                f"[JIT ERROR] Serialization failed for '{variable_name}': {e}\nTraceback:\n{traceback.format_exc()}"
            )
            return [json.loads(json.dumps(obj, cls=DjangoJSONEncoder)) for obj in queryset]

    def _jit_serialize_model(self, obj, template_content: str, variable_name: str) -> Dict:
        """
        Apply JIT auto-serialization to a single Django Model instance.
        """
        if not JIT_AVAILABLE or not extract_template_variables:
            return json.loads(json.dumps(obj, cls=DjangoJSONEncoder))

        try:
            variable_paths_map = extract_template_variables(template_content)
            paths_for_var = variable_paths_map.get(variable_name, [])

            if not paths_for_var:
                return json.loads(json.dumps(obj, cls=DjangoJSONEncoder))

            model_class = obj.__class__
            template_hash = hashlib.sha256(template_content.encode()).hexdigest()[:8]
            model_hash = _get_model_hash(model_class)
            cache_key = (template_hash, variable_name, model_hash)

            if cache_key in _jit_serializer_cache:
                serializer, _ = _jit_serializer_cache[cache_key]
            else:
                code = generate_serializer_code(model_class.__name__, paths_for_var)
                func_name = f"serialize_{variable_name}_{template_hash}"
                serializer = compile_serializer(code, func_name)
                _jit_serializer_cache[cache_key] = (serializer, None)

            return serializer(obj)

        except Exception as e:
            logger.debug(f"JIT serialization failed for {variable_name}: {e}")
            return json.loads(json.dumps(obj, cls=DjangoJSONEncoder))

    def _lazy_serialize_context(self, context: dict) -> dict:
        """
        Lazy serialization: only serialize values that need conversion.
        """
        from ..components.base import Component, LiveComponent
        from django.db.models import Model
        from datetime import datetime, date, time
        from decimal import Decimal
        from uuid import UUID

        def serialize_value(value):
            if value is None or isinstance(value, (str, int, float, bool)):
                return value

            if isinstance(value, (list, tuple)):
                return [serialize_value(item) for item in value]

            if isinstance(value, dict):
                return {k: serialize_value(v) for k, v in value.items()}

            if isinstance(value, (Component, LiveComponent)):
                return {"render": str(value.render())}

            if isinstance(value, Model):
                return str(value)

            if isinstance(value, (datetime, date)):
                return value.isoformat()

            if isinstance(value, time):
                return value.isoformat()

            if isinstance(value, (Decimal, UUID)):
                return str(value)

            try:
                return json.loads(json.dumps(value, cls=DjangoJSONEncoder))
            except (TypeError, ValueError):
                return str(value)

        return {k: serialize_value(v) for k, v in context.items()}
