"""
JITMixin - JIT auto-serialization for QuerySets and Models.
"""

import hashlib
import logging
import os
import re
import sys
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, cast

from .._render_caches import render_caches_enabled
from .._rust import extract_template_variables
from ..optimization.codegen import compile_serializer, generate_serializer_code
from ..optimization.query_optimizer import analyze_queryset_optimization, optimize_queryset
from ..serialization import model_identity, normalize_django_value
from ..session_utils import _jit_serializer_cache, _get_model_hash

logger = logging.getLogger(__name__)

# Template source → its short sha256 (``_template_hash``). Keyed by the source
# itself, not ``id()``: an id is reused once its string is freed, and a cache
# keyed by it handed a new template the hash of an old one (and grew without
# bound, one entry per render). A dict lookup by the same string object costs
# an identity check, so a cached source (``_jit_template_content``) hashes once.
_template_hash_cache: Dict[str, str] = {}
_TEMPLATE_HASH_CACHE_MAX = 256

# Cache (template_hash, variable_name) → expected top-level key count
_expected_keys_cache: Dict[Tuple[str, str], int] = {}

# Pre-compiled regex for {% include %} — handles normal and doubled quotes from Rust resolver
_INCLUDE_RE = re.compile(
    r"\{%\s*include\s+(?:\"{1,2}([^\"]+)\"{1,2}|'{1,2}([^']+)'{1,2})(.*?)%\}",
    re.DOTALL,
)


# Cache template content hash → extract_template_variables() result.
# Keyed by SHA-256 content hash (not id()) so it survives GC and works across requests.
# Hot reload is safe: changed template content produces a different hash.
# Size cap: one entry per unique template file; typical djust apps have < 50 templates.
# If the cache exceeds 256 entries (defensive cap), it is cleared to prevent unbounded growth.
_variable_extraction_cache: Dict[str, Optional[dict]] = {}
_VARIABLE_EXTRACTION_CACHE_MAX = 256


def _cached_extract_template_variables(template_content: str) -> Optional[dict]:
    """Extract template variables with caching by content hash.

    Returns the variable paths map, or None if extraction failed.
    """
    content_hash = _template_hash(template_content)

    # One lookup, not `in` then `[]`: another thread may `.clear()` the cache
    # between the two (#3074). The cached value itself can be None.
    try:
        return _variable_extraction_cache[content_hash]
    except KeyError:
        pass

    try:
        result = extract_template_variables(template_content)
    except Exception:
        logger.debug("Rust template variable extractor unavailable, using fallback")
        result = None

    # Defensive size cap: clear cache if it grows beyond expected cardinality.
    if len(_variable_extraction_cache) >= _VARIABLE_EXTRACTION_CACHE_MAX:
        _variable_extraction_cache.clear()

    _variable_extraction_cache[content_hash] = result
    return result


def _template_hash(template_content: str) -> str:
    """The short sha256 of a template source, computed once per source."""
    try:
        return _template_hash_cache[template_content]
    except KeyError:
        pass
    digest = hashlib.sha256(template_content.encode()).hexdigest()[:8]
    if len(_template_hash_cache) >= _TEMPLATE_HASH_CACHE_MAX:
        _template_hash_cache.clear()
    _template_hash_cache[template_content] = digest
    return digest


# The JIT's extraction source per template (Point Guard N-37). Building it
# resolves the ``{% extends %}`` chain in Rust and reads every ``{% include %}``
# from disk, on every render that serializes a model. The result depends on
# the template's text and the template directories only, so it is kept per
# (source or name, directories) while templates are cached: with DEBUG off,
# when Django's cached loader serves a template unchanged until restart. With
# DEBUG on (hot reload edits templates) it is built on every render, as before.
_template_source_cache: Dict[Tuple[str, str, Tuple[str, ...]], str] = {}
_TEMPLATE_SOURCE_CACHE_MAX = 256


def clear_template_source_caches() -> None:
    """Forget every cached extraction source, hash and variable map."""
    _template_source_cache.clear()
    _template_hash_cache.clear()
    _variable_extraction_cache.clear()


def _template_source_key(view: Any) -> Optional[Tuple[str, str, Tuple[str, ...]]]:
    from ..utils import get_template_dirs

    full = getattr(view, "_full_template", None)
    named_source = getattr(view, "_named_partial_jit_source", None)
    template_name = getattr(view, "template_name", None)
    if named_source is None and template_name and "#" in template_name:
        import django

        if django.VERSION >= (6, 0) and template_name.partition("#")[2]:
            # The selected-body key is unavailable until the view resolves it.
            # Do not reuse an entry based only on the include stub.
            return None
    if named_source is not None:
        # The include stub stays constant while the selected base source can
        # change. Keep extraction caches keyed by the actual selected body.
        # Included templates are read independently of Django's cached base
        # loader and may change without changing this selected body. Resolve
        # those dependencies on each extraction rather than caching stale JIT
        # field sets. Named partials without includes remain cacheable.
        if _INCLUDE_RE.search(named_source):
            return None
        kind, ident = "named-partial", f"{getattr(view, 'template_name', '')}\n{named_source}"
    elif full and not _EXTENDS_STUB_RE.match(full):
        kind, ident = "full", full
    elif getattr(view, "template", None):
        kind, ident = "inline", view.template
    elif getattr(view, "template_name", None):
        kind, ident = "name", view.template_name
    else:
        return None
    if not isinstance(ident, str):
        return None
    return (kind, ident, tuple(get_template_dirs()))


def _jit_template_content(view: Any) -> Optional[str]:
    """``view._get_template_content()``, kept per template while templates are cached.

    A view (or an instance) that overrides ``_get_template_content`` is called
    every time: its answer may depend on more than the template.
    """
    if (
        "_get_template_content" in getattr(view, "__dict__", {})
        or getattr(type(view), "_get_template_content", None) is not JITMixin._get_template_content
        or not render_caches_enabled()
    ):
        return cast(Optional[str], view._get_template_content())
    key = _template_source_key(view)
    if key is None:
        return cast(Optional[str], view._get_template_content())
    try:
        return _template_source_cache[key]
    except KeyError:
        pass
    content = view._get_template_content()
    if content:
        if len(_template_source_cache) >= _TEMPLATE_SOURCE_CACHE_MAX:
            _template_source_cache.clear()
        _template_source_cache[key] = content
    return cast(Optional[str], content)


# A `_full_template` that is nothing but `{% extends "x" %}`: the child's
# real source lives behind the loader, so this carries zero variable paths.
_EXTENDS_STUB_RE = re.compile(r"^\s*\{%\s*extends\s+[^%]*%\}\s*$")


# (type, paths) -> compiled serializer, for serialize_for_template's non-model
# objects. Bounded like the other JIT caches.
_object_serializer_cache: Dict[Tuple[type, Tuple[str, ...]], Any] = {}
_OBJECT_SERIALIZER_CACHE_MAX = 256

_LEAF_TYPES = (str, bytes, int, float, bool, dict)


def _serialize_object_by_paths(value: Any, paths: List[str]) -> Any:
    """A non-model ``value`` reduced to ``paths`` (``serialize_for_template``)."""
    if value is None or isinstance(value, _LEAF_TYPES):
        return value
    if not paths:
        return normalize_django_value(value)
    key = (type(value), tuple(sorted(set(paths))))
    serializer = _object_serializer_cache.get(key)
    if serializer is None:
        name = type(value).__name__
        if not name.isidentifier():
            name = "Object"
        func_name = "serialize_object_%s" % hashlib.sha256(repr(key).encode()).hexdigest()[:8]
        try:
            code = generate_serializer_code(name, list(key[1]), func_name)
            serializer = compile_serializer(code, func_name)
        except (ValueError, SyntaxError):
            logger.debug("No path serializer for %s; using normalize_django_value", name)
            return normalize_django_value(value)
        if len(_object_serializer_cache) >= _OBJECT_SERIALIZER_CACHE_MAX:
            _object_serializer_cache.clear()
        _object_serializer_cache[key] = serializer
    return serializer(value)


def _recursive_queryset_relation(
    content: str, variable_name: str, *, single: bool = False
) -> Optional[str]:
    """Find the safe, direct self-recursive ORM pattern in extraction source.

    Other recursive patterns cannot be eagerly serialized correctly: their
    stopping conditions may depend on context that is only known at render
    time. Refuse them rather than silently truncating the model graph.
    """
    from django.template.base import DebugLexer, TokenType
    from .._rust import raw_block_end_tag

    marker = "djust:recursive-partial"
    if marker not in content:
        return None
    stack: list[tuple[str, list[str], bool]] = []
    relations = set()
    raw_end = None
    in_comment = False
    unknown_control = False
    for token in DebugLexer(content).tokenize():
        if token.token_type == TokenType.BLOCK:
            bits = token.split_contents()
            if raw_end:
                if bits and bits[0] == raw_end:
                    raw_end = None
                continue
            if bits and bits[0] == "comment":
                in_comment = True
                continue
            if bits and bits[0] == "endcomment":
                in_comment = False
                continue
            if in_comment:
                continue
            if bits:
                raw_end = raw_block_end_tag(bits[0])
                if raw_end:
                    continue
        elif raw_end or in_comment:
            continue
        if token.token_type == TokenType.COMMENT and token.contents.strip() == marker:
            loops = [i for i, frame in enumerate(stack) if frame[0] == "for"]
            if not loops:
                # Attribute the recursive alias to its context root before
                # rejecting other unsupported forms, so unrelated model and
                # QuerySet context values are not affected.
                condition_index = next(
                    (i for i in range(len(stack) - 1, -1, -1) if stack[i][0] == "if"),
                    None,
                )
                owner = ""
                if condition_index is not None and len(stack[condition_index][1]) > 1:
                    expression = stack[condition_index][1][1]
                    match = re.match(r"[A-Za-z][A-Za-z0-9_]*", expression)
                    owner = match.group() if match else ""
                    for tag, bits, _ in reversed(stack[:condition_index]):
                        if tag == "with":
                            for bit in bits[1:]:
                                if "=" in bit:
                                    alias, value = bit.split("=", 1)
                                    if alias == owner:
                                        match = re.match(r"[A-Za-z][A-Za-z0-9_]*", value)
                                        owner = match.group() if match else ""
                if owner and owner != variable_name:
                    continue
                if not single:
                    raise ValueError(
                        "Unsupported recursive partial over model collection without a direct loop"
                    )
                frames = stack
                if unknown_control or len(frames) not in (2, 3):
                    raise ValueError("Unsupported recursive partial over a single model")
                if len(frames) == 3:
                    prefix = frames[0]
                    if prefix[0] != "with" or len(prefix[1]) != 2 or prefix[2]:
                        raise ValueError("Unsupported recursive partial over a single model")
                    prefix_binding = prefix[1][1].split("=", 1)
                    if len(prefix_binding) != 2 or prefix_binding[1] != variable_name:
                        continue
                    alias = prefix_binding[0]
                    frames = frames[1:]
                else:
                    alias = variable_name
                single_condition, single_binding = frames
                if (
                    single_condition[0] != "if"
                    or single_binding[0] != "with"
                    or single_condition[2]
                    or single_binding[2]
                    or len(single_condition[1]) != 2
                    or len(single_binding[1]) != 2
                ):
                    raise ValueError("Unsupported recursive partial over a single model")
                match = re.fullmatch(
                    r"([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z][A-Za-z0-9_]*)",
                    single_condition[1][1],
                )
                if (
                    not match
                    or match.group(1) != alias
                    or single_binding[1][1] != f"{alias}={single_condition[1][1]}"
                ):
                    raise ValueError("Unsupported recursive partial over a single model")
                relations.add(match.group(2))
                continue
            if single:
                # A standalone model can recurse inside a loop over unrelated
                # data. That shape is not supported by the eager serializer.
                for tag, bits, _ in reversed(stack):
                    if tag == "if" and len(bits) > 1:
                        match = re.match(r"[A-Za-z][A-Za-z0-9_]*", bits[1])
                        if match and match.group() == variable_name:
                            raise ValueError("Unsupported recursive single model inside a loop")
                        break
                continue
            aliases = {
                bit.split("=", 1)[0]: bit.split("=", 1)[1]
                for name, bits, _ in stack
                if name == "with"
                for bit in bits[1:]
                if "=" in bit
            }

            def root_of(expression: str) -> str:
                visited = set()
                while True:
                    match = re.match(r"[A-Za-z][A-Za-z0-9_]*", expression)
                    if not match:
                        return ""
                    root = match.group()
                    if root not in aliases or root in visited:
                        return root
                    visited.add(root)
                    expression = aliases[root]

            associated = []
            for index in loops:
                bits = stack[index][1]
                try:
                    iterable = bits[bits.index("in") + 1]
                except (ValueError, IndexError):
                    raise ValueError("Unsupported recursive partial collection loop") from None
                if root_of(iterable) == variable_name:
                    associated.append(index)
            if not associated:
                continue
            loop = associated[-1]
            frames = stack[loop:]
            loop_bits = frames[0][1]
            valid = (
                not unknown_control
                and not any(frame[0] != "with" for frame in stack[:loop])
                and len(frames) == 3
                and all(not frame[2] for frame in frames)
                and [frame[0] for frame in frames] == ["for", "if", "with"]
                and len(loop_bits) == 4
                and loop_bits[2] == "in"
                and loop_bits[3] == variable_name
            )
            relation_match = None
            if valid:
                alias = loop_bits[1]
                condition = frames[1][1]
                binding = frames[2][1]
                relation_match = re.fullmatch(
                    r"([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z][A-Za-z0-9_]*)",
                    condition[1] if len(condition) == 2 else "",
                )
                valid = (
                    relation_match is not None
                    and relation_match.group(1) == alias
                    and len(binding) == 2
                    and binding[1] == f"{alias}={condition[1]}"
                )
            if not valid or relation_match is None:
                raise ValueError(
                    "Unsupported recursive partial over model collection; "
                    "use a direct if/with self-relation traversal"
                )
            relations.add(relation_match.group(2))
        elif token.token_type == TokenType.BLOCK:
            bits = token.split_contents()
            if not bits:
                continue
            name = bits[0]
            if name in {"for", "if", "with", "autoescape", "filter", "block", "comment"}:
                stack.append((name, bits, False))
            elif name.startswith("end"):
                closing = name[3:]
                if stack and stack[-1][0] == closing:
                    stack.pop()
            elif name in {"else", "elif", "empty"} and stack:
                old = stack[-1]
                stack[-1] = (old[0], old[1], True)
            elif name not in {"partial"}:
                unknown_control = True
                # Unknown tags may be custom control blocks. Conservatively
                # reject a loop that contains one rather than skipping a
                # data-dependent recursion guard such as ifchanged.
                for index, frame in enumerate(stack):
                    if frame[0] == "for":
                        stack[index] = (frame[0], frame[1], True)
    if len(relations) > 1:
        raise ValueError("Unsupported multiple recursive relations for one model collection")
    return next(iter(relations), None)


def _serialize_recursive_models(
    items: Any, rows: List[Any], relation: str, serializer: Any, initial_caches: Dict[int, bool]
) -> List[Any]:
    """Attach only template-requested fields along an authorized self-FK."""
    from django.core.exceptions import FieldDoesNotExist
    from django.db import models
    from ..optimization.codegen import emittable_names

    for item, row in zip(items, rows):
        current, output = item, row
        seen_objects: set[int] = set()
        seen_queries = set()
        while True:
            identity = id(current)
            if identity in seen_objects:
                raise RecursionError("Recursive partial encountered a cyclic model relation")
            if len(seen_objects) >= sys.getrecursionlimit():
                raise RecursionError("Recursive partial exceeded the Python recursion limit")
            seen_objects.add(identity)
            if relation not in emittable_names(current, [relation]):
                break
            try:
                field = current._meta.get_field(relation)
            except FieldDoesNotExist as exc:
                raise ValueError("Recursive partial requires a direct self-relation field") from exc
            if (
                not field.concrete
                or not (field.many_to_one or field.one_to_one)
                or field.related_model._meta.concrete_model is not current._meta.concrete_model
            ):
                raise ValueError("Recursive partial requires a direct self-relation field")
            if not initial_caches.get(id(current), False) and current.pk is not None:
                query_key = (current._meta.concrete_model, current._state.db, current.pk)
                if query_key in seen_queries:
                    raise RecursionError("Recursive partial encountered a cyclic model relation")
                seen_queries.add(query_key)
            child = getattr(current, relation)
            if child is None:
                output[relation] = None
                break
            if not isinstance(child, models.Model):
                raise ValueError("Recursive partial relation did not resolve to a model")
            if not bool(child):
                raise ValueError("Recursive partial cannot serialize a false-valued model relation")
            initial_caches[id(child)] = field.is_cached(child)
            child_row = serializer(child)
            output[relation] = child_row
            current, output = child, child_row
    return rows


class JITMixin:
    """JIT serialization: _jit_serialize_queryset, _jit_serialize_model, _get_template_content."""

    if TYPE_CHECKING:
        # Cooperating attributes supplied by the host class (LiveView,
        # live_view.py:291-292; _full_template is a render-time framework slot).
        # Declared type-only so the strict-island mypy run resolves them on the
        # mixin without a runtime change — this mixin is never instantiated
        # standalone.
        _full_template: Optional[str]
        template: Optional[str]
        template_name: Optional[str]

        def get_template(self) -> str: ...

    def _get_template_content(self) -> Optional[str]:
        """
        Get template source code for JIT variable extraction.

        Prefers the fully-resolved template (with inheritance) so that
        variables used in parent/base templates are also discovered.
        """
        # A Django named partial exposes only its selected body and called
        # helpers to JIT extraction, not unrelated sibling definitions.
        name = getattr(self, "template_name", None)
        if name and "#" in name:
            import django

            if django.VERSION >= (6, 0) and name.partition("#")[2]:
                selected = getattr(self, "_named_partial_jit_source", None)
                if selected is None:
                    self.get_template()
                    selected = getattr(self, "_named_partial_jit_source", None)
                if selected is not None:
                    return self._with_includes(selected)

        # Prefer the fully-resolved template (includes inherited blocks).
        # Since the inheritance fix, `_full_template` may be only an
        # `{% extends %}` stub (mixins/template.py keeps inheritance intact
        # for the initial GET); a stub has no variable paths, and returning
        # it here silently disabled JIT query optimization on every
        # inheritance-using view. Fall through to the resolver instead.
        # Both sources below are the template's own text, which can
        # ``{% include %}`` partials: inline them, as the ``template_name``
        # path does, or a variable read only inside a partial has no path and
        # the JIT serializes every field of its rows (Point Guard N-34).
        if hasattr(self, "_full_template") and self._full_template:
            if not _EXTENDS_STUB_RE.match(self._full_template):
                return self._with_includes(self._full_template)

        if hasattr(self, "template") and self.template:
            return self._with_includes(self.template)

        if hasattr(self, "template_name") and self.template_name:
            # Rust template inheritance resolution. An error RAISES (2.0); it
            # used to be swallowed, silently dropping JIT extraction to the
            # single-file source below.
            from djust._rust import resolve_template_inheritance

            # Use the shared get_template_dirs() helper so JIT extraction
            # resolves templates with the SAME search-dir collection (incl.
            # djust's own backend under APP_DIRS) as get_template() and
            # render_full_template — a hardcoded DjangoTemplates-only check
            # here dropped app-template dirs under the djust backend, the
            # same defect class as #1801 (#1646 parallel-path cure).
            from ..utils import get_template_dirs

            template_dirs = get_template_dirs()

            try:
                resolved = resolve_template_inheritance(self.template_name, template_dirs)
            except Exception as e:
                raise RuntimeError(
                    f"djust could not resolve template inheritance for "
                    f"{self.template_name!r} (searched {len(template_dirs)} template "
                    f"dirs: {template_dirs}): {e}"
                ) from e
            if resolved:
                # Also inline {% include %} directives so variable extraction
                # discovers variables used in included templates
                resolved = self._inline_includes(resolved, template_dirs)
                return resolved

            # Fallback to single-file template source
            try:
                from django.template.loader import get_template

                django_template = get_template(self.template_name)

                if hasattr(django_template, "template") and hasattr(
                    django_template.template, "source"
                ):
                    source: str = django_template.template.source
                    return source
                elif hasattr(django_template, "origin") and hasattr(django_template.origin, "name"):
                    with open(django_template.origin.name, "r") as f:
                        return f.read()
            except Exception as e:
                logger.debug("Could not load template for JIT: %s", e)
                return None

        return None

    def _with_includes(self, source: str) -> str:
        """``source`` with its static ``{% include %}`` partials inlined."""
        try:
            from ..utils import get_template_dirs

            return self._inline_includes(source, get_template_dirs())
        except Exception:  # noqa: BLE001 — extraction source only, never rendered
            # Value-free: the error can carry template text.
            logger.debug("Could not inline includes for JIT extraction")
            return source

    def template_paths(self) -> Dict[str, List[str]]:
        """The dotted paths the view's template reads, by top-level name.

        The same extraction the JIT serializes models by: the template with its
        ``{% extends %}`` chain and static ``{% include %}`` partials, loop
        variables folded onto their iterable (``{% for c in cards %}{{ c.title }}``
        gives ``cards: ["title"]``). A name read whole (``{{ card }}``) maps to
        an empty list. Returns ``{}`` when the template cannot be read or
        parsed.

        Use it with :meth:`serialize_for_template` to hand the template a value
        that is not a model.
        """
        content = self._get_template_content()
        if not content:
            return {}
        found = _cached_extract_template_variables(content) or {}
        return {name: list(paths) for name, paths in found.items() if name != "*"}

    def serialize_for_template(self, value: Any, name: str) -> Any:
        """``value`` as the plain data the template reads under ``name``.

        A model, a QuerySet or a list of models goes through the JIT, exactly as
        a public attribute of that name would. Any other object (a dataclass of
        view logic, a presenter) is reduced to the paths the template reads of
        ``name`` (:meth:`template_paths`): attributes and properties by name, a
        ``get_*`` method called, a dict taken whole, and a list or tuple item by
        item. The serialization floor (``DJUST_SENSITIVE_FIELDS``,
        ``djust_exclude_fields``) applies as it does to a model.

        So a ``get_context_data()`` override can hand the renderer plain data
        instead of the live object (which is not JSON, and which djust warns
        about on every render)::

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context["cards"] = self.serialize_for_template(self._cards, "cards")
                return context

        With no path for ``name`` (the template prints the object whole, or
        does not use it), the value goes through ``normalize_django_value``.
        """
        from django.db import models
        from django.db.models import QuerySet

        from ..utils import is_model_list

        content = self._get_template_content() or ""
        if isinstance(value, QuerySet):
            return self._jit_serialize_queryset(value, content, name)
        if isinstance(value, models.Model):
            return self._jit_serialize_model(value, content, name)
        if is_model_list(value):
            return self._jit_serialize_model_list(value, content, name)
        paths = self.template_paths().get(name, [])
        if isinstance(value, (list, tuple)):
            return [_serialize_object_by_paths(item, paths) for item in value]
        return _serialize_object_by_paths(value, paths)

    @staticmethod
    def _inline_includes(template_content: str, template_dirs: List[str]) -> str:
        """Inline {% include "..." %} directives for variable extraction.

        Static include arguments become scoped ``with`` bindings for extraction.
        ``only`` masks unbound roots in the partial. This is extraction source,
        never the template passed to the renderer. Variable includes remain
        unresolved; nested static includes are bounded to five levels.
        """

        def resolve(content: str, depth: int = 0) -> str:
            if depth > 5:
                return content

            def replacer(match: "re.Match[str]") -> str:
                from django.utils.text import smart_split

                include_path = match.group(1) or match.group(2)
                import django

                base_path, separator, partial_name = include_path.partition("#")
                named_partial = django.VERSION >= (6, 0) and separator and partial_name
                bits = list(smart_split(match.group(3).strip()))
                only = "only" in bits
                if only:
                    bits.remove("only")
                assignments = {}
                if bits:
                    if bits.pop(0) != "with":
                        return match.group(0)
                    for bit in bits:
                        name, sep, value = bit.partition("=")
                        if not sep or not name.isidentifier() or not value:
                            return match.group(0)
                        assignments[name] = value
                for tpl_dir in template_dirs:
                    full_path = os.path.join(tpl_dir, base_path if named_partial else include_path)
                    if os.path.isfile(full_path):
                        try:
                            with open(full_path, "r") as f:
                                included = f.read()
                            if named_partial:
                                from .._named_partials import selected_partial_source

                                included = selected_partial_source(
                                    included, base_path, partial_name
                                )
                            included = resolve(included, depth + 1)
                            if only:
                                roots = _cached_extract_template_variables(included)
                                if roots is None:
                                    return match.group(0)
                                for root in roots:
                                    if root.isidentifier() and root not in assignments:
                                        assignments[root] = '""'
                            if assignments:
                                bindings = " ".join(f"{k}={v}" for k, v in assignments.items())
                                return "{% with " + bindings + " %}" + included + "{% endwith %}"
                            return included
                        except Exception as e:
                            logger.debug("Failed to read included template %s: %s", full_path, e)
                return match.group(0)  # Keep original if not found

            # Match only executable template tokens. A raw block, verbatim
            # section, or comment may contain include-looking literal text.
            from django.template.base import DebugLexer, TokenType
            from .._rust import raw_block_end_tag

            replacements = []
            in_comment = False
            raw_end = None
            for token in DebugLexer(content).tokenize():
                if token.token_type != TokenType.BLOCK:
                    continue
                bits = token.split_contents()
                if not bits:
                    continue
                if raw_end is not None:
                    if bits[0] == raw_end:
                        raw_end = None
                    continue
                if bits[0] == "comment":
                    in_comment = True
                elif bits[0] == "endcomment":
                    in_comment = False
                elif not in_comment:
                    raw_end = raw_block_end_tag(bits[0])
                    if bits[0] == "include":
                        start, end = token.position
                        match = _INCLUDE_RE.fullmatch(content[start:end])
                        if match:
                            replacements.append(((start, end), replacer(match)))
            for (start, end), replacement in reversed(replacements):
                content = content[:start] + replacement + content[end:]
            return content

        from ..template_libraries import library_render_scope

        with library_render_scope():
            return resolve(template_content)

    def _jit_serialize_queryset(
        self, queryset: Any, template_content: str, variable_name: str
    ) -> List[Any]:
        """
        Apply JIT auto-serialization to a Django QuerySet.

        Automatically:
        1. Extracts variable access patterns from template
        2. Generates optimized select_related/prefetch_related calls
        3. Compiles custom serializer function
        4. Caches serializer for reuse
        """
        try:
            recursive_relation = _recursive_queryset_relation(template_content, variable_name)
            variable_paths_map = _cached_extract_template_variables(template_content)
            if variable_paths_map is None:
                if recursive_relation:
                    raise ValueError("Cannot extract recursive partial model fields")
                return [normalize_django_value(obj) for obj in queryset]
            paths_for_var = variable_paths_map.get(variable_name, [])

            if not paths_for_var and recursive_relation:
                raise ValueError("Cannot extract recursive partial model fields")
            if not paths_for_var:
                logger.debug(
                    "[JIT] No paths found for '%s', using DjangoJSONEncoder fallback",
                    variable_name,
                )
                return [normalize_django_value(obj) for obj in queryset]

            model_class = queryset.model
            template_hash = _template_hash(template_content)
            model_hash = _get_model_hash(model_class)
            cache_key = (template_hash, variable_name, model_hash)

            if cache_key in _jit_serializer_cache:
                paths_for_var, optimization = _jit_serializer_cache[cache_key]
                logger.debug(
                    "[JIT] Cache HIT for '%s' - using cached paths: %s",
                    variable_name,
                    paths_for_var,
                )
            else:
                optimization = analyze_queryset_optimization(model_class, paths_for_var)

                logger.debug(
                    "[JIT] Cache MISS for '%s' (%s) - generating serializer for paths: %s",
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

            # A select_related added by the JIT can conflict with an explicit
            # only()/defer() on a recursive relation. Preserve the caller's
            # projection and let Django lazily load any traversed fields.
            deferred = queryset.query.deferred_loading[0]
            if optimization and not (recursive_relation and deferred):
                queryset = optimize_queryset(queryset, optimization)

            # Try Rust serializer first, fall back to Python codegen if incomplete
            from djust._rust import serialize_queryset

            items = list(queryset)
            initial_caches = {}
            if recursive_relation:
                initial_caches = {
                    id(item): item._meta.get_field(recursive_relation).is_cached(item)
                    for item in items
                }
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
                #
                # Since #2688 a path the serialization floor DENIES also lands here:
                # Rust now omits it (rather than shipping the value), so the key
                # count is short and this re-serializes through codegen. That is
                # wasted work, never a leak — codegen consults the same one gate
                # (`codegen.emittable_names`) and omits the same names. It only
                # costs anything for a template that names a denied field, which
                # is a bug in the template.

                func_name = f"serialize_{variable_name}_{template_hash}"
                code = generate_serializer_code(model_class.__name__, paths_for_var, func_name)
                serializer = compile_serializer(code, func_name)
                result = [serializer(obj) for obj in items]

            if recursive_relation:
                result = _serialize_recursive_models(
                    items,
                    result,
                    recursive_relation,
                    lambda obj: self._jit_serialize_model(obj, template_content, variable_name),
                    initial_caches,
                )

            from ..config import config

            if config.get("jit_debug"):
                logger.debug(
                    "[JIT] Serialized %s %s objects for '%s'",
                    len(result),
                    queryset.model.__name__,
                    variable_name,
                )
                if result:
                    logger.debug("[JIT DEBUG] First item keys: %s", list(result[0].keys()))
            return result

        except Exception as e:
            # Raises (2.0); it used to log and re-serialize every row through
            # normalize_django_value, which re-ran the query and shipped every
            # concrete field instead of the ones the template reads. The cause
            # is chained, not interpolated, so no value reaches the message.
            raise RuntimeError(f"djust JIT serialization failed for {variable_name!r}") from e

    def _jit_serialize_model_list(
        self, items: Any, template_content: str, variable_name: str
    ) -> List[Any]:
        """Serialize a ``list[Model]`` under the QuerySet contract (#2736).

        A plain list of model instances and a QuerySet of the same rows are
        the same data to a template, so they must serialize the same way.
        They did not: with no field path attributed to *variable_name* (a
        whole-object use — ``{% regroup rows by username %}``, ``{{ rows.0 }}``,
        a bridged tag walking the rows), the QuerySet path emitted the full
        field dict (``normalize_django_value``) while the list path called
        :meth:`_jit_serialize_model` per element, whose no-paths answer is the
        least-exposure IDENTITY map — right for a single ``{{ user }}``, wrong
        for a row every sink then reads a field off. Every consumer saw an
        identity-only dict; an UNSAVED row's identity is ``pk: None``, which is
        what made it look like a saved-vs-unsaved distinction.

        This is the one producer for the no-paths list case (#1646). The
        serialization floor (``_ALWAYS_EXCLUDED_FIELDS``) is applied inside
        ``normalize_django_value`` exactly as it is for a QuerySet, so a list
        is not a way past it.
        """
        recursive_relation = _recursive_queryset_relation(template_content, variable_name)
        variable_paths_map = _cached_extract_template_variables(template_content)
        paths_for_var = (variable_paths_map or {}).get(variable_name, [])
        if not paths_for_var:
            if recursive_relation:
                raise ValueError("Cannot extract recursive partial model fields")
            return [normalize_django_value(item) for item in items]
        initial_caches = {}
        if recursive_relation:
            initial_caches = {
                id(item): item._meta.get_field(recursive_relation).is_cached(item) for item in items
            }
        rows = [self._jit_serialize_model(item, template_content, variable_name) for item in items]
        if recursive_relation:
            return _serialize_recursive_models(
                items,
                rows,
                recursive_relation,
                lambda obj: self._jit_serialize_model(obj, template_content, variable_name),
                initial_caches,
            )
        return rows

    def _jit_serialize_model(
        self, obj: Any, template_content: str, variable_name: str, *, _recursive_child: bool = False
    ) -> Dict[str, Any]:
        """
        Apply JIT auto-serialization to a single Django Model instance.
        """
        try:
            relation = (
                None
                if _recursive_child
                else _recursive_queryset_relation(template_content, variable_name, single=True)
            )
            variable_paths_map = _cached_extract_template_variables(template_content)
            if variable_paths_map is None:
                if relation:
                    raise ValueError("Cannot extract recursive partial model fields")
                return cast(Dict[str, Any], normalize_django_value(obj))
            paths_for_var = variable_paths_map.get(variable_name, [])

            if not paths_for_var and relation:
                raise ValueError("Cannot extract recursive partial model fields")
            if not paths_for_var:
                # Finding #19: the whole-object / not-field-referenced case
                # (e.g. ``{{ user }}`` relying on __str__, or a public attr used
                # only in handlers). Emit ONLY the safe identity subset —
                # least-exposure, since no field of the object is referenced in
                # the template. The other fallbacks (extraction
                # None, exception) still call normalize_django_value because they
                # genuinely need the data; the denylist there keeps them safe.
                # One producer for the identity map (#2322). This site DID
                # stamp `__model__`, and still disagreed with the main path
                # about key ORDER (`pk, id` here, `id, pk` there) — which
                # survives `json.dumps` and msgpack, so it is a wire-shape
                # difference between two producers of "the same" map.
                return model_identity(obj)

            model_class = obj.__class__
            template_hash = _template_hash(template_content)
            model_hash = _get_model_hash(model_class)
            # Its own slot: ``_jit_serialize_queryset`` keeps (paths,
            # optimization) under the three-part key, and a name served as a
            # QuerySet once and a model later read that tuple as a serializer.
            cache_key = (template_hash, variable_name, model_hash, "model")

            if cache_key in _jit_serializer_cache:
                serializer, _ = _jit_serializer_cache[cache_key]
            else:
                func_name = f"serialize_{variable_name}_{template_hash}"
                code = generate_serializer_code(model_class.__name__, paths_for_var, func_name)
                serializer = compile_serializer(code, func_name)
                _jit_serializer_cache[cache_key] = (serializer, None)

            initial_caches = {}
            if relation:
                initial_caches[id(obj)] = obj._meta.get_field(relation).is_cached(obj)
            result = cast(Dict[str, Any], serializer(obj))
            if relation:
                _serialize_recursive_models([obj], [result], relation, serializer, initial_caches)
            return result

        except Exception as e:
            # Raises (2.0) instead of falling back to normalize_django_value.
            raise RuntimeError(f"djust JIT serialization failed for {variable_name!r}") from e
