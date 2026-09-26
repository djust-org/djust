"""Resolve what a decorator in a parsed module actually binds to (#3093).

The AST-based checks (``djust.S009``, ``djust_audit`` X002) used to judge a
decorator by its *local* name. That breaks both ways:

* ``from djust.decorators import permission_required as require_permission``
  is djust's per-handler gate under another name. It has to be aliased (or
  spelled ``decorators.permission_required``) whenever the view also sets the
  ``permission_required`` class attribute, because that attribute shadows the
  decorator inside the class body.
* ``from django.contrib.auth.decorators import permission_required`` shares
  the name but gates nothing on a djust event.

These helpers follow the module's import statements to a dotted target, then
compare that target by identity with the djust object.
"""

import ast
import importlib
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Sentinel: the dotted target was found but could not be imported, so the
# caller must fall back to its name-based heuristic.
UNRESOLVED = object()


def import_bindings(tree: ast.AST) -> dict[str, Optional[str]]:
    """Map each name bound by an absolute import in ``tree`` to its dotted target.

    ``import a.b`` binds ``a`` -> ``a``; ``import a.b as x`` binds ``x`` ->
    ``a.b``; ``from a import b as x`` binds ``x`` -> ``a.b``. Relative imports
    bind to ``None`` (their package is not known from the file alone). Later
    imports win, in source order.
    """
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    nodes.sort(key=lambda n: (n.lineno, n.col_offset))
    bindings: dict[str, Optional[str]] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    top = alias.name.split(".", 1)[0]
                    bindings[top] = top
        else:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                if node.level or not node.module:
                    bindings[local] = None
                else:
                    bindings[local] = "%s.%s" % (node.module, alias.name)
    return bindings


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


def decorator_target(deco: ast.expr, bindings: dict[str, Optional[str]]) -> Optional[str]:
    """Return the dotted import target a decorator refers to, or ``None``.

    ``None`` means the head name is not bound by an absolute import (a local
    definition, a star import, a relative import), so nothing can be said.
    """
    expr = deco.func if isinstance(deco, ast.Call) else deco
    parts = _dotted_parts(expr)
    if not parts:
        return None
    head = bindings.get(parts[0])
    if not head:
        return None
    return ".".join([head] + parts[1:])


def load_dotted(target: str) -> Any:
    """Import the object at ``target``; return ``UNRESOLVED`` if that fails."""
    parts = target.split(".")
    for i in range(len(parts) - 1, 0, -1):
        try:
            obj: Any = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        except Exception:  # a project module that raises on import
            logger.debug("djust: importing %s for a check failed; using the name match", target)
            return UNRESOLVED
        try:
            for attr in parts[i:]:
                obj = getattr(obj, attr)
        except AttributeError:
            return UNRESOLVED
        return obj
    return UNRESOLVED


def binds_to(deco: ast.expr, bindings: dict[str, Optional[str]], obj: Any, local_name: str) -> bool:
    """True if ``deco`` refers to ``obj``.

    Resolution order: follow the imports to a dotted target and compare the
    imported object with ``obj`` by identity. When the head name is not bound
    by an import, or its target cannot be imported, fall back to the old
    heuristic -- the decorator's final name equals ``local_name`` -- so a
    project the checker cannot resolve keeps the previous behaviour.
    """
    target = decorator_target(deco, bindings)
    if target is not None:
        found = load_dotted(target)
        if found is not UNRESOLVED:
            return found is obj
    expr = deco.func if isinstance(deco, ast.Call) else deco
    parts = _dotted_parts(expr)
    return bool(parts) and parts[-1] == local_name
