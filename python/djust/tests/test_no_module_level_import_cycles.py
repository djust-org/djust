"""No import cycle in ``djust`` may be made of module-level imports alone.

CodeQL's ``py/cyclic-import`` builds its graph from every ``import`` statement,
including the ones deferred into a function body. djust relies on deferred
imports to keep the ADR-038 core (``_exposure``, ``_state``, ``live_view``,
``components``, ``time_travel``) and a few smaller module pairs cooperating, so
that query reported about 50 cycles that cannot fail at import time. It is
excluded in ``.github/codeql/codeql-config.yml``, and this test is the guard
that replaces it. It builds the graph from module-level imports only, the ones
that execute while a module is being imported, and fails on any cycle there.
Such a cycle is the kind that raises a partially-initialised-module
``ImportError`` depending on which module is imported first.

Not edges:

- imports inside a function or method body (they run at call time);
- imports under ``if TYPE_CHECKING:`` (they never run);
- an ancestor package of the importing module, which is already initialising
  when the module runs.

Imports in class bodies, ``try``/``except`` and plain ``if`` blocks do count,
because they run at import time.

The ``TestScanner`` cases pin the scanner itself on a synthetic tree, so the
guard cannot pass because it saw nothing.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Dict, List, Set

PYTHON_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _module_level_imports(tree: ast.Module) -> List[ast.stmt]:
    found: List[ast.stmt] = []

    def walk(statements: List[ast.stmt]) -> None:
        for stmt in statements:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(stmt, ast.If) and _is_type_checking(stmt.test):
                walk(stmt.orelse)
                continue
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                found.append(stmt)
                continue
            if isinstance(stmt, ast.Try):
                walk(stmt.body)
                for handler in stmt.handlers:
                    walk(handler.body)
                walk(stmt.orelse)
                walk(stmt.finalbody)
                continue
            for field in ("body", "orelse"):
                block = getattr(stmt, field, None)
                if isinstance(block, list):
                    walk([s for s in block if isinstance(s, ast.stmt)])

    walk(tree.body)
    return found


def import_graph(root: pathlib.Path, package: str) -> Dict[str, Set[str]]:
    """Module-level import edges between the modules of ``package`` under ``root``."""
    modules: Dict[str, tuple] = {}
    for path in root.joinpath(*package.split(".")).rglob("*.py"):
        parts = list(path.relative_to(root).with_suffix("").parts)
        if "tests" in parts:
            continue
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        modules[".".join(parts)] = (path, is_package)

    edges: Dict[str, Set[str]] = {name: set() for name in modules}
    for name, (path, is_package) in modules.items():
        current_package = name if is_package else name.rpartition(".")[0]
        for node in _module_level_imports(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                if node.level:
                    base = current_package.split(".")
                    base = base[: len(base) - (node.level - 1)]
                    target = ".".join(base + ([node.module] if node.module else []))
                else:
                    target = node.module or ""
                # ``from pkg import sub`` may name a submodule.
                targets = [target] + [f"{target}.{alias.name}" for alias in node.names]
            for target in targets:
                parts = target.split(".")
                for i in range(1, len(parts) + 1):
                    candidate = ".".join(parts[:i])
                    if (
                        candidate in modules
                        and candidate != name
                        and not name.startswith(candidate + ".")
                    ):
                        edges[name].add(candidate)
    return edges


def cycles(edges: Dict[str, Set[str]]) -> List[List[str]]:
    """Strongly connected components with more than one module (Tarjan)."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    stack: List[str] = []
    on_stack: Set[str] = set()
    found: List[List[str]] = []
    counter = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for succ in sorted(edges[node]):
            if succ not in index:
                visit(succ)
                low[node] = min(low[node], low[succ])
            elif succ in on_stack:
                low[node] = min(low[node], index[succ])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                found.append(sorted(component))

    for node in sorted(edges):
        if node not in index:
            visit(node)
    return found


def test_djust_has_no_module_level_import_cycle():
    graph = import_graph(PYTHON_ROOT, "djust")
    assert len(graph) > 100, "the scanner found almost no modules; the guard would be vacuous"
    found = cycles(graph)
    assert not found, (
        "Module-level import cycle(s) in djust. Defer one edge into the function "
        "that needs it, or move the shared code into a leaf module:\n"
        + "\n".join(" -> ".join(c) for c in found)
    )


class TestScanner:
    def _tree(self, tmp_path, files):
        for rel, source in files.items():
            path = tmp_path.joinpath(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        return import_graph(tmp_path, "pkg")

    def test_a_module_level_cycle_is_found(self, tmp_path):
        graph = self._tree(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/a.py": "from .b import x\n",
                "pkg/b.py": "from pkg import a\nx = 1\n",
            },
        )
        assert cycles(graph) == [["pkg.a", "pkg.b"]]

    def test_class_body_and_try_imports_count(self, tmp_path):
        graph = self._tree(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/a.py": "class C:\n    from .b import x\n",
                "pkg/b.py": "try:\n    import pkg.a\nexcept ImportError:\n    pass\nx = 1\n",
            },
        )
        assert cycles(graph) == [["pkg.a", "pkg.b"]]

    def test_deferred_and_type_checking_imports_are_not_edges(self, tmp_path):
        graph = self._tree(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/a.py": "from .b import x\n",
                "pkg/b.py": (
                    "from typing import TYPE_CHECKING\n"
                    "if TYPE_CHECKING:\n    from .a import y\n"
                    "def f():\n    from . import a\n"
                    "x = 1\n"
                ),
            },
        )
        assert cycles(graph) == []

    def test_a_submodule_importing_through_its_own_package_is_not_a_cycle(self, tmp_path):
        graph = self._tree(
            tmp_path,
            {
                "pkg/__init__.py": "from .a import y\n",
                "pkg/a.py": "from pkg.b import x\ny = x\n",
                "pkg/b.py": "x = 1\n",
            },
        )
        assert cycles(graph) == []
