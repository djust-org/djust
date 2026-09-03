"""The ADR-027 kill-switch as a test axis — ONE copy (#2539 movement 3).

Movement 2 left five hand-copied ``resolve_lazy`` context managers across the
#2539/#2564/#2570/#2592 suites. Movement 3 needed the same push in seven MORE
files (the sibling conversion characterizations — #2429, #2477/#2489, #2478,
#2481, #2501 ×2, #2510 — all of which pin mechanisms the flip makes dormant),
and adding seven more copies of a helper that asserts a security-relevant flag
reached Rust is the #1646 shape this repo keeps paying for. So: one helper,
here.

Not a fixture, because the existing call sites are ``with`` blocks inside test
bodies and several push BOTH states in one test. Not in ``conftest.py``,
because importing from a conftest is a pytest implementation detail rather than
a contract.

Migrating the five pre-existing copies onto this module is tracked separately —
movement 3 is the routing flip and keeps its diff to the flip.
"""

from __future__ import annotations

import contextlib
from typing import Iterator

from djust import _rust

__all__ = ["resolve_lazy", "shipped_default"]


def shipped_default() -> bool:
    """The shipped ``template_resolve_lazy`` default (``True`` since #2539
    movement 3). Read, never re-stated, so a later movement that flips it again
    does not leave a literal behind in a test (#1200)."""
    from djust.config import LiveViewConfig

    return bool(LiveViewConfig._defaults["template_resolve_lazy"])


@contextlib.contextmanager
def resolve_lazy(enabled: bool) -> Iterator[None]:
    """Push the ADR-027 flag for the block, through the REAL wiring.

    Goes through ``apply_render_env()`` — the one place every render path
    acquires its ambient settings — and then ASSERTS the Rust thread-local took
    the value. A fixture that set the config and assumed the push would make
    every assertion inside the block vacuous if the wiring broke; a setter with
    no getter cannot be tested end to end (#2017), which is why
    ``_rust.resolve_lazy_enabled`` exists.

    ``resolve_lazy(False)`` is the ESCAPE-HATCH axis: since movement 3 flipped
    the default, a test that means "the old conversion mechanism" has to say so
    explicitly rather than rely on the ambient state.
    """
    from djust.config import config
    from djust.render_env import apply_render_env

    # No literal fallback: `LiveViewConfig.__init__` seeds `_config` from
    # `_defaults`, so the key is always present — and a literal here would be a
    # second statement of the default, which is what movement 3 removed.
    previous = config.get("template_resolve_lazy", shipped_default())
    config.update({"template_resolve_lazy": enabled})
    apply_render_env()
    assert _rust.resolve_lazy_enabled() is enabled, (
        "the ADR-027 flag did not reach Rust — apply_render_env() is not wiring it"
    )
    try:
        yield
    finally:
        config.update({"template_resolve_lazy": previous})
        apply_render_env()
