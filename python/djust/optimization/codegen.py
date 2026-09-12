"""
Serializer Code Generation

Generates optimized Python serializer functions for specific variable access patterns.
"""

import hashlib
import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, fine for typing
    from ..serialization import DjangoJSONEncoder

from django.db.models.fields.files import FieldFile

logger = logging.getLogger(__name__)

#: Memoized :func:`emittable_names` decisions (#2685 perf).
#:
#: Keyed on ``(names, denied, allowed, optout)`` — the COMPLETE argument set of
#: ``DjangoJSONEncoder._attr_is_serializable``, which is a pure function of
#: exactly those. The model class is deliberately NOT part of the key: two
#: models resolving to the same three sets provably reach the same decision, and
#: any config change (``DJUST_SENSITIVE_FIELDS``, ``djust_exclude_fields``,
#: ``djust_serializable_fields``, ``djust_serialize_sensitive_fields``) changes
#: one of the sets and therefore the key. There is no staleness mode to
#: invalidate — a changed policy simply misses.
#:
#: *names* is keyed as a ``frozenset``, not a tuple, because the answer depends
#: on the SET of names and nothing else — and because the Rust caller iterates a
#: Rust ``HashMap``, whose order differs per instance. Keyed as a tuple, twelve
#: identical ``serialize_queryset`` calls produced ELEVEN distinct keys: every
#: call missed and the cache walked to its cap. ``gated_names`` deliberately does
#: NOT sort to compensate — that would be a second mechanism covering the same
#: half, which no test could then tell apart from this one (#2233). This key is
#: the single mechanism, and it is correct for any caller's ordering.
_GATE_CACHE: Dict[
    Tuple[FrozenSet[str], FrozenSet[str], Optional[FrozenSet[str]], FrozenSet[str]],
    FrozenSet[str],
] = {}

#: Bound on :data:`_GATE_CACHE`. Real templates name a small, fixed set of paths
#: per model, so the live key count is tiny; the cap only stops an unbounded
#: walk if some caller synthesises path tuples at runtime. Past the cap the gate
#: keeps working, uncached.
_GATE_CACHE_MAX = 512

#: The chokepoint class, resolved on first use. Held here rather than imported
#: at module scope because ``serialization`` imports back into this package, and
#: rather than imported per call because the gate runs once per serialized
#: object and a repeated ``from ..serialization import`` is a measurable share
#: of that (it re-runs ``importlib``'s parent resolution every time).
_encoder_cls: 'Optional[type["DjangoJSONEncoder"]]' = None


def _chokepoint() -> 'type["DjangoJSONEncoder"]':
    """``DjangoJSONEncoder`` — the ONE per-attribute authority (#2614)."""
    global _encoder_cls
    if _encoder_cls is None:
        from ..serialization import DjangoJSONEncoder

        _encoder_cls = DjangoJSONEncoder
    return _encoder_cls


def emittable_names(obj: Any, names: Iterable[str]) -> FrozenSet[str]:
    """Which of *names* may the generated serializer emit for *obj*? (#2685)

    The JIT codegen path used to emit any attribute the template named —
    ``{{ m.password }}`` shipped the field, bypassing the serialization floor
    the eager and sidecar channels enforce (#2614). Every attribute the
    generated code reads, and every attribute the **Rust** queryset serializer
    reads (``crates/djust_live/src/lib.rs::serialize_object_with_paths`` calls
    this function), now goes through the same ONE chokepoint,
    ``DjangoJSONEncoder._attr_is_serializable``, with the same per-model
    denylist / allowlist / opt-out resolution the eager loops use — so no
    channel can drift (#1646).

    Set-at-a-time, once per OBJECT, for two reasons. It is the shape the Rust
    caller needs — one GIL crossing per object level instead of one per
    attribute, over every row of a QuerySet. And it makes the whole decision
    memoizable in one lookup (:data:`_GATE_CACHE`): the answer depends only on
    ``(names, denied, allowed, optout)``, so the per-name ``_attr_is_serializable``
    loop runs once per distinct policy+path-set and never again. The three
    per-model sets still resolve per object — they are three ``getattr`` calls
    over a memoized global — because a nested path (``lease.tenant.password``)
    crosses model classes and the decision cannot be inherited.

    A denied name is simply omitted from the result dict, which the template
    renders as ``string_if_invalid`` (empty) — the same outcome as the eager
    channel. Fail-closed: any error resolving the sets denies every name.
    """
    try:
        encoder = _chokepoint()
        denied = encoder._get_denied_fields(obj)
        allowed = encoder._get_allowlist_fields(obj)
        optout = encoder._get_sensitive_optout_fields(obj)
        wanted = frozenset(names)
        key = (wanted, denied, allowed, optout)
        hit = _GATE_CACHE.get(key)
        if hit is not None:
            return hit
        permitted = frozenset(
            n for n in wanted if encoder._attr_is_serializable(n, denied, allowed, optout)
        )
        if len(_GATE_CACHE) < _GATE_CACHE_MAX:
            _GATE_CACHE[key] = permitted
        return permitted
    except Exception:
        logger.debug("attribute gate failed for %s; refusing every name", type(obj).__name__)
        return frozenset()


def _empty_file(value: Any) -> bool:
    """An empty Django file has no readable URL, size, or path."""
    return isinstance(value, FieldFile) and not value


def _gate_line(indent: int, gate_var: str, obj_expr: str, names: List[str]) -> str:
    """The per-object prologue line the generated code runs before emitting."""
    tup = ", ".join(f"'{n}'" for n in names)
    return f"{'    ' * indent}{gate_var} = _djust_gate({obj_expr}, ({tup},))"


def generate_serializer_code(
    model_name: str, variable_paths: List[str], func_name: Optional[str] = None
) -> str:
    """
    Generate Python code for a custom serializer function.

    Args:
        model_name: Name of the model (e.g., "Lease")
        variable_paths: List of paths to serialize (e.g., ["property.name", "tenant.user.email"])
        func_name: Optional function name to use (if None, generates one automatically)

    Returns:
        Python source code as string

    Example:
        >>> code = generate_serializer_code("Lease", ["property.name", "tenant.user.email"])
        >>> print(code)
        def serialize_lease_a4f8b2(obj):
            result = {}
            # property.name
            if hasattr(obj, 'property') and obj.property is not None:
                if 'property' not in result:
                    result['property'] = {}
                result['property']['name'] = obj.property.name
            ...
            return result
    """
    # Generate unique function name based on paths if not provided
    if func_name is None:
        func_hash = hashlib.sha256("".join(sorted(variable_paths)).encode()).hexdigest()[:6]
        func_name = f"serialize_{model_name.lower()}_{func_hash}"

    # Build path tree to avoid redundant checks
    path_tree = _build_path_tree(variable_paths)

    lines = [
        f"def {func_name}(obj):",
        "    '''Auto-generated serializer'''",
        "    result = {}",
    ]

    # The serialization gate for the root object (#2685): the per-model sets
    # resolve ONCE here, and every emission below is a set-membership test.
    counter = [0]
    root_gate = "_ok_0"
    if path_tree:
        lines.append(_gate_line(1, root_gate, "obj", list(path_tree)))
    lines.append("")

    # Generate code for each path
    for root_attr, nested_paths in path_tree.items():
        _generate_nested_access(
            lines, [], nested_paths, "obj", "result", root_attr, gate_var=root_gate, counter=counter
        )

    lines.append("    return result")

    return "\n".join(lines)


def _build_path_tree(paths: List[str]) -> Dict:
    """
    Build tree structure from flat paths for efficient code generation.

    Args:
        paths: ["property.name", "property.address", "tenant.user.email", "leases.0.property.name"]

    Returns:
        {
            "property": {
                "name": {},
                "address": {}
            },
            "tenant": {
                "user": {
                    "email": {}
                }
            },
            "leases": {
                "__list_item__": {
                    "property": {
                        "name": {}
                    }
                }
            }
        }

    Note: Numeric indices (e.g., "leases.0.property") are converted to "__list_item__"
    to indicate the serializer should iterate over the list.
    """
    # Dict iteration methods used in Django templates (e.g., {% for k, v in dict.items %}).
    # These are template-level operations handled by the template engine, not data
    # attributes to serialize. Stripping them ensures the parent dict is serialized
    # whole, rather than storing a builtin_function_or_method reference.
    _DICT_METHODS = {"items", "keys", "values"}

    tree: Dict[str, Any] = {}

    for path in paths:
        parts = path.split(".")

        # Strip terminal dict iteration methods
        while parts and parts[-1] in _DICT_METHODS:
            parts.pop()
        if not parts:
            continue

        current = tree

        # Skip paths starting with numeric index (e.g., "0.url" from "posts.0.url")
        # These are list index accesses, not model attribute paths
        if parts[0].isdigit():
            continue

        i = 0
        while i < len(parts):
            part = parts[i]

            # Check if next part is a numeric index
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                # This is a list/queryset attribute (e.g., "leases" in "leases.0.property")
                # Create entry for this attribute with __list_item__ for nested access
                if part not in current:
                    current[part] = {}

                if "__list_item__" not in current[part]:
                    current[part]["__list_item__"] = {}

                # Skip the numeric index and continue with remaining parts
                current = current[part]["__list_item__"]
                i += 2  # Skip both attribute name and numeric index
                continue

            # Regular attribute access
            if part not in current:
                current[part] = {}
            current = current[part]
            i += 1

    return tree


def _generate_nested_access(
    lines: List[str],
    current_path: List[str],
    tree: Dict,
    obj_var: str,
    result_var: str,
    root_attr: Optional[str] = None,
    indent: int = 1,
    gate_var: str = "_ok_0",
    counter: Optional[List[int]] = None,
) -> None:
    """
    Recursively generate safe nested attribute access code.

    Args:
        lines: List of code lines to append to
        current_path: Current attribute path so far (e.g., ["property"])
        tree: Tree structure to process (e.g., {"name": {}, "address": {}})
        obj_var: Python variable name for object (e.g., "obj", "obj.property")
        result_var: Python variable name for result dict
        root_attr: Root attribute name (used on first call only)
        indent: Current indentation level
    """
    ind = "    " * indent

    # Handle root attribute on first call
    if root_attr is not None:
        current_path = [root_attr]
        obj_access = f"{obj_var}.{root_attr}"

        # Generate safety check for root. Every emitted attribute is gated by
        # the ONE serialization chokepoint (#2685 / #2614) via the per-object
        # ``_djust_gate`` prologue: a denied name is omitted from the dict —
        # the template then renders ``string_if_invalid`` (empty) — never
        # shipped.
        lines.append(
            f"{ind}if '{root_attr}' in {gate_var} and "
            f"not _djust_empty_file({obj_var}) and hasattr({obj_var}, '{root_attr}') and {obj_access} is not None:"
        )

        if tree:
            # If the value is a dict (e.g., JSONField), serialize it whole —
            # dict keys aren't Python attributes, so nested attribute access
            # would fail. Otherwise, create empty dict and recurse for models.
            lines.append(f"{ind}    if isinstance({obj_access}, dict):")
            lines.append(f"{ind}        {result_var}['{root_attr}'] = {obj_access}")
            lines.append(f"{ind}    else:")
            lines.append(f"{ind}        {result_var}['{root_attr}'] = {{}}")
            _generate_nested_access(
                lines,
                current_path,
                tree,
                obj_access,
                result_var,
                None,
                indent + 2,
                gate_var="",
                counter=counter,
            )
        else:
            # Leaf node - direct assignment
            if root_attr.startswith("get_"):
                # Method call. Guarded by Django's template-callable safety
                # attributes (ADR-024 Decision 2 — every auto-call site shares
                # one guard semantics): alters_data methods are never called,
                # do_not_call_in_templates callables are left un-called.
                lines.append(f"{ind}    _m = {obj_access}")
                lines.append(
                    f"{ind}    if not getattr(_m, 'alters_data', False) "
                    f"and not getattr(_m, 'do_not_call_in_templates', False):"
                )
                lines.append(f"{ind}        try:")
                lines.append(f"{ind}            {result_var}['{root_attr}'] = _m()")
                lines.append(f"{ind}        except Exception:")
                lines.append(
                    f"{ind}            _logger.debug('Method %s() failed during serialization', '{root_attr}')"
                )
            else:
                # Direct attribute
                lines.append(f"{ind}    {result_var}['{root_attr}'] = {obj_access}")
        return

    # Process nested tree
    if not tree:
        # Should not happen in normal flow
        return

    if counter is None:
        counter = [0]

    # A fresh object level (``gate_var=""``) resolves its own gate: nested
    # paths cross model classes, so the decision cannot be inherited (#2685).
    gateable = [k for k in tree if k != "__list_item__"]
    if not gate_var and gateable:
        counter[0] += 1
        gate_var = f"_ok_{counter[0]}"
        lines.append(_gate_line(indent, gate_var, obj_var, gateable))

    for attr_name, subtree in tree.items():
        # Special handling for list iteration marker
        if attr_name == "__list_item__":
            # This is a list - iterate and extract nested fields from each item
            # The subtree contains the fields to extract from each list item
            obj_access = obj_var  # We're already at the list level

            # Generate list iteration code
            list_var = f"item_{indent}"
            dict_path = _build_dict_path(result_var, current_path)
            lines.append(f"{ind}try:")
            lines.append(f"{ind}    {dict_path} = []")
            lines.append(f"{ind}    for {list_var} in {obj_var}:")
            lines.append(f"{ind}        item_result = {{}}")

            # One gate per list ITEM (a heterogeneous list is possible), shared
            # by every attribute extracted from it.
            item_names = [k for k in subtree if k != "__list_item__"]
            item_gate = ""
            if item_names:
                counter[0] += 1
                item_gate = f"_ok_{counter[0]}"
                lines.append(_gate_line(indent + 2, item_gate, list_var, item_names))

            # Generate code to extract fields from each list item
            for nested_attr, nested_subtree in subtree.items():
                _generate_nested_access(
                    lines,
                    [],  # Reset path for list item
                    {nested_attr: nested_subtree},
                    list_var,
                    "item_result",
                    None,
                    indent + 2,
                    gate_var=item_gate,
                    counter=counter,
                )

            lines.append(f"{ind}        {dict_path}.append(item_result)")
            lines.append(f"{ind}except (TypeError, AttributeError):")
            lines.append(f"{ind}    pass  # Not iterable or access failed")
            continue

        new_path = current_path + [attr_name]
        obj_access = f"{obj_var}.{attr_name}"

        # Generate safety check, gated by this object level's chokepoint set
        # (#2685) — a nested object may be a different model, so it resolved
        # its own gate above.
        lines.append(
            f"{ind}if '{attr_name}' in {gate_var} and "
            f"not _djust_empty_file({obj_var}) and hasattr({obj_var}, '{attr_name}') and {obj_access} is not None:"
        )

        if subtree:
            # Check if subtree contains list iteration marker
            if "__list_item__" in subtree:
                # This is a list/queryset attribute - handle specially
                dict_path = _build_dict_path(result_var, current_path)
                lines.append(f"{ind}    # List iteration for {attr_name}")
                _generate_nested_access(
                    lines,
                    new_path,
                    subtree,
                    obj_access,
                    result_var,
                    None,
                    indent + 1,
                    gate_var="",
                    counter=counter,
                )
            elif attr_name == "all":
                # .all() returns an iterable — iterate results
                # e.g. tags.all has subtree {name: {}, url: {}} meaning
                # template does {% for tag in post.tags.all %} {{ tag.name }}
                dict_path = _build_dict_path(result_var, current_path)
                list_var = f"item_{indent}"
                item_result_var = f"_item_result_{indent}"
                lines.append(f"{ind}    try:")
                lines.append(f"{ind}        {dict_path}['{attr_name}'] = []")
                lines.append(f"{ind}        for {list_var} in {obj_access}():")
                lines.append(f"{ind}            {item_result_var} = {{}}")

                all_names = [k for k in subtree if k != "__list_item__"]
                all_gate = ""
                if all_names:
                    counter[0] += 1
                    all_gate = f"_ok_{counter[0]}"
                    lines.append(_gate_line(indent + 3, all_gate, list_var, all_names))

                for nested_attr, nested_subtree in subtree.items():
                    _generate_nested_access(
                        lines,
                        [],
                        {nested_attr: nested_subtree},
                        list_var,
                        item_result_var,
                        None,
                        indent + 3,
                        gate_var=all_gate,
                        counter=counter,
                    )

                lines.append(
                    f"{ind}            {dict_path}['{attr_name}'].append({item_result_var})"
                )
                lines.append(f"{ind}    except (TypeError, AttributeError):")
                lines.append(f"{ind}        pass  # Method call or iteration failed")
            else:
                # Has nested attributes - create nested dict and recurse
                dict_path = _build_dict_path(result_var, current_path)
                lines.append(f"{ind}    if isinstance({obj_access}, dict):")
                lines.append(f"{ind}        {dict_path}['{attr_name}'] = {obj_access}")
                lines.append(f"{ind}    else:")
                lines.append(f"{ind}        {dict_path}['{attr_name}'] = {{}}")

                _generate_nested_access(
                    lines,
                    new_path,
                    subtree,
                    obj_access,
                    result_var,
                    None,
                    indent + 2,
                    gate_var="",
                    counter=counter,
                )
        else:
            # Leaf node - final assignment
            dict_path_full = _build_dict_path(result_var, new_path[:-1])

            if attr_name.startswith("get_") or attr_name in ("all", "count", "exists"):
                # Method call (includes Django manager/queryset methods).
                # Same ADR-024 guard semantics as the root-attr site above.
                lines.append(f"{ind}    _m = {obj_access}")
                lines.append(
                    f"{ind}    if not getattr(_m, 'alters_data', False) "
                    f"and not getattr(_m, 'do_not_call_in_templates', False):"
                )
                lines.append(f"{ind}        try:")
                lines.append(f"{ind}            {dict_path_full}['{attr_name}'] = _m()")
                lines.append(f"{ind}        except Exception:")
                lines.append(
                    f"{ind}            _logger.debug('Method %s() failed during serialization', '{attr_name}')"
                )
            else:
                # Direct attribute
                lines.append(f"{ind}    {dict_path_full}['{attr_name}'] = {obj_access}")


def _build_dict_path(result_var: str, path: List[str]) -> str:
    """
    Build dictionary access path string.

    Args:
        result_var: Base result variable name (e.g., "result")
        path: Attribute path (e.g., ["property", "owner"])

    Returns:
        "result['property']['owner']"
    """
    if not path:
        return result_var

    return result_var + "".join([f"['{p}']" for p in path])


def compile_serializer(code: str, func_name: str) -> Callable:
    """
    Compile serializer code to bytecode and return the function.

    Args:
        code: Python source code
        func_name: Name of the serializer function

    Returns:
        Compiled function object

    Example:
        >>> code = generate_serializer_code("Lease", ["property.name"])
        >>> func = compile_serializer(code, "serialize_lease_a4f8b2")
        >>> lease = Lease.objects.first()
        >>> serialized = func(lease)
        >>> print(serialized)
        {"property": {"name": "123 Main St"}}
    """
    namespace: Dict[str, Any] = {
        "_logger": logging.getLogger("djust.codegen.generated"),
        # The generated code's only authority for "may these attributes ship"
        # (#2685). Bound here, not looked up per call, so a generated
        # serializer can never run without it.
        "_djust_gate": emittable_names,
        "_djust_empty_file": _empty_file,
    }

    try:
        # Compile to bytecode
        code_obj = compile(code, f"<generated:{func_name}>", "exec")

        # Execute to define function in namespace
        exec(code_obj, namespace)

        # Return the function
        compiled_func: Callable = namespace[func_name]
        return compiled_func

    except SyntaxError as e:
        # Include generated code in error for debugging
        lines = code.split("\n")
        error_context = "\n".join([f"{i + 1:3}: {line}" for i, line in enumerate(lines)])
        raise SyntaxError(f"Failed to compile generated serializer:\n{error_context}") from e


def get_serializer_source(serializer_func: Callable) -> str:
    """
    Get source code of a compiled serializer function.

    Useful for debugging.

    Args:
        serializer_func: Compiled serializer function

    Returns:
        Source code as string
    """
    try:
        return inspect.getsource(serializer_func)
    except OSError:
        # Function was compiled from string, not a file
        # Return the docstring which contains info
        return f"# Auto-generated serializer\n# {serializer_func.__name__}\n"
