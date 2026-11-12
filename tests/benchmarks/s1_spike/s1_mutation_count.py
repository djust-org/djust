"""S1 spike: how often do the repo's own example and test views mutate view
state in place (invisible to assignment tracking) versus reassign it?

AST scan, no imports. For every class, in every method, count writes through
``self.<public name>``:

* in-place: ``self.x.append(...)`` (and the other mutating container methods),
  ``self.x[k] = v``, ``self.x[k] += v``, ``del self.x[k]``, and the nested
  forms (``self.x[k][j] = v``, ``self.x[k].append(v)``, ``self.x.y = v``);
* assignment: ``self.x = v``, ``self.x += v``.

A class is a "state() class" when its body declares at least one
``name = state(...)``; a name is "declared" when it is one of those. A class
is "view-like" when a base name ends in ``View`` or ``Component`` or it is a
state() class.

Usage: python tests/benchmarks/s1_spike/s1_mutation_count.py DIR [DIR ...]
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

MUTATORS = {
    "append",
    "extend",
    "insert",
    "pop",
    "remove",
    "clear",
    "sort",
    "reverse",
    "update",
    "add",
    "discard",
    "setdefault",
    "popitem",
    "appendleft",
    "difference_update",
    "intersection_update",
    "symmetric_difference_update",
}


def root_self_attr(node: ast.AST):
    """``self.x`` at the bottom of an attribute/subscript chain -> ("x", depth)."""
    depth = 0
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            return node.attr, depth
        node = node.value
        depth += 1
    return None, 0


def base_name(b: ast.AST) -> str:
    if isinstance(b, ast.Name):
        return b.id
    if isinstance(b, ast.Attribute):
        return b.attr
    return ""


def scan(path: Path, out: Counter, per_class: list) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        out["unparsed_files"] += 1
        return
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        declared = set()
        for stmt in cls.body:
            targets = []
            value = None
            if isinstance(stmt, ast.Assign):
                targets, value = stmt.targets, stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets, value = [stmt.target], stmt.value
            if isinstance(value, ast.Call) and base_name(value.func) == "state":
                declared.update(t.id for t in targets if isinstance(t, ast.Name))
        viewlike = bool(declared) or any(
            base_name(b).endswith(("View", "Component")) for b in cls.bases
        )
        if not viewlike:
            continue
        inplace = Counter()
        assign = Counter()
        for fn in cls.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in MUTATORS:
                        name, _ = root_self_attr(node.func.value)
                        if name and not name.startswith("_"):
                            inplace[name] += 1
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target]
                elif isinstance(node, ast.Delete):
                    targets = node.targets
                for t in targets:
                    name, depth = root_self_attr(t)
                    if not name or name.startswith("_"):
                        continue
                    if depth == 0 and not isinstance(node, ast.Delete):
                        assign[name] += 1
                    elif depth > 0:
                        inplace[name] += 1
        out["viewlike_classes"] += 1
        if declared:
            out["state_classes"] += 1
        if inplace:
            out["classes_with_inplace"] += 1
        if declared and any(n in declared for n in inplace):
            out["state_classes_with_inplace_on_declared"] += 1
        d_in = sum(c for n, c in inplace.items() if n in declared)
        d_as = sum(c for n, c in assign.items() if n in declared)
        out["inplace_sites_declared"] += d_in
        out["inplace_sites_undeclared"] += sum(inplace.values()) - d_in
        out["assign_sites_declared"] += d_as
        out["assign_sites_undeclared"] += sum(assign.values()) - d_as
        if d_in:
            per_class.append((d_in, f"{path}:{cls.lineno} {cls.name} {dict(inplace)}"))


def main() -> None:
    for root in sys.argv[1:]:
        out: Counter = Counter()
        per_class: list = []
        files = sorted(Path(root).rglob("*.py"))
        for f in files:
            if ".venv" in f.parts or "node_modules" in f.parts:
                continue
            scan(f, out, per_class)
        print(f"== {root} ({len(files)} files)")
        for k in sorted(out):
            print(f"  {k}: {out[k]}")
        for n, line in sorted(per_class, reverse=True)[:8]:
            print(f"    {n} {line}")


if __name__ == "__main__":
    main()
