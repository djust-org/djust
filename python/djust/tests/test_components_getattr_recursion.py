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

import subprocess
import sys
import textwrap

import pytest


def _probe(source: str) -> str:
    """Run *source* in a fresh interpreter and return its last output line."""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"probe crashed:\n{result.stderr[-2000:]}"
    return (result.stdout.strip().splitlines() or ["NO_OUTPUT"])[-1]


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
