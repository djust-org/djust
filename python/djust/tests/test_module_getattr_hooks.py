"""Every module-level ``__getattr__`` must answer a missing name.

A PEP 562 hook that resolves names lazily has one trap, and djust fell into
it in 1.2.0rc9: written as ``from . import submodule``, the hook asks the
import machinery for an attribute on its OWN module, and until that submodule
is bound the lookup re-enters the hook, which asks again. The failure is not
a wrong answer — it is unbounded recursion, so a consumer probing an optional
name with ``hasattr`` crashes instead of getting ``False``.

**Each probe runs in a fresh interpreter, and that is the whole difficulty.**
The bug only bites while the submodule is still unbound. Anything that
imported it first — a package walk, an earlier test, the fixture itself —
binds the attribute, after which ``__getattr__`` is never consulted for it
and the recursion cannot be reproduced. A first version of this file probed
in-process, passed against the known-broken code, and would have guarded
nothing.

Two shapes keep a hook safe and both are worth copying:

* decide from a known-names map BEFORE importing anything, so a name that
  does not exist never triggers an import at all; and
* reach the submodule with ``importlib.import_module``, which resolves
  through ``sys.modules`` rather than through an attribute lookup on self.
"""

import ast
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

import djust

_MISSING = "_no_such_attribute_anywhere_in_djust_"


def _modules_with_a_getattr_hook(root=None):
    """Discover declarations without executing package initialization.

    Importing every module in the pytest process can reconfigure logging and
    close pytest's capture streams. Only the isolated probes below execute a
    discovered module; discovery must not alter the process under test.
    """
    root = pathlib.Path(djust.__file__).parent if root is None else root
    found = []
    for source in root.rglob("*.py"):
        relative = source.relative_to(root)
        if {"tests", "migrations"}.intersection(relative.parts):
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        if not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__"
            for node in tree.body
        ):
            continue
        parts = relative.with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found.append(".".join(("djust", *parts)))
    return sorted(set(found))


def test_discovery_is_static_and_excludes_class_hooks(tmp_path):
    (tmp_path / "__init__.py").write_text(
        "raise RuntimeError('must not import')\ndef __getattr__(name): raise AttributeError(name)\n"
    )
    (tmp_path / "ordinary.py").write_text(
        "class Example:\n    def __getattr__(self, name): return None\n"
    )
    package = tmp_path / "nested"
    package.mkdir()
    (package / "__init__.py").write_text("def __getattr__(name): raise AttributeError(name)\n")
    assert _modules_with_a_getattr_hook(tmp_path) == ["djust", "djust.nested"]


def _tree_under_test() -> str:
    """The directory the child must have on its path to import the djust this
    session imported. A subprocess does not inherit pytest's `sys.path`, so
    without this it resolves whatever djust the ambient environment installs
    — in a worktree, the main checkout — and would report a pass for a tree
    nobody is reviewing."""
    return str(pathlib.Path(djust.__file__).resolve().parent.parent)


def _probe_in_a_fresh_interpreter(module_name: str, attribute: str) -> str:
    """Import *module_name* cold and probe *attribute*. Returns a verdict.

    Every non-verdict outcome is surfaced rather than swallowed. A dead
    subprocess used to read as a pass, and so did `SKIP:RecursionError` —
    which is this exact bug class raised one frame earlier.
    """
    tree = _tree_under_test()
    script = textwrap.dedent(
        f"""
        import pathlib, sys
        sys.setrecursionlimit(300)
        import importlib
        import djust
        actual = pathlib.Path(djust.__file__).resolve().parent.parent
        if str(actual) != {tree!r}:
            print("WRONG_TREE:" + str(actual))
            raise SystemExit(0)
        try:
            module = importlib.import_module({module_name!r})
        except RecursionError:
            print("RECURSION")
            raise SystemExit(0)
        except Exception as exc:
            print("IMPORT_FAILED:" + type(exc).__name__)
            raise SystemExit(0)
        try:
            present = hasattr(module, {attribute!r})
        except RecursionError:
            print("RECURSION")
            raise SystemExit(0)
        print("PRESENT" if present else "ABSENT")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": tree + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert result.returncode == 0, (
        f"probe for {module_name}.{attribute} crashed "
        f"(exit {result.returncode}):\n{result.stderr[-2000:]}"
    )
    verdict = (result.stdout.strip().splitlines() or ["NO_OUTPUT"])[-1]
    assert verdict in {"PRESENT", "ABSENT", "RECURSION"}, (
        f"probe for {module_name}.{attribute} produced no usable verdict: "
        f"{verdict!r}. It tested nothing."
    )
    return verdict


@pytest.fixture(scope="module")
def hooked_modules():
    names = _modules_with_a_getattr_hook()
    assert names, "no module-level __getattr__ found — has the walk broken?"
    return names


def test_the_known_lazy_packages_are_still_covered(hooked_modules):
    """If a package stops being lazy this list shrinks and the guards below
    quietly stop testing it. Pin the ones that exist today."""
    assert "djust.components" in hooked_modules
    assert "djust" in hooked_modules


def test_a_missing_name_never_recurses(hooked_modules):
    bad = {
        name: verdict
        for name in hooked_modules
        if (verdict := _probe_in_a_fresh_interpreter(name, _MISSING)) == "RECURSION"
    }
    assert bad == {}, (
        f"module-level __getattr__ recursed on a missing name: {sorted(bad)}. "
        "Resolve the submodule with importlib.import_module rather than "
        "`from . import x`, and answer from a known-names map before "
        "importing anything."
    )


def test_a_missing_name_answers_false(hooked_modules):
    wrong = [
        name
        for name in hooked_modules
        if _probe_in_a_fresh_interpreter(name, _MISSING) == "PRESENT"
    ]
    assert wrong == [], f"__getattr__ invented an attribute for a missing name: {wrong}"


def test_a_dunder_probe_never_recurses(hooked_modules):
    """The import machinery probes dunders on a package; each miss reaches
    the hook, which is how the recursion was first triggered in practice."""
    bad = [
        (name, probe)
        for name in hooked_modules
        for probe in ("__wrapped__", "__all__")
        if _probe_in_a_fresh_interpreter(name, probe) == "RECURSION"
    ]
    assert bad == [], f"dunder probe recursed: {bad}"
