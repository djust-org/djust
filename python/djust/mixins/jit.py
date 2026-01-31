"""
JITMixin - JIT auto-serialization for QuerySets and Models.
"""

import hashlib
import json
import logging
import os
import re
import sys
from typing import Dict, Optional

from ..serialization import DjangoJSONEncoder
from ..session_utils import _jit_serializer_cache, _get_model_hash

logger = logging.getLogger(__name__)

# Cache template_content id → sha256 hash (avoids recomputing per variable).
# Keyed by id(template_content) to avoid keeping large template strings alive as dict keys.
# The id is safe here because the template string is kept alive by the caller for the
# duration of get_context_data(), and stale entries are harmless (just a cache miss).
_template_hash_cache: Dict[int, str] = {}

# Cache (template_hash, variable_name) → expected top-level key count
_expected_keys_cache: Dict[tuple, int] = {}

# Pre-compiled regex for {% include %} — handles normal and doubled quotes from Rust resolver
_INCLUDE_RE = re.compile(
    r'\{%\s*include\s+"{1,2}([^"]+)"{1,2}\s*%\}|\{%\s*include\s+\'{1,2}([^\']+)\'{1,2}\s*%\}'
)

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

        Prefers the fully-resolved template (with inheritance) so that
        variables used in parent/base templates are also discovered.
        """
        # Prefer the fully-resolved template (includes inherited blocks)
        if hasattr(self, "_full_template") and self._full_template:
            return self._full_template

        if hasattr(self, "template") and self.template:
            return self.template

        if hasattr(self, "template_name") and self.template_name:
            # Try Rust template inheritance resolution first
            try:
                from djust._rust import resolve_template_inheritance
                from django.conf import settings
                from pathlib import Path

                template_dirs = []
                for tpl_cfg in getattr(settings, "TEMPLATES", []):
                    if "DIRS" in tpl_cfg:
                        template_dirs.extend(str(d) for d in tpl_cfg["DIRS"])
                    backend = tpl_cfg.get("BACKEND", "")
                    if backend == "django.template.backends.django.DjangoTemplates":
                        if tpl_cfg.get("APP_DIRS", False):
                            from django.apps import apps

                            for app_config in apps.get_app_configs():
                                tpl_dir = Path(app_config.path) / "templates"
                                if tpl_dir.exists():
                                    template_dirs.append(str(tpl_dir))

                resolved = resolve_template_inheritance(self.template_name, template_dirs)
                if resolved:
                    # Also inline {% include %} directives so variable extraction
                    # discovers variables used in included templates
                    resolved = self._inline_includes(resolved, template_dirs)
                    return resolved
            except Exception:
                pass  # Rust resolver unavailable or failed; fall back to Django loader

            # Fallback to single-file template source
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

    @staticmethod
    def _inline_includes(template_content: str, template_dirs: list) -> str:
        """Inline {% include "..." %} directives for variable extraction.

        Only handles simple static includes (not variable includes).
        Recursively resolves nested includes up to 5 levels deep.
        """

        def resolve(content, depth=0):
            if depth > 5:
                return content

            def replacer(match):
                include_path = match.group(1) or match.group(2)
                for tpl_dir in template_dirs:
                    full_path = os.path.join(tpl_dir, include_path)
                    if os.path.isfile(full_path):
                        try:
                            with open(full_path, "r") as f:
                                included = f.read()
                            return resolve(included, depth + 1)
                        except Exception as e:
                            logger.debug("Failed to read included template %s: %s", full_path, e)
                return match.group(0)  # Keep original if not found

            return _INCLUDE_RE.sub(replacer, content)

        return resolve(template_content)

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
            _tc_id = id(template_content)
            if _tc_id not in _template_hash_cache:
                _template_hash_cache[_tc_id] = hashlib.sha256(
                    template_content.encode()
                ).hexdigest()[:8]
            template_hash = _template_hash_cache[_tc_id]
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

            # Try Rust serializer first, fall back to Python codegen if incomplete
            from djust._rust import serialize_queryset

            items = list(queryset)
            result = serialize_queryset(items, paths_for_var)

            # Check if Rust serializer captured all expected paths
            # (Rust can't access @property attributes, only model fields)
            ek_cache_key = (template_hash, variable_name)
            if ek_cache_key not in _expected_keys_cache:
                _expected_keys_cache[ek_cache_key] = len(
                    set(p.split(".")[0] for p in paths_for_var)
                )
            expected_keys = _expected_keys_cache[ek_cache_key]
            if result and len(result[0]) < expected_keys:
                # Heuristic: if the first item has fewer top-level keys than expected,
                # Rust likely can't access some paths (e.g. @property). A nullable FK
                # on item 0 could cause a false positive, but the codegen fallback is
                # correct (just slightly slower), so this is an acceptable trade-off.

                func_name = f"serialize_{variable_name}_{template_hash}"
                code = generate_serializer_code(model_class.__name__, paths_for_var, func_name)
                serializer = compile_serializer(code, func_name)
                result = [serializer(obj) for obj in items]

            from ..config import config

            if config.get("jit_debug"):
                logger.debug(
                    f"[JIT] Serialized {len(result)} {queryset.model.__name__} objects for '{variable_name}'"
                )
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
            _tc_id = id(template_content)
            if _tc_id not in _template_hash_cache:
                _template_hash_cache[_tc_id] = hashlib.sha256(
                    template_content.encode()
                ).hexdigest()[:8]
            template_hash = _template_hash_cache[_tc_id]
            model_hash = _get_model_hash(model_class)
            cache_key = (template_hash, variable_name, model_hash)

            if cache_key in _jit_serializer_cache:
                serializer, _ = _jit_serializer_cache[cache_key]
            else:
                func_name = f"serialize_{variable_name}_{template_hash}"
                code = generate_serializer_code(model_class.__name__, paths_for_var, func_name)
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
