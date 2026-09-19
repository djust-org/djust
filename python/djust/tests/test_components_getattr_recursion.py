"""`hasattr(djust.components, x)` must answer, not recurse.

`djust.components` resolves the ~150 component classes lazily through a
module-level ``__getattr__``. Written as ``from . import components``, that
asks the import machinery for the attribute ``components`` on this module —
and until the submodule is bound, the lookup comes straight back into
``__getattr__``, which asks again. Any probe for a name that does not exist
recursed until the interpreter gave up.

It shipped in 1.2.0rc9 and broke consumers that probe for optional names:
djust-docs' symbol gate walks the public API with ``hasattr`` and died with
``RecursionError``. 1.2.0rc6 through rc8 are unaffected.

**Every probe runs in a subprocess, deliberately.** The bug only bites while
the submodule is unbound, so reproducing it in-process needs
``djust.components`` cleared out of ``sys.modules`` first — and doing that
detaches the component classes every later test in the session already holds,
which broke three unrelated gallery tests on the shard that ran this file.
A subprocess gets the cold-import state for free and mutates nothing.
"""

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest


def _tree_under_test() -> str:
    """The directory that must be on the child's path for it to import the
    djust this test session imported.

    Without this the child resolves whatever `djust` the ambient environment
    installs — in a worktree that is the main checkout, so the probe would
    happily test a different tree than the one under review and report it as
    a pass. `pyproject.toml` sets pytest's own `sys.path`, which a subprocess
    does not inherit.
    """
    import djust

    return str(pathlib.Path(djust.__file__).resolve().parent.parent)


def _child_env() -> dict:
    tree = _tree_under_test()
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = tree + (os.pathsep + existing if existing else "")
    return env


#: Prepended to every probe so the child proves WHICH tree it loaded. A probe
#: that silently imported a different djust would otherwise pass while
#: testing nothing.
_ASSERT_SAME_TREE = """
import pathlib, sys
import djust
_expected = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
_actual = pathlib.Path(djust.__file__).resolve().parent.parent
if _expected is not None and _actual != _expected:
    print("WRONG_TREE:" + str(_actual))
    raise SystemExit(0)
"""


def _probe(source: str) -> str:
    """Run *source* in a fresh interpreter and return its last output line."""
    tree = _tree_under_test()
    result = subprocess.run(
        [sys.executable, "-c", _ASSERT_SAME_TREE + textwrap.dedent(source), tree],
        capture_output=True,
        text=True,
        timeout=120,
        env=_child_env(),
    )
    assert result.returncode == 0, f"probe crashed:\n{result.stderr[-2000:]}"
    verdict = (result.stdout.strip().splitlines() or ["NO_OUTPUT"])[-1]
    assert verdict != "NO_OUTPUT", "probe produced no output — it tested nothing"
    assert not verdict.startswith("WRONG_TREE:"), (
        f"probe imported the wrong djust: {verdict.split(':', 1)[1]} instead of {tree}"
    )
    return verdict


def test_a_missing_name_answers_false_instead_of_recursing():
    assert (
        _probe(
            """
            import sys; sys.setrecursionlimit(300)
            import djust.components as c
            try:
                print("ABSENT" if not hasattr(c, "NoSuchComponentAnywhere") else "PRESENT")
            except RecursionError:
                print("RECURSION")
            """
        )
        == "ABSENT"
    )


def test_a_missing_name_raises_attribute_error():
    assert (
        _probe(
            """
            import sys; sys.setrecursionlimit(300)
            import djust.components as c
            try:
                c.NoSuchComponentAnywhere
                print("NO_ERROR")
            except AttributeError:
                print("ATTRIBUTE_ERROR")
            except RecursionError:
                print("RECURSION")
            """
        )
        == "ATTRIBUTE_ERROR"
    )


def test_a_dunder_probe_does_not_recurse():
    # The import machinery probes dunders on the parent package; each miss
    # re-entered the hook.
    assert (
        _probe(
            """
            import sys; sys.setrecursionlimit(300)
            import djust.components as c
            try:
                hasattr(c, "__wrapped__")
                print("OK")
            except RecursionError:
                print("RECURSION")
            """
        )
        == "OK"
    )


@pytest.mark.parametrize("name", ["Accordion", "Badge"])
def test_the_lazy_classes_still_resolve(name):
    assert (
        _probe(
            f"""
            import djust.components as c
            print(getattr(c, {name!r}).__name__)
            """
        )
        == name
    )


def test_the_submodule_is_still_reachable_as_an_attribute():
    assert (
        _probe(
            """
            import djust.components as c
            print(c.components.__name__)
            """
        )
        == "djust.components.components"
    )


def test_a_name_defined_here_still_wins():
    # `__getattr__` is only consulted for a missing attribute, so a name this
    # package defines itself must not be shadowed by the subpackage.
    assert (
        _probe(
            """
            import djust.components as c
            print(c.Component.__module__.startswith("djust.components"))
            """
        )
        == "True"
    )
