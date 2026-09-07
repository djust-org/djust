"""
Template rendering logic for djust.

Provides the DjustTemplate class that handles template inheritance,
URL tag resolution, JIT serialization, and Rust-based rendering.
"""

from __future__ import annotations

import hashlib
import logging
import re
from os.path import abspath

from typing import TYPE_CHECKING, Any, Callable, Optional, cast

from django.db import models
from django.db.models import QuerySet
from django.template import Context, TemplateDoesNotExist, Origin
from django.template.backends.utils import csrf_input_lazy, csrf_token_lazy
from django.utils.safestring import SafeString

if TYPE_CHECKING:
    from .backend import DjustTemplateBackend

from .serialization import serialize_context

# OUTSIDE the JIT try-block below on purpose (#2322): the two sites that call
# this are the JIT-unavailable and serialization-raised fallbacks, so importing
# it alongside `DjangoJSONEncoder` would leave it undefined in exactly the two
# cases that need it. `djust.serialization` is pure Python and is already a
# hard dependency of this module's happy path.
from ..serialization import model_identity

logger = logging.getLogger(__name__)

# Try to import JIT optimization utilities
try:
    from djust._rust import extract_template_variables, serialize_queryset
    from djust.optimization.query_optimizer import analyze_queryset_optimization, optimize_queryset
    from djust.serialization import DjangoJSONEncoder, normalize_django_value

    # Import from the true source module (re-exported via djust.live_view for
    # back-compat) so the type checker resolves the symbols directly.
    from djust.session_utils import (
        _get_model_hash,
        _jit_serializer_cache,  # Shared cache - cleared by clear_jit_cache()
    )

    JIT_AVAILABLE = True
except ImportError:
    JIT_AVAILABLE = False
    DjangoJSONEncoder = None  # type: ignore[misc,assignment]
    _get_model_hash = None  # type: ignore[assignment]
    _jit_serializer_cache = {}  # Fallback empty cache when JIT not available


class _TemplateSourceWrapper:
    """
    Wrapper to make DjustTemplate compatible with Django template structure.

    Django templates have: template.template.source
    This provides the .template attribute for compatibility.
    """

    def __init__(self, source: str):
        self.source = source


#: Exceptions that mean "the project's own code raised", not "the template
#: engine failed" — so they must cross a render boundary unwrapped (#2508).
#:
#: This is EXACTLY the set `django.core.handlers.exception.response_for_exception`
#: dispatches on, read from that function rather than recalled: `Http404` -> 404,
#: `PermissionDenied` -> 403, `MultiPartParserError` -> 400, `BadRequest` -> 400,
#: `SuspiciousOperation` -> 400. Wrapping any of them costs a status code, which
#: no template-location hint is worth.
#:
#: The first version of this list had three of the five. `BadRequest` and
#: `MultiPartParserError` are SIBLINGS of `SuspiciousOperation`, not subclasses,
#: so the `isinstance` did not reach them and both still rendered 500 — the same
#: defect this function exists to close, left half-closed (#2508 re-review). If
#: Django's dispatch set changes, this list is what has to change with it.
#:
#: `ObjectDoesNotExist` is deliberately ABSENT: Django assigns it no status, and
#: it is silent at the lookup layer (rendered empty long before it reaches here),
#: so listing it only cost the hint. `ImproperlyConfigured` is absent for the
#: same reason — a setup failure, where the hint is genuinely useful.
_MISSING_TEMPLATE_RE = re.compile(r"Template not found: (?P<name>[^\n]+)")


def _missing_template_name(message: str) -> str | None:
    """The template name from the engine's "Template not found" error, or None.

    Compatibility fallback for older extensions and custom loaders. Current
    filesystem errors carry the name and searched paths as structured data.
    """
    match = _MISSING_TEMPLATE_RE.search(message)
    if match is None:
        return None
    return match.group("name").strip()


def _missing_template_exception(error: Exception, backend: Any) -> TemplateDoesNotExist | None:
    """Reconstruct Django's lookup failure without parsing origin paths."""
    name = getattr(error, "djust_missing_template_name", None)
    if name is None:
        name = _missing_template_name(str(error))
    if name is None:
        return None
    skipped = set(getattr(error, "djust_skipped_template_paths", ()))
    tried = [
        (
            Origin(name=abspath(path), template_name=name, loader=backend),
            "Skipped to avoid recursion" if path in skipped else "Source does not exist",
        )
        for path in getattr(error, "djust_tried_template_paths", ())
    ]
    return TemplateDoesNotExist(name, tried=tried, backend=backend)


def _is_user_raised(exc: BaseException) -> bool:
    """Did this exception arrive WHOLE from code the render called (#2605)?

    Two stamps, no type allow-list. An exception is user-raised by
    PROVENANCE, not by what class it happens to be:

    * ``_djust_python_exception`` — set by ``DjangoRustError::PythonException``'s
      ``PyErr`` conversion (``crates/djust_core/src/errors.rs``) on every
      exception that crossed PyO3 whole: a property or nullary method that
      raised during a lookup (#2508, #2568), a `{% url %}` handler's
      ``NoReverseMatch`` (#2563), a tag handler's own ``TemplateSyntaxError``.
    * ``raised_by_library`` — set by the bridge on an exception that came out
      of a bridged Django template library's own code, Django's
      ``TemplateSyntaxError`` from ``parse_bits``, or the `{% load %}` loader
      (#2547). See ``template_libraries._raised_by_library``.

    Everything else reaching the two ``except`` blocks is an ENGINE failure —
    a ``DjangoRustError`` that arrives as an untyped ``RuntimeError`` — and is
    what the wrapping below exists for. Until #2605 a five-type allow-list
    (``Http404``, ``PermissionDenied``, ``MultiPartParserError``,
    ``BadRequest``, ``SuspiciousOperation``) plus ``NoReverseMatch`` and
    ``TemplateSyntaxError`` sat beside the stamps; every member was already
    stamped on every path it can actually reach these blocks by, and a list
    of types someone thought of is exactly the shape that misses the next
    custom exception (#2568 measured ``ApplicationError``).
    """
    if getattr(exc, "_djust_python_exception", False):
        return True
    from ..template_libraries import raised_by_library

    return raised_by_library(exc)


class DjustTemplate:
    """
    Wrapper for a template rendered with djust's Rust engine.

    Compatible with Django's template interface.
    """

    # Pre-compiled regex patterns for template inheritance processing

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
        from .exceptions import UNKNOWN_SOURCE

        self.template_string = template_string
        self.source = template_string
        self.backend = backend
        self.engine = backend
        self.origin = origin if origin is not None else Origin(name=UNKNOWN_SOURCE)

        # Add .template.source for LiveView compatibility
        # LiveView expects: template.template.source
        self.template = _TemplateSourceWrapper(template_string)

        self._compile()

    def _compile(self) -> None:
        """Parse the source now, where Django's ``Engine.from_string`` /
        ``get_template`` parse (#2549).

        Until #2549 the Rust parse ran at first ``render``, so a syntax
        error surfaced one call later than Django's, and a defect in a
        branch that never rendered never surfaced at all. The parse goes
        through the engine's ``TEMPLATE_CACHE``, so the render that follows
        finds it parsed and does not pay twice. Raises
        ``DjustTemplateSyntaxError`` — Django's ``TemplateSyntaxError`` and
        a ``RuntimeError`` — with the engine's message text unchanged.

        ``_ensure_custom_filters_bridged`` runs first for the same reason
        ``render`` runs it: the parser refuses an unknown filter (#2419),
        and a project's ``@register.filter`` callables reach the Rust
        registry only through that bridge.

        When the engine knows WHERE the parse failed it hands the byte span
        of the offending token back on the exception's ``djust_token_span``
        attribute (``crates/djust_live/src/lib.rs`` ``SPAN_ATTR``); that
        becomes Django's ``template_debug`` dict, so the technical-500 page
        shows the template name, the line and the source excerpt instead of
        a bare message (#2557).
        """
        from .._rust import compile_template
        from ..template_filters import _ensure_custom_filters_bridged
        from ..template_libraries import rendering_with_backend
        from .exceptions import DjustTemplateSyntaxError, build_template_debug

        try:
            # Library lookup and tag compilation belong to this engine, just
            # as library rendering does. Restore the enclosing engine on exit.
            with rendering_with_backend(self.backend):
                _ensure_custom_filters_bridged()
                self._compiled_template = compile_template(
                    self.template_string,
                    getattr(self.origin, "template_name", None),
                    return_template=True,
                )
        except Exception as e:
            message = str(e)
            user_raised = _is_user_raised(e)
            syntax_message = getattr(e, "djust_template_syntax_message", None)
            if not user_raised and isinstance(syntax_message, str):
                message = syntax_message
            if not user_raised and not isinstance(e, RuntimeError):
                raise
            # The parser does not know the loader's template name. Complete
            # Django's must-be-first diagnostic here, at the origin boundary.
            first_tag_suffix = " must be the first tag in the template."
            template_name = getattr(self.origin, "template_name", None)
            if (
                not user_raised
                and template_name
                and message.startswith("Template error: {% extends")
                and message.endswith(first_tag_suffix)
            ):
                message = message[: -len(first_tag_suffix)] + (
                    f" must be the first tag in {template_name!r}."
                )
            exc = e if user_raised else DjustTemplateSyntaxError(message, origin=self.origin)
            span = getattr(e, "djust_token_span", None)
            if span is not None:
                setattr(
                    exc,
                    "template_debug",
                    build_template_debug(
                        self.template_string,
                        getattr(self.origin, "name", None),
                        span[0],
                        span[1],
                        message,
                    ),
                )
            if user_raised:
                raise
            raise exc from e

    def _annotate_loaded_error(self, error: Exception, original: Exception) -> None:
        """Attach the loaded source's location without replacing a user exception."""
        if not getattr(self.backend, "debug", False):
            return
        if getattr(error, "template_debug", None) is not None:
            return
        source = getattr(original, "djust_template_source", None)
        span = getattr(original, "djust_token_span", None)
        if not isinstance(source, str) or span is None:
            return
        from .exceptions import build_template_debug

        error.template_debug = build_template_debug(  # type: ignore[attr-defined]
            source,
            (
                self.origin.name
                if getattr(original, "djust_template_origin", None) == "<unknown source>"
                and source == self.template_string
                else getattr(original, "djust_template_origin", None)
            ),
            span[0],
            span[1],
            str(error),
        )

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
            logger.debug("[JIT] Not available, using DjangoJSONEncoder for '%s'", variable_name)
            return [normalize_django_value(obj) for obj in queryset]

        try:
            # Extract variable paths from template
            variable_paths_map = extract_template_variables(self.template_string)
            paths_for_var = variable_paths_map.get(variable_name, [])

            if not paths_for_var:
                # No template access detected, use default serialization
                logger.debug(
                    "[JIT] No paths found for '%s', using DjangoJSONEncoder", variable_name
                )
                return [normalize_django_value(obj) for obj in queryset]

            # Generate cache key (includes model hash for invalidation on model changes)
            model_class = queryset.model
            template_hash = hashlib.sha256(self.template_string.encode()).hexdigest()[:8]
            model_hash = _get_model_hash(model_class) if _get_model_hash else ""
            cache_key = (template_hash, variable_name, model_hash)

            # Check cache
            if cache_key in _jit_serializer_cache:
                paths_for_var, optimization = _jit_serializer_cache[cache_key]
                logger.debug("[JIT] Cache HIT for '%s' - paths: %s", variable_name, paths_for_var)
            else:
                # Analyze and cache optimization
                optimization = analyze_queryset_optimization(model_class, paths_for_var)

                logger.debug(
                    "[JIT] Cache MISS for '%s' (%s) - paths: %s",
                    variable_name,
                    model_class.__name__,
                    paths_for_var,
                )
                if optimization:
                    logger.debug(
                        "[JIT] Query optimization: select_related=%s, prefetch_related=%s",
                        sorted(optimization.select_related),
                        sorted(optimization.prefetch_related),
                    )

                _jit_serializer_cache[cache_key] = (paths_for_var, optimization)

            # Optimize queryset (prevents N+1 queries)
            if optimization:
                queryset = optimize_queryset(queryset, optimization)

            # Serialize with Rust (5-10x faster)
            result = serialize_queryset(list(queryset), paths_for_var)

            logger.debug(
                "[JIT] Serialized %s objects for '%s' using Rust", len(result), variable_name
            )
            return result

        except Exception as e:
            # Graceful fallback
            logger.warning(
                "[JIT] Serialization failed for '%s': %s", variable_name, e, exc_info=True
            )
            return [normalize_django_value(obj) for obj in queryset]

    def _jit_serialize_model(self, model_instance: models.Model, variable_name: str) -> dict:
        """
        Serialize a single Django model instance.

        Returns both 'id' and 'pk' as native types for consistent template comparisons.
        This ensures {% if item.id == state_var %} works with integer comparisons.

        Args:
            model_instance: Django model instance
            variable_name: Variable name in template

        Returns:
            Serialized dictionary with 'id' and 'pk' keys (both native types)
        """
        if not JIT_AVAILABLE or DjangoJSONEncoder is None:
            # Fallback to the identity map — one producer for it (#2322), so
            # this answers exactly what the main path does. Before that, whether
            # a serialized model carried `__model__` depended on whether the JIT
            # extension happened to be importable.
            return model_identity(model_instance)

        try:
            return cast(dict, normalize_django_value(model_instance))
        except Exception as e:
            logger.warning("Model serialization failed for '%s': %s", variable_name, e)
            # Same map on the raised path: a consumer must not be able to tell
            # that serialization failed by the SHAPE it got back (#2322).
            return model_identity(model_instance)

    def render(self, context: Any = None, request: Any = None) -> SafeString:
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
        # Preserve source and let the native loader resolve inheritance at render time.
        resolved_template = self.template_string
        assignments: dict[str, Any] = {}

        # Convert context to dict
        if context is None:
            context_dict = {}
        elif hasattr(context, "flatten"):
            # Django Context object
            context_dict = context.flatten()
        else:
            context_dict = dict(context)

        # A `RequestContext` carries its request as an ATTRIBUTE, and
        # `flatten()` drops it (#2556, the request slice of #2550): Django's
        # `Template.render(RequestContext(request))` can reach
        # `context.request` (`{% querystring %}` reads it), so this path must
        # too when the caller did not pass `request=` separately.
        if request is None:
            request = getattr(context, "request", None)

        # Add request to context if provided
        if request is not None:
            context_dict["request"] = request
            # Add CSRF token - force evaluation of lazy string for Rust serialization
            # csrf_token_lazy returns a SimpleLazyObject which must be converted to string
            context_dict["csrf_input"] = str(csrf_input_lazy(request))
            context_dict["csrf_token"] = str(csrf_token_lazy(request))
            # csrf_input contains raw HTML — mark it safe to skip auto-escaping
            self._safe_keys = ["csrf_input"]

        # Apply context processors
        if request is not None:
            for processor_path in self.backend.context_processors:
                processor = self._get_context_processor(processor_path)
                context_dict.update(processor(request))

        # Retain objects for the protected lookup sidecar before JIT replaces
        # models/querysets with dictionaries. Rust applies the same sidecar
        # protection as its direct render entry points.
        raw_context = dict(context_dict)

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

        # Prepare file fields and forms for rendering while retaining the
        # Python types accepted directly by the native render API.
        # Form/BoundField objects are converted to SafeString dicts here, so
        # safe-key detection must run AFTER serialization to catch nested paths
        # like "form.first_name".
        context_dict = serialize_context(context_dict, for_render=True)

        # Detect SafeString values after serialization so that SafeStrings
        # produced by Form/BoundField rendering (above) are included.
        # Use _collect_safe_keys for recursive detection of dotted paths
        # (e.g. "form.first_name") in addition to top-level keys.
        safe_keys = list(getattr(self, "_safe_keys", None) or [])
        try:
            from djust.mixins.rust_bridge import _collect_safe_keys

            for key, value in context_dict.items():
                safe_keys.extend(k for k in _collect_safe_keys(value, key) if k not in safe_keys)
        except ImportError:
            # Fallback: top-level SafeString detection only
            for key, value in context_dict.items():
                if isinstance(value, SafeString) and key not in safe_keys:
                    safe_keys.append(key)

        # Render with Rust engine (use resolved template with inheritance resolved)
        # Pass template directories to support {% include %} tags
        try:
            # Per-render Django settings the Rust engine cannot read for itself:
            # the active timezone (#2209) and number format (#2221). This is a
            # TOP-LEVEL render path — a plain Django template rendered through
            # djust's backend — and it was the third one, unwired while the two
            # LiveView paths were fixed (#2223). On a fresh worker thread it
            # rendered `1234567|23:30` where Django renders `1,234,567|19:30`.
            #
            # Here rather than inside `_rust.render_template*` deliberately:
            # measured at ~12us against ~15us for a small render, so pushing on
            # every call — including the many NESTED component renders that
            # already inherit a correct thread-local from their enclosing
            # render — would be ~78% overhead for no gain. Top-level entries
            # pay it once; nested ones inherit.
            from ..render_env import apply_render_env

            apply_render_env()
            # The project's ``@register.filter`` callables, forwarded to the
            # Rust registry BEFORE the engine can parse this source (#2419).
            #
            # Since #2419 an unknown filter refuses at PARSE time, as it does
            # on Django, so "which names exist" has to be settled before the
            # first parse rather than at the moment a value flows through the
            # filter. ``DjustConfig.ready()`` already warms the bridge at
            # startup, and the LiveView path re-arms it in
            # ``_initialize_rust_view`` — this is the third top-level render
            # entry and was the one relying on the startup warm alone, so a
            # project that sets ``filter_bridge_warm = False`` had no bridge
            # here at all and its custom filters did not resolve. Same
            # parallel-path shape as #2223, one entry point over.
            #
            # Free after the first call: ``_ensure_custom_filters_bridged``
            # short-circuits on a module-level flag and never raises.
            from ..template_filters import _ensure_custom_filters_bridged

            _ensure_custom_filters_bridged()
            template_dirs = [str(d) for d in self.backend.template_dirs]
            # ADR-024 auto-call kill-switch. The Rust entry point defaults it
            # ON (Django's behaviour) for a caller that reaches it directly;
            # this path is a project's render, so the project's
            # `LIVEVIEW_CONFIG['template_auto_call']` governs it — the same
            # flag `_apply_template_auto_call_flag` wires on the LiveView path
            # (#2501). One shared reader, called by all three framework render
            # paths (#2508 review).
            from ..config import template_auto_call_enabled

            auto_call = template_auto_call_enabled()
            # The backend a bridged `inclusion_tag` renders its template
            # through, and the library map a `{% load %}` in THIS parse
            # resolves against (#2547). A ContextVar, NOT a context-dict
            # entry: a backend object in the context is the #2516 segfault
            # class. Set around the Rust call only, so a nested render
            # (an inclusion tag's own template) inherits it and a LiveView
            # render, which never comes through here, sees the fallback.
            from ..template_libraries import rendering_with_backend

            with rendering_with_backend(
                self.backend,
                use_l10n=getattr(context, "use_l10n", None),
                use_tz=getattr(context, "use_tz", None),
            ):
                html = self.backend._render_fn_with_dirs(
                    resolved_template,
                    context_dict,
                    template_dirs,
                    safe_keys or None,
                    auto_call,
                    getattr(self.backend, "string_if_invalid", "") or None,
                    # This template's own name, for relative `{% extends %}`
                    # (#2517). `Origin.template_name` is what Django's
                    # `construct_relative_path` reads.
                    getattr(self.origin, "template_name", None) if self.origin else None,
                    raw_context=raw_context,
                    compiled_template=self._compiled_template,
                    assignments=assignments,
                    uncached_template_dirs=[
                        str(path) for path in getattr(self.backend, "uncached_template_dirs", [])
                    ],
                    autoescape=bool(
                        context.autoescape
                        if isinstance(context, Context)
                        else getattr(self.backend, "autoescape", True)
                    ),
                )

            # In DEBUG mode, inject data-dj-src attributes for template source mapping.
            # This adds the template filename to opening HTML element tags, enabling
            # the djust-browser-mcp find_by_template tool to link DOM elements back
            # to their source templates.
            from django.conf import settings

            if getattr(settings, "DEBUG", False) and self.origin:
                template_name = getattr(self.origin, "template_name", None)
                if template_name:
                    html = self._inject_source_mapping(html, template_name)

            return SafeString(html)
        except Exception as e:
            # An exception RAISED BY THE PROJECT'S OWN CODE during a lookup
            # (a property, a nullary method) crosses back whole so Django's
            # handler chain can dispatch on its type — `PermissionDenied` to
            # 403, `Http404` to 404 (#2508). Re-wrapping it as a bare
            # `Exception` made both a 500, and the template-location hint
            # below is worth nothing next to losing the status code. A real
            # ENGINE failure (unsupported tag, parse error) still gets the
            # hint, which is what it was written for.
            if _is_user_raised(e):
                self._annotate_loaded_error(e, e)
                raise

            # A missing `{% extends %}` / `{% include %}` target is Django's
            # `TemplateDoesNotExist`, not a bare `Exception` (#2517). Callers
            # dispatch on the TYPE — Django's own `{% include %}` tests assert
            # `assertRaises(TemplateDoesNotExist)`, and the loader chain
            # catches it to try the next loader — so re-wrapping it lost both
            # behaviours. The engine reports the name it could not resolve;
            # that name is what Django puts on the exception.
            syntax_message = getattr(e, "djust_template_syntax_message", None)
            if isinstance(syntax_message, str):
                from .exceptions import DjustTemplateSyntaxError

                origin_path = getattr(e, "djust_template_origin", None)
                origin = Origin(name=origin_path) if origin_path else self.origin
                error = DjustTemplateSyntaxError(syntax_message, origin=origin)
                self._annotate_loaded_error(error, e)
                raise error from e

            missing = _missing_template_exception(e, self.backend)
            if missing is not None:
                self._annotate_loaded_error(missing, e)
                raise missing from e

            # Provide helpful error message with template location
            origin_info = f" (from {self.origin.name})" if self.origin else ""

            # Check if error might be due to unsupported template tag/filter
            error_msg = str(e)
            if (
                "Unsupported tag" in error_msg
                or "Unknown filter" in error_msg
                or "Invalid filter:" in error_msg
            ):
                suggestion = (
                    "\n\nHint: This template uses features not yet supported by djust's Rust engine. "
                    "Consider using workarounds (see docs/TEMPLATE_BACKEND.md) or use Django's "
                    "template backend for this specific template."
                )
                raise Exception(
                    f"Error rendering template{origin_info}: {error_msg}{suggestion}"
                ) from e

            # Native runtime failures remain catchable as RuntimeError while
            # gaining the same debug metadata as exceptions from user code.
            runtime_error = RuntimeError(f"Error rendering template{origin_info}: {error_msg}")
            self._annotate_loaded_error(runtime_error, e)
            raise runtime_error from e
        finally:
            # Django writes top-level `{% … as var %}` bindings through to the
            # caller's object on BOTH APIs: `Context(d)` pushes `d` itself as a
            # frame, and the backend's `make_context(d)` wraps `d` the same
            # way — so `render(d)` leaves `d["var"]` set in Django too. Verified
            # against Django 5.2 in the #2665 review; a plain dict is mutated
            # on purpose, for parity.
            if isinstance(context, (dict, Context)):
                for name, value in assignments.items():
                    context[name] = value

    # Regex to match opening HTML element tags (not comments, not closing tags, not doctypes)
    _OPENING_TAG_RE = re.compile(
        r"<([a-zA-Z][a-zA-Z0-9]*)"  # Tag name
        r"(\s|>|/>)",  # Followed by whitespace, >, or />
    )

    def _inject_source_mapping(self, html: str, template_name: str) -> str:
        """
        Inject data-dj-src attributes into opening HTML element tags.

        Only adds to root-level elements (depth 0) to avoid excessive bloat.
        The attribute value is the template filename (e.g., "dashboard.html").

        This enables the djust-browser-mcp find_by_template tool to link
        DOM elements back to their source template files.
        """
        # Escape the template name for use in HTML attributes
        safe_name = template_name.replace('"', "&quot;")
        attr = f' data-dj-src="{safe_name}"'

        # Add data-dj-src to the first opening tag only (root element).
        # This avoids bloating every element while still enabling template lookup.
        return self._OPENING_TAG_RE.sub(
            lambda m: f"<{m.group(1)}{attr}{m.group(2)}",
            html,
            count=1,  # Only first match
        )

    def _get_context_processor(self, processor_path: str) -> Callable[..., Any]:
        """Import and return a context processor function."""
        from django.utils.module_loading import import_string

        return cast("Callable[..., Any]", import_string(processor_path))
