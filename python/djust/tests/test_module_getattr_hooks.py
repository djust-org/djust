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

import importlib
import pkgutil
import subprocess
import sys
import textwrap

import pytest

import djust

_MISSING = "_no_such_attribute_anywhere_in_djust_"


def _modules_with_a_getattr_hook():
    """Every importable djust module that defines a module-level hook."""
    found = []
    for info in pkgutil.walk_packages(djust.__path__, prefix="djust."):
        if ".tests" in info.name or ".migrations" in info.name:
            continue
        try:
            module = importlib.import_module(info.name)
        except Exception:  # noqa: BLE001 — optional extras need not import
            continue
        if callable(module.__dict__.get("__getattr__")):
            found.append(info.name)
    if callable(djust.__dict__.get("__getattr__")):
        found.append("djust")
    return sorted(set(found))


def _probe_in_a_fresh_interpreter(module_name: str, attribute: str) -> str:
    """Import *module_name* cold and probe *attribute*. Returns a verdict."""
    script = textwrap.dedent(
        f"""
        import sys
        sys.setrecursionlimit(300)
        import importlib
        try:
            module = importlib.import_module({module_name!r})
        except Exception as exc:
            print("SKIP:" + type(exc).__name__)
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
        text=True,
        timeout=120,
    )
    return (result.stdout.strip().splitlines() or ["NO_OUTPUT"])[-1]


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
