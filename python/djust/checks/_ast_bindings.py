"""Resolve what a decorator in a parsed module refers to (#3093).

The AST-based checks (``djust.S009``, ``djust_audit`` X002) used to judge a
decorator by its *local* name. That breaks both ways:

* ``from djust.decorators import permission_required as require_permission``
  is djust's per-handler gate under another name. It has to be aliased (or
  spelled ``decorators.permission_required``) whenever the view also sets the
  ``permission_required`` class attribute, because that attribute shadows the
  decorator inside the class body.
* ``from django.contrib.auth.decorators import permission_required`` shares
  the name but gates nothing on a djust event.

These helpers follow the module's top-level imports to a dotted target. They
reject a target only when it is positively not djust's gate (a known
non-djust gate, or a different djust object); anything else keeps the old
name match, so a project's own ``permission_required`` wrapper still counts.

**These helpers never import the code they scan.** A target is looked up only
through modules already in ``sys.modules``; if its module is not loaded, the
caller falls back to the name match. So neither ``manage.py check`` nor
``djust_audit --ast`` runs a project module's import-time code (#3158 review).
"""

import ast
import sys
from typing import Any, Optional, Union

# A name bound more than once at module level to different targets (an import
# fallback in try/except, a later redefinition): a tuple of the candidates,
# ``None`` standing for a non-import binding.
Candidates = tuple[Optional[str], ...]
Binding = Union[Optional[str], Candidates]

# Returned when a target cannot be looked up without importing anything.
UNRESOLVED = object()

# Dotted targets that ARE djust's per-handler gate (decided statically).
_DJUST_GATE_TARGETS = frozenset(
    {"djust.decorators.permission_required", "djust.permission_required"}
)
# Dotted targets that are positively NOT djust's gate, whatever they are named.
_KNOWN_NON_DJUST_GATES = frozenset({"django.contrib.auth.decorators.permission_required"})

_GATE_NAME = "permission_required"

# target -> (the loaded module the lookup walked from, the object found).
_lookup_cache: dict[str, tuple[Any, Any]] = {}


def _top_level_statements(tree: ast.AST) -> list[ast.stmt]:
    """Module-level statements, including those inside top-level if/try blocks.

    Imports inside functions and classes do not bind a module-level name, so
    they are ignored (#3158 review, M1).
    """
    out: list[ast.stmt] = []

    def visit(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            out.append(stmt)
            if isinstance(stmt, ast.If):
                visit(stmt.body)
                visit(stmt.orelse)
            elif isinstance(stmt, ast.Try) or type(stmt).__name__ == "TryStar":
                visit(stmt.body)  # type: ignore[attr-defined]
                for handler in stmt.handlers:  # type: ignore[attr-defined]
                    visit(handler.body)
                visit(stmt.orelse)  # type: ignore[attr-defined]
                visit(stmt.finalbody)  # type: ignore[attr-defined]

    visit(getattr(tree, "body", []))
    return out


def _stmt_bindings(stmt: ast.stmt) -> list[tuple[str, Optional[str]]]:
    if isinstance(stmt, ast.Import):
        pairs: list[tuple[str, Optional[str]]] = []
        for alias in stmt.names:
            if alias.asname:
                pairs.append((alias.asname, alias.name))
            else:
                top = alias.name.split(".", 1)[0]
                pairs.append((top, top))
        return pairs
    if isinstance(stmt, ast.ImportFrom):
        pairs = []
        for alias in stmt.names:
            if alias.name == "*":
                continue
            local = alias.asname or alias.name
            if stmt.level or not stmt.module:
                pairs.append((local, None))  # relative: package unknown here
            else:
                pairs.append((local, "%s.%s" % (stmt.module, alias.name)))
        return pairs
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [(stmt.name, None)]
    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        return [(t.id, None) for t in targets if isinstance(t, ast.Name)]
    return []


def import_bindings(tree: ast.AST) -> dict[str, Binding]:
    """Map each module-level name to the dotted target an import binds it to.

    ``import a.b`` binds ``a`` -> ``a``; ``import a.b as x`` binds ``x`` ->
    ``a.b``; ``from a import b as x`` binds ``x`` -> ``a.b``. A relative import
    or a local def/assignment binds ``None``. A name bound to more than one
    distinct target maps to the tuple of candidates.
    """
    seen: dict[str, list[Optional[str]]] = {}
    for stmt in _top_level_statements(tree):
        for name, target in _stmt_bindings(stmt):
            values = seen.setdefault(name, [])
            if target not in values:
                values.append(target)
    return {name: vals[0] if len(vals) == 1 else tuple(vals) for name, vals in seen.items()}


def _dotted_parts(expr: ast.expr) -> Optional[list[str]]:
    parts: list[str] = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    parts.append(expr.id)
    parts.reverse()
    return parts


def _targets(deco: ast.expr, bindings: dict[str, Binding]) -> Optional[Candidates]:
    """The candidate dotted targets ``deco`` refers to (``None`` = not an import)."""
    expr = deco.func if isinstance(deco, ast.Call) else deco
    parts = _dotted_parts(expr)
    if not parts or parts[0] not in bindings:
        return None
    head = bindings[parts[0]]
    heads: Candidates = head if isinstance(head, tuple) else (head,)
    return tuple(".".join([h] + parts[1:]) if h else None for h in heads)


def lookup_loaded(target: str) -> Any:
    """The object at ``target``, found WITHOUT importing anything.

    Walks from the longest prefix of ``target`` already in ``sys.modules``.
    Returns ``UNRESOLVED`` when no prefix is loaded or the attribute walk
    fails for any reason (a module ``__getattr__`` can run code). Results,
    including failures, are cached per target and revalidated against the
    module object, so S009 and X002 share one lookup.
    """
    parts = target.split(".")
    for i in range(len(parts) - 1, 0, -1):
        module = sys.modules.get(".".join(parts[:i]))
        if module is None:
            continue
        cached = _lookup_cache.get(target)
        if cached is not None and cached[0] is module:
            return cached[1]
        obj: Any = module
        try:
            for attr in parts[i:]:
                obj = getattr(obj, attr)
        except KeyboardInterrupt:
            raise
        except BaseException:  # noqa: B036 - never let scanned code stop a check
            obj = UNRESOLVED
        _lookup_cache[target] = (module, obj)
        return obj
    return UNRESOLVED


def _is_gate_target(target: Optional[str]) -> Optional[bool]:
    """True/False when ``target`` is decidably (not) djust's gate, else None."""
    if target is None:
        return None
    if target in _DJUST_GATE_TARGETS:
        return True
    if target in _KNOWN_NON_DJUST_GATES:
        return False
    found = lookup_loaded(target)
    if found is UNRESOLVED:
        return None
    from djust.decorators import permission_required

    if found is permission_required:
        return True
    auth_decorators = sys.modules.get("django.contrib.auth.decorators")
    if auth_decorators is not None and found is getattr(auth_decorators, _GATE_NAME, None):
        return False
    if target.startswith("djust."):
        return False  # positively a different djust object (e.g. debounce)
    return None  # a project callable: judge it by its name


def is_djust_permission_gate(deco: ast.expr, bindings: dict[str, Binding]) -> bool:
    """True if ``deco`` is djust's ``@permission_required`` per-handler gate.

    Rejects only a decorator whose target is positively not the gate; every
    undecidable case (an unbound or relative name, a module not yet loaded, a
    project's own callable, an ambiguous binding) keeps the name match, where
    the name is the local spelling or the imported attribute's name.
    """
    targets = _targets(deco, bindings)
    expr = deco.func if isinstance(deco, ast.Call) else deco
    parts = _dotted_parts(expr) or []
    local_match = bool(parts) and parts[-1] == _GATE_NAME
    if targets is None:
        return local_match
    if len(targets) == 1:
        decided = _is_gate_target(targets[0])
        if decided is not None:
            return decided
    # Undecidable or ambiguous: the old name match, over the local spelling
    # and every candidate target's final name.
    return local_match or all(t is not None and t.rsplit(".", 1)[-1] == _GATE_NAME for t in targets)
