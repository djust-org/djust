"""
Serializer Code Generation

Generates optimized Python serializer functions for specific variable access patterns.
"""

import hashlib
import inspect
import itertools
import keyword
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, fine for typing
    from ..serialization import DjangoJSONEncoder

from django.db.models.fields.files import FieldFile

logger = logging.getLogger(__name__)

#: Memoized :func:`emittable_names` decisions (#2685 perf).
#:
#: Keyed on ``(names, denied, allowed, optout, model class, configured types)``:
#: every input to the verdict. The name sets are the per-model denylist /
#: allowlist / opt-out resolution of ``DjangoJSONEncoder._attr_is_serializable``;
#: the model class and the configured sensitive type names are the inputs to the
#: column-TYPE floor (``DjangoJSONEncoder._attr_is_readable``), which depends on
#: what a column IS, not what it is called. Two models whose name sets are equal
#: can still differ in a column's type, and ``LIVEVIEW_CONFIG['sensitive_field_types']``
#: can change under a running process, so neither may be left out of the key. Any
#: config change (``DJUST_SENSITIVE_FIELDS``, ``djust_exclude_fields``,
#: ``djust_serializable_fields``, ``djust_serialize_sensitive_fields``, the
#: configured types) changes one of the parts and therefore the key. There is no
#: staleness mode to invalidate — a changed policy simply misses.
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
    Tuple[
        FrozenSet[str],
        FrozenSet[str],
        Optional[FrozenSet[str]],
        FrozenSet[str],
        Optional[type],
        FrozenSet[str],
    ],
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
    ``DjangoJSONEncoder._attr_is_readable`` (the name rules of
    ``_attr_is_serializable`` plus the column-TYPE floor, #1987), with the same
    per-model denylist / allowlist / opt-out resolution the eager loops use — so
    no channel can drift (#1646).

    Set-at-a-time, once per OBJECT, for two reasons. It is the shape the Rust
    caller needs — one GIL crossing per object level instead of one per
    attribute, over every row of a QuerySet. And it makes the whole decision
    memoizable in one lookup (:data:`_GATE_CACHE`): the answer depends only on
    the names, the three name sets, the model class and the configured sensitive
    types, so the per-name loop runs once per distinct policy+path-set and never
    again. The three
    per-model sets still resolve per object — they are three ``getattr`` calls
    over a memoized global — because a nested path (``lease.tenant.password``)
    crosses model classes and the decision cannot be inherited.

    A denied name is simply omitted from the result dict, which the template
    renders as ``string_if_invalid`` (empty) — the same outcome as the eager
    channel. Fail-closed: any error resolving the sets denies every name.
    """
    try:
        encoder = _chokepoint()
        # The model class, seen through a lazy wrapper: the per-model sets and the
        # column types both belong to it, and ``type(obj)`` of a ``SimpleLazyObject``
        # is the wrapper. The readers below look only at the class, so a bare
        # instance of it stands in for the wrapped one (as ``_floor_for_model`` does).
        model_class = encoder._model_class_behind(obj)
        subject = obj
        if model_class is not None and type(obj) is not model_class:
            subject = object.__new__(model_class)
        denied = encoder._get_denied_fields(subject)
        allowed = encoder._get_allowlist_fields(subject)
        optout = encoder._get_sensitive_optout_fields(subject)
        wanted = frozenset(names)
        key = (wanted, denied, allowed, optout, model_class, encoder._configured_sensitive_types())
        hit = _GATE_CACHE.get(key)
        if hit is not None:
            return hit
        permitted = frozenset(
            n for n in wanted if encoder._attr_is_readable(n, model_class, denied, allowed, optout)
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


#: Methods the generated code calls at a leaf (``_generate_nested_access``).
_CALLED_LEAF_NAMES = ("all", "count", "exists")


def _is_sequence(value: Any) -> bool:
    """A list-like value whose items the template iterates."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return True
    from django.db.models import QuerySet

    return isinstance(value, QuerySet)


#: The path-tree key that records a node's own name is ALSO read whole (#3788).
#: Only an ``all`` node carries it. It is never an attribute: the helpers that
#: list a node's names skip it, as they skip ``__list_item__``.
ALL_WHOLE_KEY = "__djust_whole__"

#: The QuerySet methods a template can chain after ``.all``.
ALL_CHAIN_METHODS = ("count", "exists", "first", "last")

_DEFAULT_ALL_ROW_CAP = 100_000

#: A cap this large is no cap; see :func:`all_row_cap`.
_NO_CAP_FROM = 2**62


def all_row_cap() -> Optional[int]:
    """``LIVEVIEW_CONFIG['queryset_all_row_cap']`` as the serializers' cap (#3788).

    ``0`` is no cap, and so is any value of 2**62 or more (no relation has that
    many rows, and the Rust side counts in a signed 64-bit slice bound: 10**30
    used to overflow it and blank every relation). ``None`` is NOT "no cap":
    ``config.get`` treats an unset or ``None`` key as absent, so it is the
    default. A value that is not a non-negative integer is a configuration
    mistake, and the safe answer to a mistake is the default, not "unbounded".
    """
    from ..config import config

    cap = config.get("queryset_all_row_cap", _DEFAULT_ALL_ROW_CAP)
    if isinstance(cap, int) and not isinstance(cap, bool) and cap >= _NO_CAP_FROM:
        return None
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        logger.warning(
            "Invalid LIVEVIEW_CONFIG['queryset_all_row_cap']=%r (must be a non-negative integer)."
            " Using %s.",
            cap,
            _DEFAULT_ALL_ROW_CAP,
        )
        return _DEFAULT_ALL_ROW_CAP
    return cap or None


def capped_all_rows(rows: Any) -> Any:
    """The rows a called ``all`` yields, at most ``queryset_all_row_cap`` (#3788).

    A related manager's ``all()`` is an UNEVALUATED QuerySet and iterating it
    loads the whole relation. With a cap the read is ``rows[:cap + 1]``: a SQL
    ``LIMIT`` on an unevaluated QuerySet, a slice of the result cache on a
    prefetched one. One row past the cap is read only to learn the relation
    was longer; it is dropped and the truncation is logged. The Rust twin is
    ``capped_rows`` in ``crates/djust_live/src/lib.rs``.
    """
    cap = all_row_cap()
    if cap is None:
        return rows
    try:
        window = rows[: cap + 1]
    except TypeError:
        window = rows
    taken = list(itertools.islice(window, cap + 1))
    if len(taken) > cap:
        logger.warning(
            "A related manager's .all read by the queryset serializer has more than %s rows; "
            "only the first %s are sent. Raise LIVEVIEW_CONFIG['queryset_all_row_cap'] "
            "(0 disables it), read .count / .first / .exists, or paginate.",
            cap,
            cap,
        )
        del taken[cap:]
    return taken


def tree_names(tree: Dict[str, Any]) -> List[str]:
    """The attribute names a path tree node asks for (no markers)."""
    return [name for name in tree if name != "__list_item__" and name != ALL_WHOLE_KEY]


def all_methods_value(queryset: Any, subtree: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``{"count": 2, "exists": True, "first": {...}}`` for an ``all`` the template
    reads ONLY through QuerySet methods -- ``u.groups.all.count``,
    ``.all.first.name``, ``.all.exists`` -- or ``None`` when it reads rows (#3788).

    The tree cannot say whether ``count`` is ``qs.count()`` or the ``count``
    FIELD of a row in ``{% for i in o.items.all %}{{ i.count }}``: both extract
    to ``items.all.count``. It is a method read only when nothing else about
    the node asks for rows -- the relation is not read whole
    (:data:`ALL_WHOLE_KEY`), every child is a QuerySet method name, and the row
    model has no attribute of that name. Each method runs on the QuerySet, so
    Django's own SQL applies: ``COUNT(*)`` (or a prefetched cache's length),
    ``EXISTS``, ``LIMIT 1``. Every method goes through the one call guard that
    ``all`` itself does, and a ``first`` / ``last`` row goes through the
    serialization gate. The Rust twin is ``serialize_all_methods`` in
    ``crates/djust_live/src/lib.rs``.
    """
    from django.db.models import QuerySet

    names = tree_names(subtree)
    if (
        not names
        or ALL_WHOLE_KEY in subtree
        or "__list_item__" in subtree
        or any(name not in ALL_CHAIN_METHODS for name in names)
        or not isinstance(queryset, QuerySet)
    ):
        return None
    model = getattr(queryset, "model", None)
    if model is None or any(hasattr(model, name) for name in names):
        return None
    out: Dict[str, Any] = {}
    for name in names:
        method = getattr(queryset, name, None)
        if method is None or getattr(method, "alters_data", False):
            continue
        if getattr(method, "do_not_call_in_templates", False):
            continue
        try:
            value = method()
        except (TypeError, AttributeError):
            continue
        if value is not None and name in ("first", "last") and subtree[name]:
            row = value
            value = take_by_tree(row, subtree[name])
            # The row is also read whole (``{{ u.groups.all.first }}`` beside
            # ``.first.name``): a dict prints as its repr, so it carries the
            # ``__str__`` a serialized model carries (the Rust twin does the same).
            if ALL_WHOLE_KEY in subtree[name] and isinstance(value, dict):
                value["__str__"] = str(row)
        out[name] = value
    return out


def _related_model(model: Any, name: str) -> Any:
    """The model a relation named ``name`` on ``model`` yields, or ``None``."""
    if model is None:
        return None
    try:
        related = model._meta.get_field(name).related_model
    except Exception:  # FieldDoesNotExist, or a model without _meta
        related = None
    if related is None:
        descriptor = getattr(model, name, None)
        for chain in (("rel", "model"), ("related", "related_model"), ("field", "related_model")):
            candidate = descriptor
            for step in chain:
                candidate = getattr(candidate, step, None)
            if isinstance(candidate, type):
                related = candidate
                break
    return related if isinstance(related, type) else None


def _tree_mixes_all_methods_with_rows(tree: Dict[str, Any], model: Any) -> bool:
    for name, subtree in tree.items():
        if name in ("__list_item__", ALL_WHOLE_KEY) or not isinstance(subtree, dict):
            continue
        row_model = _related_model(model, name)
        if name == "all":
            names = tree_names(subtree)
            if any(n in ALL_CHAIN_METHODS for n in names) and (
                ALL_WHOLE_KEY in subtree
                or "__list_item__" in subtree
                or any(n not in ALL_CHAIN_METHODS for n in names)
                or (model is not None and any(hasattr(model, n) for n in names))
            ):
                return True
            # ``all`` is a manager's method, not a relation: its rows are the
            # PARENT relation's model, which is the ``model`` we were handed.
            if _tree_mixes_all_methods_with_rows(subtree, model):
                return True
            continue
        if subtree and _tree_mixes_all_methods_with_rows(subtree, row_model):
            return True
    return False


def all_methods_need_rows(model: Any, paths: List[str]) -> bool:
    """Whether a template reads some ``all`` BOTH as rows and through a QuerySet
    method (``{% for g in u.groups.all %}`` beside ``{{ u.groups.all.count }}``),
    so the serialized rows cannot answer the method (#3801).

    ``model`` is the row model the ``paths`` start from. The decision is the one
    :func:`all_methods_value` and its Rust twin make, read from the other side:
    where they would ship a method dict the methods are answered; where they
    ship rows (the relation is read whole, a row field is read, or the row model
    has an attribute of that name) a method read has nothing to read. Those are
    the names the view then answers from the raw rows (the render sidecar).
    """
    return _tree_mixes_all_methods_with_rows(_build_path_tree(list(paths)), model)


def take_by_tree(value: Any, tree: Dict[str, Any]) -> Any:
    """``value`` reduced to the path ``tree`` (``_build_path_tree``'s shape).

    The generated code's answer for a value that is a list (or tuple, set,
    QuerySet) where the template reads INSIDE its items:
    ``{% for tool in agent.tools %}{{ tool.name }}``, with ``tools`` a property
    returning a list of dicts. Attribute access on the list itself found
    nothing, so ``tools`` serialized as ``{}`` (Point Guard N-32). Each item is
    taken by the same tree, with the same rules the generated code applies to
    an object: a dict is kept whole, a ``None`` attribute is omitted, the
    serialization gate (``emittable_names``) decides which names ship, and
    ``get_*`` / ``all`` / ``count`` / ``exists`` are called unless
    ``alters_data`` or ``do_not_call_in_templates`` says not to.
    """
    if not tree or value is None:
        return value
    if isinstance(value, dict) or isinstance(value, (str, bytes, int, float, bool)):
        return value
    if _is_sequence(value):
        return [take_by_tree(item, tree) for item in value]
    if _empty_file(value):
        return {}
    names = tree_names(tree)
    permitted = emittable_names(value, names)
    taken: Dict[str, Any] = {}
    for name in names:
        if name not in permitted:
            continue
        try:
            found = getattr(value, name)
        except Exception:  # noqa: BLE001 — a template lookup that fails is empty
            continue
        if found is None:
            continue
        if callable(found) and (name.startswith("get_") or name in _CALLED_LEAF_NAMES):
            if getattr(found, "alters_data", False) or getattr(
                found, "do_not_call_in_templates", False
            ):
                continue
            try:
                found = found()
            except Exception:  # noqa: BLE001 — as the generated code does
                logger.debug("Method %s() failed during serialization", name)
                continue
            if name == "all":
                chain = all_methods_value(found, tree[name])
                if chain is not None:
                    taken[name] = chain
                    continue
                found = capped_all_rows(found)
        taken[name] = take_by_tree(found, tree[name])
    return taken


def _gate_line(indent: int, gate_var: str, obj_expr: str, names: List[str]) -> str:
    """The per-object prologue line the generated code runs before emitting."""
    tup = ", ".join(f"'{n}'" for n in names)
    return f"{'    ' * indent}{gate_var} = _djust_gate({obj_expr}, ({tup},))"


def _is_plain_identifier(name: str) -> bool:
    """A Python identifier that is not a keyword."""
    return name.isidentifier() and not keyword.iskeyword(name)


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
    # Path segments and names are pasted into generated source, so accept only
    # Python identifiers (and digit list indices). The template parser already
    # rejects anything else; this keeps codegen safe on its own. Callers catch
    # the ValueError and fall back to regular serialization.
    for path in variable_paths:
        for segment in path.split("."):
            if not (segment.isdigit() or _is_plain_identifier(segment)):
                raise ValueError(f"unsupported serializer path segment: {segment!r} in {path!r}")
    if not _is_plain_identifier(model_name):
        raise ValueError(f"unsupported model name: {model_name!r}")
    if func_name is not None and not _is_plain_identifier(func_name):
        raise ValueError(f"unsupported serializer function name: {func_name!r}")

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

    _mark_whole_all_nodes(tree, paths)
    return tree


def _mark_whole_all_nodes(tree: Dict[str, Any], paths: List[str]) -> None:
    """Record, on every ``all`` node the template reads whole AND walks into,
    that it does (#3788).

    ``{% for g in u.groups.all %}{{ g.name }}`` extracts ``groups.all`` and
    ``groups.all.name``; ``{{ u.groups.all.count }}`` extracts
    ``groups.all.count`` alone. Both build an ``all`` node with one child, so
    the tree alone cannot tell a loop that also prints ``count`` from a
    template that prints only the count. The mark is ``ALL_WHOLE_KEY``.
    """
    split = [tuple(path.split(".")) for path in paths]
    known = set(split)
    for parts in split:
        for end, part in enumerate(parts, start=1):
            if (
                part not in ("all", "first", "last")
                or end >= len(parts)
                or parts[end].isdigit()
                or parts[:end] not in known
            ):
                continue
            node: Any = tree
            at = 0
            prefix = parts[:end]
            while at < len(prefix) and isinstance(node, dict):
                node = node.get(prefix[at])
                if isinstance(node, dict) and at + 1 < len(prefix) and prefix[at + 1].isdigit():
                    node = node.get("__list_item__")
                    at += 1
                at += 1
            if isinstance(node, dict):
                node[ALL_WHOLE_KEY] = {}


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
            # A list (a property returning a list of dicts or objects): take
            # each item by the subtree (N-32).
            lines.append(f"{ind}    elif _djust_is_sequence({obj_access}):")
            lines.append(
                f"{ind}        {result_var}['{root_attr}'] = _djust_take({obj_access}, {tree!r})"
            )
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
    gateable = tree_names(tree)
    if not gate_var and gateable:
        counter[0] += 1
        gate_var = f"_ok_{counter[0]}"
        lines.append(_gate_line(indent, gate_var, obj_var, gateable))

    for attr_name, subtree in tree.items():
        if attr_name == ALL_WHOLE_KEY:
            continue
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
            item_names = tree_names(subtree)
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
                #
                # Called under the same guard as every other auto-called
                # method (#3772), and read as QuerySet METHODS when the
                # template asks the relation, not its rows -- ``u.groups.all
                # .count`` (#3788). The Rust twin is
                # ``serialize_object_with_paths``'s ``all`` arm.
                dict_path = _build_dict_path(result_var, current_path)
                list_var = f"item_{indent}"
                item_result_var = f"_item_result_{indent}"
                lines.append(
                    f"{ind}    if not getattr({obj_access}, 'alters_data', False) "
                    f"and not getattr({obj_access}, 'do_not_call_in_templates', False):"
                )
                lines.append(f"{ind}        try:")
                lines.append(f"{ind}            _all_qs = {obj_access}()")
                lines.append(
                    f"{ind}            _all_chain = _djust_all_methods(_all_qs, {subtree!r})"
                )
                lines.append(f"{ind}            if _all_chain is not None:")
                lines.append(f"{ind}                {dict_path}['{attr_name}'] = _all_chain")
                lines.append(f"{ind}            else:")
                lines.append(f"{ind}                {dict_path}['{attr_name}'] = []")
                lines.append(f"{ind}                for {list_var} in _djust_all_rows(_all_qs):")
                lines.append(f"{ind}                    {item_result_var} = {{}}")

                all_names = tree_names(subtree)
                all_gate = ""
                if all_names:
                    counter[0] += 1
                    all_gate = f"_ok_{counter[0]}"
                    lines.append(_gate_line(indent + 5, all_gate, list_var, all_names))

                for nested_attr, nested_subtree in subtree.items():
                    if nested_attr == ALL_WHOLE_KEY:
                        continue
                    _generate_nested_access(
                        lines,
                        [],
                        {nested_attr: nested_subtree},
                        list_var,
                        item_result_var,
                        None,
                        indent + 5,
                        gate_var=all_gate,
                        counter=counter,
                    )

                lines.append(
                    f"{ind}                    {dict_path}['{attr_name}'].append({item_result_var})"
                )
                lines.append(f"{ind}        except (TypeError, AttributeError):")
                lines.append(f"{ind}            pass  # Method call or iteration failed")
            else:
                # Has nested attributes - create nested dict and recurse
                dict_path = _build_dict_path(result_var, current_path)
                lines.append(f"{ind}    if isinstance({obj_access}, dict):")
                lines.append(f"{ind}        {dict_path}['{attr_name}'] = {obj_access}")
                lines.append(f"{ind}    elif _djust_is_sequence({obj_access}):")
                lines.append(
                    f"{ind}        {dict_path}['{attr_name}'] = _djust_take({obj_access}, {subtree!r})"
                )
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
                #
                # A leaf that is NOT callable is a plain attribute: a row's
                # integer ``count`` field shares its name with the QuerySet
                # method and was dropped by the call's TypeError (#3801). The
                # Rust serializer ships it; only a callable is ever called.
                lines.append(f"{ind}    _m = {obj_access}")
                lines.append(f"{ind}    if not callable(_m):")
                lines.append(f"{ind}        {dict_path_full}['{attr_name}'] = _m")
                lines.append(
                    f"{ind}    elif not getattr(_m, 'alters_data', False) "
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
        "_djust_is_sequence": _is_sequence,
        "_djust_take": take_by_tree,
        "_djust_all_methods": all_methods_value,
        "_djust_all_rows": capped_all_rows,
    }

    try:
        # Compile to bytecode
        code_obj = compile(code, f"<generated:{func_name}>", "exec")

        # Execute to define function in namespace
        # The source was built by generate_serializer_code from validated
        # identifiers only, so executing it is intended.
        exec(code_obj, namespace)  # nosec B102

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
