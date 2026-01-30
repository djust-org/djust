"""
Template rendering logic for djust.

Provides the DjustTemplate class that handles template inheritance,
URL tag resolution, JIT serialization, and Rust-based rendering.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from typing import TYPE_CHECKING, Any, Dict, Optional

from django.db import models
from django.db.models import QuerySet
from django.template import TemplateDoesNotExist, Origin
from django.template.backends.utils import csrf_input_lazy, csrf_token_lazy
from django.utils.safestring import SafeString

if TYPE_CHECKING:
    from .backend import DjustTemplateBackend

from .serialization import serialize_context

logger = logging.getLogger(__name__)

# Try to import JIT optimization utilities
try:
    from djust._rust import extract_template_variables, serialize_queryset
    from djust.optimization.query_optimizer import analyze_queryset_optimization, optimize_queryset
    from djust.serialization import DjangoJSONEncoder
    from djust.live_view import (
        _get_model_hash,
        clear_jit_cache,  # noqa: F401 - exported for external use
        _jit_serializer_cache,  # Shared cache - cleared by clear_jit_cache()
    )

    JIT_AVAILABLE = True
except ImportError:
    JIT_AVAILABLE = False
    DjangoJSONEncoder = None
    _get_model_hash = None
    clear_jit_cache = None
    _jit_serializer_cache = {}  # Fallback empty cache when JIT not available


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

    # Pre-compiled regex patterns for template inheritance processing
    _BLOCK_START_RE = re.compile(r"{%\s*block\s+(\w+)\s*%}")
    _BLOCK_END_RE = re.compile(r"{%\s*endblock\s*(?:\w+\s*)?%}")
    _EXTENDS_RE = re.compile(r'{%\s*extends\s+["\']([^"\']+)["\']\s*%}')

    # Regex pattern for {% url %} tag
    # Matches: {% url 'name' %}, {% url 'name' arg1 %}, {% url 'name' key=val %},
    #          {% url 'name' as var %}, etc.
    # The negative lookahead (?!as\s) prevents 'as' from being captured as an argument
    _URL_TAG_RE = re.compile(
        r"{%\s*url\s+"
        r"['\"]([^'\"]+)['\"]"  # URL name (required, in quotes)
        r"((?:\s+(?!as\s)(?:[a-zA-Z_][a-zA-Z0-9_.]*(?:=[^\s%}]+)?|['\"][^'\"]*['\"]|\d+))*)"  # args/kwargs (excluding 'as')
        r"(?:\s+as\s+([a-zA-Z_][a-zA-Z0-9_]*))?"  # optional 'as variable'
        r"\s*%}",
        re.DOTALL,
    )

    def __init__(
        self,
        template_string: str,
        backend: "DjustTemplateBackend",
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

            # Generate cache key (includes model hash for invalidation on model changes)
            model_class = queryset.model
            template_hash = hashlib.sha256(self.template_string.encode()).hexdigest()[:8]
            model_hash = _get_model_hash(model_class) if _get_model_hash else ""
            cache_key = (template_hash, variable_name, model_hash)

            # Check cache
            if cache_key in _jit_serializer_cache:
                paths_for_var, optimization = _jit_serializer_cache[cache_key]
                logger.debug(f"[JIT] Cache HIT for '{variable_name}' - paths: {paths_for_var}")
            else:
                # Analyze and cache optimization
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

        The algorithm works by:
        1. Finding {% extends 'parent.html' %} at the start of the template
        2. Loading the parent template
        3. Extracting blocks from the child template
        4. Replacing blocks in the parent with child blocks, PRESERVING block wrappers
        5. Preserving child blocks that don't exist in immediate parent (for ancestors)
        6. Repeating until no more {% extends %} tags are found
        7. Stripping all block wrappers at the end
        """
        template_source = self.template_string
        max_depth = 10  # Prevent infinite loops
        depth = 0

        # Accumulate all block overrides through the inheritance chain
        accumulated_blocks = {}

        while depth < max_depth:
            # Check for {% extends 'parent.html' %} at start of template
            match = self._EXTENDS_RE.match(template_source.strip())
            if not match:
                break

            parent_name = match.group(1)

            # Load parent template
            for template_dir in self.backend.template_dirs:
                parent_path = template_dir / parent_name
                if parent_path.is_file():
                    with open(parent_path, "r", encoding="utf-8") as f:
                        parent_source = f.read()

                    # Extract blocks from current template
                    current_blocks = self._extract_template_blocks(template_source)

                    # Merge current blocks into accumulated (current takes precedence)
                    # This preserves overrides from descendants even if intermediate
                    # templates don't have those blocks
                    accumulated_blocks.update(current_blocks)

                    # Replace blocks in parent with accumulated blocks
                    template_source = self._replace_blocks_in_template(
                        parent_source, accumulated_blocks
                    )
                    depth += 1
                    break
            else:
                # Parent template not found
                raise TemplateDoesNotExist(f"Parent template '{parent_name}' not found")

        # Strip all remaining block wrappers after inheritance is fully resolved
        template_source = self._strip_block_wrappers(template_source)

        return template_source

    def _replace_blocks_in_template(self, template_source: str, child_blocks: dict) -> str:
        """
        Replace blocks in template with child block content, preserving wrappers.

        Handles nested blocks correctly by:
        1. If child overrides a block, use child's content entirely
        2. If child doesn't override a block, recursively process its content
           to handle nested blocks that the child might override

        Args:
            template_source: The parent template to modify
            child_blocks: Dict mapping block names to their content

        Returns:
            Template with blocks replaced
        """
        result = []
        pos = 0

        while pos < len(template_source):
            # Find next block start
            start_match = self._BLOCK_START_RE.search(template_source, pos)
            if not start_match:
                # No more blocks, append rest of template
                result.append(template_source[pos:])
                break

            # Append content before block
            result.append(template_source[pos : start_match.start()])

            block_name = start_match.group(1)
            content_start = start_match.end()

            # Find matching endblock by tracking nesting depth
            depth = 1
            search_pos = content_start
            content_end = None
            block_end_pos = None

            while depth > 0 and search_pos < len(template_source):
                next_start = self._BLOCK_START_RE.search(template_source, search_pos)
                next_end = self._BLOCK_END_RE.search(template_source, search_pos)

                if next_end is None:
                    # No matching endblock - malformed template
                    break

                start_pos = next_start.start() if next_start else len(template_source)
                end_pos = next_end.start()

                if start_pos < end_pos:
                    # Found nested block start
                    depth += 1
                    search_pos = next_start.end()
                else:
                    # Found endblock
                    depth -= 1
                    if depth == 0:
                        content_end = end_pos
                        block_end_pos = next_end.end()
                    search_pos = next_end.end()

            if block_end_pos is None:
                # Malformed template, append as-is
                result.append(template_source[start_match.start() :])
                break

            # Determine block content
            if block_name in child_blocks:
                # Use child block content, preserve wrapper for further inheritance
                result.append(f"{{% block {block_name} %}}")
                result.append(child_blocks[block_name])
                result.append("{% endblock %}")
            else:
                # Child doesn't override this block, but might override nested blocks
                # Recursively process the block content to handle nested blocks
                parent_block_content = template_source[content_start:content_end]
                processed_content = self._replace_blocks_in_template(
                    parent_block_content, child_blocks
                )
                result.append(f"{{% block {block_name} %}}")
                result.append(processed_content)
                result.append("{% endblock %}")

            pos = block_end_pos

        return "".join(result)

    def _strip_block_wrappers(self, template_source: str) -> str:
        """
        Strip all {% block %}...{% endblock %} wrappers, keeping content.

        Handles nested blocks correctly.

        Args:
            template_source: Template with block wrappers

        Returns:
            Template with block wrappers removed
        """
        result = []
        pos = 0

        while pos < len(template_source):
            start_match = self._BLOCK_START_RE.search(template_source, pos)
            if not start_match:
                result.append(template_source[pos:])
                break

            # Append content before block start tag
            result.append(template_source[pos : start_match.start()])

            content_start = start_match.end()

            # Find matching endblock
            depth = 1
            search_pos = content_start
            content_end = None
            block_end_pos = None

            while depth > 0 and search_pos < len(template_source):
                next_start = self._BLOCK_START_RE.search(template_source, search_pos)
                next_end = self._BLOCK_END_RE.search(template_source, search_pos)

                if next_end is None:
                    break

                start_pos = next_start.start() if next_start else len(template_source)
                end_pos = next_end.start()

                if start_pos < end_pos:
                    depth += 1
                    search_pos = next_start.end()
                else:
                    depth -= 1
                    if depth == 0:
                        content_end = end_pos
                        block_end_pos = next_end.end()
                    search_pos = next_end.end()

            if content_end is not None:
                # Recursively strip nested blocks from content
                block_content = template_source[content_start:content_end]
                result.append(self._strip_block_wrappers(block_content))
                pos = block_end_pos
            else:
                # Malformed, keep as-is
                result.append(template_source[start_match.start() :])
                break

        return "".join(result)

    def _extract_template_blocks(self, template_source: str) -> dict:
        """
        Extract all top-level blocks from a template source.

        Handles nested blocks correctly by tracking block depth.

        Args:
            template_source: The template string to extract blocks from

        Returns:
            Dict mapping block names to their content (without wrapper tags)
        """
        blocks = {}
        pos = 0
        while pos < len(template_source):
            # Find next block start
            start_match = self._BLOCK_START_RE.search(template_source, pos)
            if not start_match:
                break

            block_name = start_match.group(1)
            content_start = start_match.end()

            # Find matching endblock by tracking nesting depth
            depth = 1
            search_pos = content_start
            content_end = None

            while depth > 0 and search_pos < len(template_source):
                next_start = self._BLOCK_START_RE.search(template_source, search_pos)
                next_end = self._BLOCK_END_RE.search(template_source, search_pos)

                if next_end is None:
                    # No matching endblock - malformed template
                    break

                # Determine which comes first
                start_pos = next_start.start() if next_start else len(template_source)
                end_pos = next_end.start()

                if start_pos < end_pos:
                    # Found nested block start
                    depth += 1
                    search_pos = next_start.end()
                else:
                    # Found endblock
                    depth -= 1
                    if depth == 0:
                        content_end = end_pos
                    search_pos = next_end.end()

            if content_end is not None:
                blocks[block_name] = template_source[content_start:content_end]
                pos = search_pos
            else:
                pos = content_start

        return blocks

    def _resolve_url_tags(self, template_source: str, context_dict: Dict[str, Any]) -> str:
        """
        Resolve {% url %} tags by replacing them with actual URLs.

        This preprocessing step allows the Rust rendering engine to work with
        resolved URLs since it doesn't have access to Django's URL resolver.

        Supports:
        - Basic: {% url 'name' %}
        - With args: {% url 'name' arg1 arg2 %}
        - With kwargs: {% url 'name' key=value %}
        - With context variables: {% url 'name' post.slug %}
        - As variable: {% url 'name' as var_name %}

        Args:
            template_source: Template string containing {% url %} tags
            context_dict: Context dictionary for resolving variable arguments

        Returns:
            Template string with {% url %} tags replaced by resolved URLs
        """
        from django.urls import NoReverseMatch, reverse

        def resolve_value(value_str: str, context: Dict[str, Any]) -> Any:
            """Resolve a value from the context or return the literal value."""
            value_str = value_str.strip()

            # String literal (single or double quotes)
            if (value_str.startswith("'") and value_str.endswith("'")) or (
                value_str.startswith('"') and value_str.endswith('"')
            ):
                return value_str[1:-1]

            # Integer literal
            if value_str.isdigit():
                return int(value_str)

            # Context variable (possibly with dot notation)
            if "." in value_str:
                parts = value_str.split(".")
                value = context.get(parts[0])
                for part in parts[1:]:
                    if value is None:
                        return None
                    if isinstance(value, dict):
                        value = value.get(part)
                    else:
                        value = getattr(value, part, None)
                return value
            else:
                return context.get(value_str)

        def replace_url_tag(match: re.Match) -> str:
            """Replace a single {% url %} tag with its resolved URL."""
            url_name = match.group(1)
            args_string = match.group(2) or ""
            as_variable = match.group(3)

            # Parse arguments and keyword arguments
            args = []
            kwargs = {}

            # Tokenize the arguments string
            if args_string.strip():
                # Simple tokenization - handle quoted strings and key=value pairs
                tokens = []
                current_token = ""
                in_quotes = False
                quote_char = None

                for char in args_string:
                    if char in "\"'" and not in_quotes:
                        in_quotes = True
                        quote_char = char
                        current_token += char
                    elif char == quote_char and in_quotes:
                        in_quotes = False
                        quote_char = None
                        current_token += char
                    elif char.isspace() and not in_quotes:
                        if current_token:
                            tokens.append(current_token)
                            current_token = ""
                    else:
                        current_token += char

                if current_token:
                    tokens.append(current_token)

                # Process tokens into args and kwargs
                for token in tokens:
                    if "=" in token and not token.startswith("'") and not token.startswith('"'):
                        # Keyword argument
                        key, value = token.split("=", 1)
                        resolved_value = resolve_value(value, context_dict)
                        if resolved_value is not None:
                            kwargs[key] = resolved_value
                    else:
                        # Positional argument
                        resolved_value = resolve_value(token, context_dict)
                        if resolved_value is not None:
                            args.append(resolved_value)

            # Check if any args/kwargs couldn't be resolved (value is None)
            # This happens when the URL references loop variables like post.slug
            # In this case, leave the original tag - it can't be resolved yet
            has_unresolved = False
            if args_string.strip():
                for token in tokens:
                    if "=" in token and not token.startswith("'") and not token.startswith('"'):
                        # Keyword argument
                        _, value = token.split("=", 1)
                        if resolve_value(value, context_dict) is None and not (
                            (value.startswith("'") and value.endswith("'"))
                            or (value.startswith('"') and value.endswith('"'))
                            or value.isdigit()
                        ):
                            has_unresolved = True
                            break
                    else:
                        # Positional argument
                        if resolve_value(token, context_dict) is None and not (
                            (token.startswith("'") and token.endswith("'"))
                            or (token.startswith('"') and token.endswith('"'))
                            or token.isdigit()
                        ):
                            has_unresolved = True
                            break

            if has_unresolved:
                # Leave the original tag in place - it references variables
                # that don't exist in the context yet (e.g., loop variables)
                # The Rust engine will treat this as an unknown tag (empty output)
                logger.debug(
                    "URL tag with unresolved variables (likely loop variable): %s",
                    match.group(0),
                )
                return match.group(0)

            # Resolve the URL
            try:
                url = reverse(
                    url_name, args=args if args else None, kwargs=kwargs if kwargs else None
                )

                if as_variable:
                    # Store in context and return empty string
                    # We'll handle this by adding to context_dict
                    context_dict[as_variable] = url
                    return ""
                else:
                    return url
            except NoReverseMatch as e:
                # Re-raise to match Django's behavior
                raise NoReverseMatch(
                    f"Reverse for '{url_name}' not found. "
                    f"'{url_name}' is not a valid view function or pattern name."
                ) from e

        # Replace all {% url %} tags
        return self._URL_TAG_RE.sub(replace_url_tag, template_source)

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
            # Add CSRF token - force evaluation of lazy string for Rust serialization
            # csrf_token_lazy returns a SimpleLazyObject which must be converted to string
            context_dict["csrf_input"] = str(csrf_input_lazy(request))
            context_dict["csrf_token"] = str(csrf_token_lazy(request))

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

        # Resolve {% url %} tags (must be done after context is fully prepared)
        # This replaces {% url 'name' args %} with the actual resolved URL
        resolved_template = self._resolve_url_tags(resolved_template, context_dict)

        # Serialize remaining context values (datetime, Decimal, UUID, FieldFile, etc.)
        # This ensures all values are JSON-compatible for the Rust engine
        context_dict = serialize_context(context_dict)

        # Render with Rust engine (use resolved template with inheritance resolved)
        # Pass template directories to support {% include %} tags
        try:
            template_dirs = [str(d) for d in self.backend.template_dirs]
            html = self.backend._render_fn_with_dirs(resolved_template, context_dict, template_dirs)
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
