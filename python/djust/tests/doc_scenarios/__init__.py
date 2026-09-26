"""Named behaviors a documented example must show (ADR-037 D2).

A marker ``scenario=<name>`` selects the function registered here under that
name. Each function receives the ``Example`` and drives it through a real GET
and the real HTTP fallback.
"""

from typing import Callable, Dict

SCENARIOS: Dict[str, Callable] = {}


def scenario(name: str) -> Callable:
    def register(function: Callable) -> Callable:
        if name in SCENARIOS:
            raise ValueError("duplicate scenario %r" % name)
        SCENARIOS[name] = function
        return function

    return register


def load_all() -> Dict[str, Callable]:
    from . import adr034  # noqa: F401  (registers on import)

    return SCENARIOS
