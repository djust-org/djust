"""
Django template backend for djust's Rust rendering engine.

This enables any Django view (including TemplateView) to use djust's
high-performance Rust template rendering without requiring LiveView.

Usage in settings.py:
    TEMPLATES = [
        {
            'BACKEND': 'djust.template_backend.DjustTemplateBackend',
            'DIRS': [BASE_DIR / 'templates'],
            'APP_DIRS': True,
            'OPTIONS': {},
        },
    ]

Then use standard Django views:
    from django.views.generic import TemplateView

    class MyView(TemplateView):
        template_name = 'my_template.html'  # Rendered with Rust!
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.db import models
from django.db.models import QuerySet
from django.template import TemplateDoesNotExist, Origin
from django.template.backends.base import BaseEngine
from django.template.backends.utils import csrf_input_lazy, csrf_token_lazy
from django.utils.safestring import SafeString

logger = logging.getLogger(__name__)

# Try to import JIT optimization utilities
try:
    from djust._rust import extract_template_variables, serialize_queryset
    from djust.optimization.query_optimizer import analyze_queryset_optimization, optimize_queryset
    from djust.live_view import DjangoJSONEncoder

    JIT_AVAILABLE = True
except ImportError:
    JIT_AVAILABLE = False
    DjangoJSONEncoder = None

# Cache for JIT-compiled serializers (shared across all DjustTemplate instances)
_jit_serializer_cache = {}


class DjustTemplateBackend(BaseEngine):
    """
    Django template backend using djust's Rust rendering engine.

    Benefits:
    - 10-100x faster rendering than Django templates
    - Sub-millisecond template compilation
    - Automatic template caching
    - Compatible with Django template syntax

    Limitations:
    - Not all Django template tags/filters supported yet
    - Custom template tags not supported
    - See djust documentation for supported features
    """

    app_dirname = "templates"

    def __init__(self, params: Dict[str, Any]):
        """Initialize the Djust template backend."""
        params = params.copy()
        options = params.pop("OPTIONS").copy()
        super().__init__(params)

        self.context_processors = options.pop("context_processors", [])

        # Build list of template directories
        self.template_dirs = self._get_template_dirs(
            params.get("DIRS", []), params.get("APP_DIRS", False)
        )

        # Check if Rust rendering is available
        try:
            from djust._rust import render_template

            self._render_fn = render_template
        except ImportError as e:
            raise ImportError(
                "djust Rust extension not available. "
                "Make sure djust is properly installed with: pip install -e ."
            ) from e

    def _get_template_dirs(self, configured_dirs: List, app_dirs: bool) -> List[Path]:
        """Get list of directories to search for templates."""
        template_dirs = [Path(d) for d in configured_dirs]

        if app_dirs:
            from django.apps import apps

            for app_config in apps.get_app_configs():
                template_dir = Path(app_config.path) / self.app_dirname
                if template_dir.is_dir():
                    template_dirs.append(template_dir)

        return template_dirs

    def from_string(self, template_code: str):
        """
        Create a template from a string.

        Args:
            template_code: Template source code

        Returns:
            DjustTemplate instance
        """
        return DjustTemplate(template_code, backend=self)

    def get_template(self, template_name: str):
        """
        Load a template by name.

        Searches through template directories in order until the template
        is found.

        Args:
            template_name: Name of template to load (e.g., 'home.html')

        Returns:
            DjustTemplate instance

        Raises:
            TemplateDoesNotExist: If template not found
        """
        for template_dir in self.template_dirs:
            template_path = template_dir / template_name
            if template_path.is_file():
                try:
                    with open(template_path, "r", encoding="utf-8") as f:
                        template_code = f.read()
                    origin = Origin(
                        name=str(template_path),
                        template_name=template_name,
                        loader=self,
                    )
                    return DjustTemplate(template_code, backend=self, origin=origin)
                except OSError as e:
                    raise TemplateDoesNotExist(template_name) from e

        # Template not found in any directory
        tried = [str(d / template_name) for d in self.template_dirs]
        raise TemplateDoesNotExist(
            template_name,
            tried=tried,
            backend=self,
        )


class _TemplateSourceWrapper:
    """
    Wrapper to make DjustTemplate compatible with Django template structure.

    Django templates have: template.template.source
    This provides the .template attribute for compatibility.
    """

    def __init__(self, source: str):
        self.source = source


class DjustTemplate:
    """
    Wrapper for a template rendered with djust's Rust engine.

    Compatible with Django's template interface.
    """

    def __init__(
        self,
        template_string: str,
        backend: DjustTemplateBackend,
        origin: Optional[Origin] = None,
    ):
        """
        Initialize template.

        Args:
            template_string: Template source code
            backend: DjustTemplateBackend instance
            origin: Template origin (for debugging)
        """
        self.template_string = template_string
        self.backend = backend
        self.origin = origin

        # Add .template.source for LiveView compatibility
        # LiveView expects: template.template.source
        self.template = _TemplateSourceWrapper(template_string)

    def _jit_serialize_queryset(self, queryset: QuerySet, variable_name: str) -> list:
        """
        Apply JIT auto-serialization to a Django QuerySet.

        Automatically:
        1. Extracts variable access patterns from template
        2. Generates optimized select_related/prefetch_related calls
        3. Serializes using Rust (5-10x faster than Python)

        Args:
            queryset: Django QuerySet to serialize
            variable_name: Variable name in template (e.g., "items")

        Returns:
            List of serialized dictionaries
        """
        if not JIT_AVAILABLE:
            # Fallback to DjangoJSONEncoder
            logger.debug(f"[JIT] Not available, using DjangoJSONEncoder for '{variable_name}'")
            return [json.loads(json.dumps(obj, cls=DjangoJSONEncoder)) for obj in queryset]

        try:
            # Extract variable paths from template
            variable_paths_map = extract_template_variables(self.template_string)
            paths_for_var = variable_paths_map.get(variable_name, [])

            if not paths_for_var:
                # No template access detected, use default serialization
                logger.debug(f"[JIT] No paths found for '{variable_name}', using DjangoJSONEncoder")
                return [json.loads(json.dumps(obj, cls=DjangoJSONEncoder)) for obj in queryset]

            # Generate cache key
            import hashlib

            template_hash = hashlib.sha256(self.template_string.encode()).hexdigest()[:8]
            cache_key = (template_hash, variable_name)

            # Check cache
            if cache_key in _jit_serializer_cache:
                paths_for_var, optimization = _jit_serializer_cache[cache_key]
                logger.debug(f"[JIT] Cache HIT for '{variable_name}' - paths: {paths_for_var}")
            else:
                # Analyze and cache optimization
                model_class = queryset.model
                optimization = analyze_queryset_optimization(model_class, paths_for_var)

                logger.debug(
                    f"[JIT] Cache MISS for '{variable_name}' ({model_class.__name__}) - "
                    f"paths: {paths_for_var}"
                )
                if optimization:
                    logger.debug(
                        f"[JIT] Query optimization: select_related={sorted(optimization.select_related)}, "
                        f"prefetch_related={sorted(optimization.prefetch_related)}"
                    )

                _jit_serializer_cache[cache_key] = (paths_for_var, optimization)

            # Optimize queryset (prevents N+1 queries)
            if optimization:
                queryset = optimize_queryset(queryset, optimization)

            # Serialize with Rust (5-10x faster)
            result = serialize_queryset(list(queryset), paths_for_var)

            logger.debug(f"[JIT] Serialized {len(result)} objects for '{variable_name}' using Rust")
            return result

        except Exception as e:
            # Graceful fallback
            logger.warning(f"[JIT] Serialization failed for '{variable_name}': {e}", exc_info=True)
            return [json.loads(json.dumps(obj, cls=DjangoJSONEncoder)) for obj in queryset]

    def _jit_serialize_model(self, model_instance: models.Model, variable_name: str) -> dict:
        """
        Serialize a single Django model instance.

        Args:
            model_instance: Django model instance
            variable_name: Variable name in template

        Returns:
            Serialized dictionary
        """
        if not JIT_AVAILABLE or not DjangoJSONEncoder:
            # Fallback to basic serialization
            return {"id": str(model_instance.pk), "__str__": str(model_instance)}

        try:
            return json.loads(json.dumps(model_instance, cls=DjangoJSONEncoder))
        except Exception as e:
            logger.warning(f"Model serialization failed for '{variable_name}': {e}")
            return {"id": str(model_instance.pk), "__str__": str(model_instance)}

    def _resolve_template_inheritance(self) -> str:
        """
        Manually resolve {% extends %} tags by loading parent templates.

        This is a workaround until Rust template engine supports template loaders.
        Returns the fully resolved template string.
        """
        import re

        template_source = self.template_string
        max_depth = 10  # Prevent infinite loops
        depth = 0

        while depth < max_depth:
            # Check for {% extends 'parent.html' %} at start of template
            match = re.match(r'{%\s*extends\s+["\']([^"\']+)["\']\s*%}', template_source.strip())
            if not match:
                break

            parent_name = match.group(1)

            # Load parent template
            for template_dir in self.backend.template_dirs:
                parent_path = template_dir / parent_name
                if parent_path.is_file():
                    with open(parent_path, "r", encoding="utf-8") as f:
                        parent_source = f.read()

                    # Extract blocks from child template
                    child_blocks = {}
                    block_pattern = r"{%\s*block\s+(\w+)\s*%}(.*?){%\s*endblock\s*(?:\w+\s*)?%}"
                    for block_match in re.finditer(block_pattern, template_source, re.DOTALL):
                        block_name = block_match.group(1)
                        block_content = block_match.group(2)
                        child_blocks[block_name] = block_content

                    # Replace blocks in parent with child blocks
                    def replace_block(match):
                        block_name = match.group(1)
                        if block_name in child_blocks:
                            return child_blocks[block_name]
                        return match.group(2)  # Keep parent block content

                    template_source = re.sub(
                        block_pattern, replace_block, parent_source, flags=re.DOTALL
                    )
                    depth += 1
                    break
            else:
                # Parent template not found
                raise TemplateDoesNotExist(f"Parent template '{parent_name}' not found")

        return template_source

    def render(self, context=None, request=None) -> SafeString:
        """
        Render the template with the given context.

        Automatically serializes Django QuerySets and Models for compatibility
        with Rust rendering engine, with JIT optimization to prevent N+1 queries.

        Args:
            context: Template context (dict or Context object)
            request: Django request object (optional)

        Returns:
            Rendered HTML as SafeString
        """
        # Resolve template inheritance ({% extends %})
        # This is a temporary workaround until Rust engine supports template loaders
        try:
            resolved_template = self._resolve_template_inheritance()
        except Exception as e:
            logger.warning(f"Template inheritance resolution failed: {e}")
            resolved_template = self.template_string

        # Convert context to dict
        if context is None:
            context_dict = {}
        elif hasattr(context, "flatten"):
            # Django Context object
            context_dict = context.flatten()
        else:
            context_dict = dict(context)

        # Add request to context if provided
        if request is not None:
            context_dict["request"] = request
            # Add CSRF token functions
            context_dict["csrf_input"] = csrf_input_lazy(request)
            context_dict["csrf_token"] = csrf_token_lazy(request)

        # Apply context processors
        if request is not None:
            for processor_path in self.backend.context_processors:
                processor = self._get_context_processor(processor_path)
                context_dict.update(processor(request))

        # JIT auto-serialization for QuerySets and Models
        # This prevents N+1 queries and makes context compatible with Rust
        jit_serialized_keys = set()
        for key, value in list(context_dict.items()):
            if isinstance(value, QuerySet):
                # Auto-serialize QuerySet with query optimization
                serialized = self._jit_serialize_queryset(value, key)
                context_dict[key] = serialized
                jit_serialized_keys.add(key)

                # Auto-add count variable (e.g., items -> items_count)
                if isinstance(serialized, list):
                    count_key = f"{key}_count"
                    if count_key not in context_dict:
                        context_dict[count_key] = len(serialized)

            elif isinstance(value, models.Model):
                # Auto-serialize Model instance
                context_dict[key] = self._jit_serialize_model(value, key)
                jit_serialized_keys.add(key)

        # Auto-add count for plain lists (Phase 4+ optimization)
        for key, value in list(context_dict.items()):
            if isinstance(value, list) and not key.endswith("_count"):
                count_key = f"{key}_count"
                if count_key not in context_dict:
                    context_dict[count_key] = len(value)

        # Render with Rust engine (use resolved template with inheritance resolved)
        try:
            html = self.backend._render_fn(resolved_template, context_dict)
            return SafeString(html)
        except Exception as e:
            # Provide helpful error message with template location
            origin_info = f" (from {self.origin.name})" if self.origin else ""

            # Check if error might be due to unsupported template tag/filter
            error_msg = str(e)
            if "Unsupported tag" in error_msg or "Unknown filter" in error_msg:
                suggestion = (
                    "\n\nHint: This template uses features not yet supported by djust's Rust engine. "
                    "Consider using workarounds (see docs/TEMPLATE_BACKEND.md) or use Django's "
                    "template backend for this specific template."
                )
                raise Exception(
                    f"Error rendering template{origin_info}: {error_msg}{suggestion}"
                ) from e

            raise Exception(f"Error rendering template{origin_info}: {error_msg}") from e

    def _get_context_processor(self, processor_path: str):
        """Import and return a context processor function."""
        from django.utils.module_loading import import_string

        return import_string(processor_path)
