"""Meta-invariant test for the theming register/get roundtrip contract.

Closes #1597. Structural prevention for the #1595 bug class.

## The bug class

#1595 caught a real divergence: `register_preset()` added to the runtime
registry, but `presets.get_preset()` read from a static module dict only —
the CSS generator silently rendered the wrong palette. PR #1596 fixed the
specific case (preset getter now consults the registry first) by mirroring
the pattern in `theme_packs.get_theme_pack()`.

The bug class generalizes: **any `register_X()` API whose matching `get_X()`
ignores the runtime registry**. There's nothing today that catches this
structurally; the next `register_*` API added to `djust.theming.registry`
could repeat the same pattern unnoticed.

## What this test asserts

For every `register_<thing>(name, obj)` function exported from
`djust.theming.registry`, the matching module-level getter
(`presets.get_preset`, `theme_packs.get_theme_pack`, etc.) must return
the registered object when called with that name.

If a new `register_*` API is added to the registry without a matching
registry-aware getter — OR an existing getter drops its registry consult
in a refactor — this test fails fast and names the specific pair that
broke the contract.

## Maintenance

When a new `register_X()` is added to `djust.theming.registry`, add a row
to `ROUNDTRIP_PAIRS` below. The audit test `test_no_unaudited_register_apis`
fails loudly if a register API exists in the registry but no row covers it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable

import pytest

pytestmark = pytest.mark.theming


@dataclass(frozen=True)
class RoundtripPair:
    """One register/get pair under the meta-invariant."""

    # Public registration function name in `djust.theming.registry`.
    register_fn: str
    # Module path of the matching module-level getter.
    getter_module: str
    # Function name in `getter_module` that should return the registered object.
    getter_fn: str
    # Attribute name on the Registry instance holding the underlying dict,
    # used for test cleanup (e.g., "_presets" for register_preset).
    registry_dict_attr: str


# Authoritative table. When a new register_X() API lands in
# djust.theming.registry, add a row here AND verify the matching getter
# follows the registry-first dispatch pattern.
ROUNDTRIP_PAIRS: tuple[RoundtripPair, ...] = (
    RoundtripPair(
        register_fn="register_preset",
        getter_module="djust.theming.presets",
        getter_fn="get_preset",
        registry_dict_attr="_presets",
    ),
    RoundtripPair(
        register_fn="register_design_system",
        getter_module="djust.theming.theme_packs",
        getter_fn="get_design_system",
        registry_dict_attr="_themes",
    ),
    RoundtripPair(
        register_fn="register_theme_pack",
        getter_module="djust.theming.theme_packs",
        getter_fn="get_theme_pack",
        registry_dict_attr="_packs",
    ),
)


def _resolve(pair: RoundtripPair) -> tuple[Callable[[str, Any], None], Callable[[str], Any]]:
    """Resolve a pair to (register_fn_callable, getter_fn_callable)."""
    registry_mod = importlib.import_module("djust.theming.registry")
    register_fn = getattr(registry_mod, pair.register_fn, None)
    assert register_fn is not None, (
        f"#1597 invariant: registry has no public {pair.register_fn!r} — "
        f"either the API was renamed (update ROUNDTRIP_PAIRS) or removed."
    )

    getter_mod = importlib.import_module(pair.getter_module)
    getter_fn = getattr(getter_mod, pair.getter_fn, None)
    assert getter_fn is not None, (
        f"#1597 invariant: {pair.getter_module} has no public {pair.getter_fn!r} — "
        f"either the getter was renamed (update ROUNDTRIP_PAIRS) or removed."
    )

    return register_fn, getter_fn


def _cleanup(pair: RoundtripPair, name: str) -> None:
    """Remove the sentinel from the runtime registry after the test."""
    from djust.theming.registry import get_registry

    reg = get_registry()
    registry_dict = getattr(reg, pair.registry_dict_attr, None)
    if registry_dict is not None:
        registry_dict.pop(name, None)


@pytest.mark.parametrize(
    "pair",
    ROUNDTRIP_PAIRS,
    ids=lambda p: f"{p.register_fn}->{p.getter_fn}",
)
def test_register_get_roundtrip_invariant_1597(pair: RoundtripPair) -> None:
    """For every register_X() in the theming registry, the module-level
    get_X() must return what was registered.

    Failure shape: the getter does not consult the runtime registry — same
    bug class as #1595, which cost a consumer ~1 hour of debugging when the
    rendered palette silently diverged from what `register_preset()` had set.
    """
    register_fn, getter_fn = _resolve(pair)

    # Sentinel chosen to be:
    #  - truthy (so the getter's `registry.get(...) or static_dict.get(...)`
    #    short-circuits to it rather than falling through)
    #  - identity-unique (no equal-but-not-identical comparisons can confuse
    #    the `is` assertion below)
    #  - unique name (so it can't collide with built-ins across test runs)
    sentinel_name = f"_regression_1597_{pair.register_fn}"
    sentinel = object()

    register_fn(sentinel_name, sentinel)
    try:
        result = getter_fn(sentinel_name)
        assert result is sentinel, (
            f"#1597: {pair.getter_fn}({sentinel_name!r}) returned "
            f"{result!r} instead of the runtime-registered sentinel — "
            f"{pair.getter_module}.{pair.getter_fn} does not consult the "
            f"runtime registry. Apply the registry-first dispatch pattern "
            f"from theme_packs.get_theme_pack() (#1596 fixed this exact "
            f"divergence for presets)."
        )
    finally:
        _cleanup(pair, sentinel_name)


def test_no_unaudited_register_apis_1597() -> None:
    """Audit: every `register_*` function exported from `djust.theming.registry`
    must have a row in ROUNDTRIP_PAIRS.

    Fails loud when a new register API lands without a corresponding entry,
    so the meta-invariant doesn't silently undertest the registry surface.
    """
    import djust.theming.registry as registry_mod

    actual_register_fns = {
        name
        for name in dir(registry_mod)
        if name.startswith("register_") and callable(getattr(registry_mod, name))
    }
    audited_register_fns = {p.register_fn for p in ROUNDTRIP_PAIRS}

    unaudited = actual_register_fns - audited_register_fns
    assert not unaudited, (
        f"#1597: new register API(s) exported from djust.theming.registry "
        f"without coverage in ROUNDTRIP_PAIRS: {sorted(unaudited)}. "
        f"Add a row to ROUNDTRIP_PAIRS in this file AND verify the matching "
        f"module-level getter consults the runtime registry first (mirroring "
        f"theme_packs.get_theme_pack at python/djust/theming/theme_packs.py:1216)."
    )

    # Defensive: the audit table itself must not reference removed APIs.
    stale = audited_register_fns - actual_register_fns
    assert not stale, (
        f"#1597: ROUNDTRIP_PAIRS references register API(s) no longer "
        f"exported from djust.theming.registry: {sorted(stale)}. "
        f"Remove the stale row(s) from ROUNDTRIP_PAIRS."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
