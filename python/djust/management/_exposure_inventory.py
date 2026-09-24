"""Values-redacted inventory of the names legacy exposure infers (ADR-038 E6-2).

ADR-038 *Compatibility and rollout* requires "a values-redacted inventory of
inferred names and their destinations before migration". This module builds it
statically, for use by the ``djust_exposure_inventory`` management command:

- it never instantiates a view, evaluates a property or ``state()`` factory, or
  reads an instance;
- class attributes are read with ``vars()``, and only their *type name* is
  reported;
- ``self.<name>`` assignments are found by parsing method source with ``ast``
  (the approach ``tests/test_log_exposure_pin.py`` uses for log calls);
- nothing in the output is derived from a value except a builtin type name.

The destinations model the legacy policy as implemented, and
``tests/test_exposure_inventory_command.py`` checks them against the real
legacy APIs. It is an inventory to decide declarations from, not an allowlist:
ADR-038 says not to declare everything legacy happened to find.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any, Iterable, Optional

#: Destination names and what each means under the legacy policy.
DESTINATIONS: dict[str, str] = {
    "template_context": "legacy get_context_data(): rendered into HTML and the Rust render state",
    "render_cache": "the Rust view (with its render state) kept in the shared state backend",
    "session_state": "session key liveview_<path>: the context copy saved after HTTP GET/POST",
    "client_state": "get_state(): client/debug state and API assign diffs",
    "snapshot": "_capture_snapshot_state(): signed browser snapshot "
    "(enable_state_snapshot) and time-travel history",
    "private_session": "session key liveview_<path>__private: _get_private_state()",
}

_PUBLIC_INSTANCE = ("template_context", "render_cache", "session_state", "client_state", "snapshot")
_RENDER_ONLY = ("template_context", "render_cache", "session_state")
# Class-level values legacy context keeps only when JSON-serializable (#694).
_JSON_TYPES = (str, int, float, bool, type(None), list, dict, tuple)
_LITERAL_TYPES = {
    ast.List: "list",
    ast.ListComp: "list",
    ast.Dict: "dict",
    ast.DictComp: "dict",
    ast.Set: "set",
    ast.SetComp: "set",
    ast.Tuple: "tuple",
    ast.JoinedStr: "str",
}
_BUILTIN_TYPE_NAMES = frozenset(
    ("list", "dict", "set", "tuple", "str", "int", "float", "bool", "bytes")
)


def _framework_names() -> frozenset[str]:
    from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS, LiveView

    names = set(_FRAMEWORK_INTERNAL_ATTRS)
    for base in LiveView.__mro__:
        names.update(vars(base))
    names.update({"exposure_policy", "request"})
    return frozenset(names)


def _user_bases(view_class: type) -> list[type]:
    """Classes in the MRO that application code owns (LiveView's MRO excluded)."""
    from djust.live_view import LiveView

    framework = set(LiveView.__mro__)
    return [cls for cls in view_class.__mro__ if cls not in framework]


def _expr_type(node: Optional[ast.AST]) -> Optional[str]:
    """A builtin type name for a literal expression, never its value."""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return type(node.value).__name__
    for kind, name in _LITERAL_TYPES.items():
        if isinstance(node, kind):
            return name
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _BUILTIN_TYPE_NAMES
    ):
        return node.func.id
    return None


class _Assignments(ast.NodeVisitor):
    """Collect ``self.<name>`` stores and loads inside one method."""

    def __init__(self, self_name: str) -> None:
        self.self_name = self_name
        self.stored: dict[str, set[Optional[str]]] = {}
        self.loaded: set[str] = set()

    def _is_self_attr(self, node: ast.AST) -> Optional[str]:
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == self.self_name
        ):
            return node.attr
        return None

    def _store(self, target: ast.AST, value: Optional[ast.AST], annotation: Optional[str]) -> None:
        name = self._is_self_attr(target)
        if name is not None:
            self.stored.setdefault(name, set()).add(annotation or _expr_type(value))
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            values: list[Optional[ast.AST]] = [None] * len(target.elts)
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts):
                values = list(value.elts)
            for element, element_value in zip(target.elts, values):
                self._store(element, element_value, None)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._store(target, node.value, None)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._store(node.target, node.value, ast.unparse(node.annotation))
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        name = self._is_self_attr(node.target)
        if name is not None:
            self.stored.setdefault(name, set()).add(None)
            self.loaded.add(name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # setattr(self, "literal", value)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) == 3
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == self.self_name
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            self.stored.setdefault(node.args[1].value, set()).add(_expr_type(node.args[2]))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        name = self._is_self_attr(node)
        if name is not None and isinstance(node.ctx, ast.Load):
            self.loaded.add(name)
        self.generic_visit(node)


class _MethodScan:
    """Stores and loads of ``self`` attributes, split into mount() and elsewhere."""

    def __init__(self) -> None:
        self.mount_stored: dict[str, set[Optional[str]]] = {}
        self.other_stored: dict[str, set[Optional[str]]] = {}
        self.mount_loaded: set[str] = set()
        self.annotations: dict[str, str] = {}
        self.unavailable: list[str] = []


def _scan_class_source(cls: type, scan: _MethodScan) -> None:
    try:
        source = textwrap.dedent(inspect.getsource(cls))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError):
        scan.unavailable.append(f"{cls.__module__}.{cls.__qualname__}")
        return
    class_node = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls.__name__), None
    )
    if class_node is None:
        scan.unavailable.append(f"{cls.__module__}.{cls.__qualname__}")
        return
    for item in class_node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            scan.annotations.setdefault(item.target.id, ast.unparse(item.annotation))
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) or not item.args.args:
            continue
        if any(
            isinstance(d, ast.Name) and d.id in ("staticmethod", "classmethod")
            for d in item.decorator_list
        ):
            continue
        visitor = _Assignments(item.args.args[0].arg)
        for statement in item.body:
            visitor.visit(statement)
        target = scan.mount_stored if item.name == "mount" else scan.other_stored
        for name, types in visitor.stored.items():
            target.setdefault(name, set()).update(types)
        if item.name == "mount":
            scan.mount_loaded.update(visitor.loaded)


def _class_attrs(view_class: type) -> dict[str, Any]:
    """Raw class-level objects from application classes, nearest definition first."""
    attrs: dict[str, Any] = {}
    for cls in _user_bases(view_class):
        for name, obj in vars(cls).items():
            attrs.setdefault(name, obj)
    return attrs


def _join_types(types: Iterable[Optional[str]]) -> Optional[str]:
    return " | ".join(sorted({t for t in types if t})) or None


def _state_type(descriptor: Any) -> Optional[str]:
    """Type of a state() default without calling a factory or copying the default."""
    factory = getattr(descriptor, "default_factory", None)
    if factory is not None:
        return factory.__name__ if factory in (list, dict, set, tuple, str) else None
    default = getattr(descriptor, "default", None)
    return None if default is None else type(default).__name__


def _suggest(name: str, kind: str, type_name: Optional[str], declared: bool) -> str:
    annotation = f": {type_name}" if type_name and " " not in type_name else ""
    supply = f"supply {name}=self.{name} from get_context_data() if the template uses it"
    if kind == "state":
        return (
            f'keep {name} = state(...); add persist="server" if it must survive reconnect, '
            f"client=True only if the browser must read it; {supply}"
            if not declared
            else f"declared; review its persist/client grants; {supply}"
        )
    if kind == "instance":
        return (
            f'{name}{annotation} = state(persist="server")  # add client=True only if the '
            f"browser must read it; or drop persist for render-only data; {supply}"
        )
    if kind == "private":
        public = name.lstrip("_") or name
        return (
            f"explicit mode never persists underscore attributes; if it must survive "
            f'reconnect declare {public}{annotation} = state(persist="server"), '
            f"otherwise leave it instance-only"
        )
    return f"render-only: {supply}; no state declaration needed"


def inventory_view(view_class: type) -> dict[str, Any]:
    """Build the redacted inventory for one LiveView class. Never instantiates it."""
    from djust._state import StateProperty

    framework = _framework_names()
    scan = _MethodScan()
    for cls in reversed(_user_bases(view_class)):
        if cls is not object:
            _scan_class_source(cls, scan)
    attrs = _class_attrs(view_class)

    policy_obj = inspect.getattr_static(view_class, "exposure_policy", "legacy")
    policy = policy_obj if isinstance(policy_obj, str) else "unreadable"

    entries: dict[str, dict[str, Any]] = {}

    def add(name: str, **entry: Any) -> None:
        if name.startswith("__") or name in framework or name in entries:
            return
        entries[name] = {"name": name, **entry}

    stored = {**scan.other_stored}
    for name, types in scan.mount_stored.items():
        stored.setdefault(name, set()).update(types)

    # Declared state() fields: legacy reads them into context. A PUBLIC field's
    # backing slot (_state_<name>) reaches the session through the public
    # state, so the private session keeps it only when its value holds a
    # Django model (#2959, #1994). A ``_``-named field, or one in
    # ``static_assigns``, never reaches the public context: its slot is a
    # private attribute once touched in mount().
    static_assigns = attrs.get("static_assigns") or ()
    try:
        static_skip = set(static_assigns)
    except TypeError:
        static_skip = set()
    for name, obj in attrs.items():
        if issubclass(type(obj), StateProperty):
            touched = name in scan.mount_stored or name in scan.mount_loaded
            slot_private = name.startswith("_") or name in static_skip
            add(
                name,
                kind="state",
                type=scan.annotations.get(name) or _state_type(obj),
                destinations=list(_RENDER_ONLY)
                + (["private_session"] if slot_private and touched else []),
                conditional=[] if slot_private and touched else ["private_session"],
                storage_key=f"_state_{name}",
                declared={"persist": obj.exposure.persist, "client": obj.exposure.client},
                suggestion=_suggest(name, "state", None, obj.exposure != type(obj.exposure)()),
            )

    for name, types in sorted(stored.items()):
        if name in attrs and isinstance(attrs[name], StateProperty):
            continue
        type_name = scan.annotations.get(name) or _join_types(types)
        if name.startswith("_"):
            in_mount = name in scan.mount_stored
            add(
                name,
                kind="private",
                type=type_name,
                destinations=["private_session"] if in_mount else [],
                conditional=[] if in_mount else ["private_session"],
                suggestion=_suggest(name, "private", type_name, False),
            )
        else:
            add(
                name,
                kind="instance",
                type=type_name,
                destinations=list(_PUBLIC_INSTANCE),
                conditional=[],
                suggestion=_suggest(name, "instance", type_name, False),
            )

    for name, obj in sorted(attrs.items()):
        if name.startswith("_") or isinstance(obj, StateProperty):
            continue
        if isinstance(obj, (staticmethod, classmethod)) or inspect.isfunction(obj):
            continue
        if isinstance(obj, type) or inspect.ismodule(obj):
            continue
        if isinstance(obj, property) or (
            hasattr(obj, "__get__") and not callable(obj) and not isinstance(obj, _JSON_TYPES)
        ):
            add(
                name,
                kind="property",
                type=None,
                destinations=list(_RENDER_ONLY),
                conditional=[],
                suggestion=_suggest(name, "property", None, False),
            )
            continue
        if callable(obj):
            continue
        serializable = isinstance(obj, _JSON_TYPES)
        add(
            name,
            kind="class_value",
            type=type(obj).__name__,
            destinations=list(_RENDER_ONLY) if serializable else [],
            conditional=[] if serializable else ["template_context"],
            suggestion=_suggest(name, "class_value", None, False),
        )

    return {
        "view": f"{view_class.__module__}.{view_class.__qualname__}",
        "policy": policy,
        "source_unavailable": scan.unavailable,
        "names": sorted(entries.values(), key=lambda e: (e["name"].startswith("_"), e["name"])),
    }


def discover_views(app_label: Optional[str] = None) -> list[type]:
    """User LiveView classes: routed views plus imported subclasses (as check_liveviews)."""
    from djust.checks.components import _routed_liveview_classes
    from djust.live_view import LiveView
    from djust.management._introspect import app_label_for_class, is_user_class, walk_subclasses

    routed = set(_routed_liveview_classes())  # imports view modules first
    found = routed | set(walk_subclasses(LiveView))
    return sorted(
        (
            cls
            for cls in found
            if is_user_class(cls) and (not app_label or app_label_for_class(cls) == app_label)
        ),
        key=lambda c: (c.__module__, c.__qualname__),
    )


def format_text(views: list[dict[str, Any]]) -> str:
    """Human-readable report. Contains names, type names and destinations only."""
    lines = ["ADR-038 exposure inventory (legacy destinations; values redacted)", ""]
    for view in views:
        lines.append(f"{view['view']}  [policy: {view['policy']}]")
        for missing in view["source_unavailable"]:
            lines.append(f"  ! source unavailable for {missing}; assignments not scanned")
        if not view["names"]:
            lines.append("  (no inferred names)")
        for entry in view["names"]:
            destinations = ", ".join(entry["destinations"]) or "-"
            if entry["conditional"]:
                destinations += " (conditional: " + ", ".join(entry["conditional"]) + ")"
            lines.append(
                f"  {entry['name']}  {entry['type'] or '?'}  {entry['kind']}  -> {destinations}"
            )
            lines.append(f"      suggest: {entry['suggestion']}")
        lines.append("")
    lines.append("Destinations:")
    lines.extend(f"  {key}: {meaning}" for key, meaning in DESTINATIONS.items())
    return "\n".join(lines)


__all__ = ["DESTINATIONS", "discover_views", "format_text", "inventory_view"]
