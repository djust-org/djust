"""What counts as djust's own code (#4094).

System checks skip djust's own production classes, and the test client leaves
names the framework defines out of a view's state. Both need the same answer
to "is this module djust's?", and a name prefix gives the wrong one: a project
app called ``djust_shop`` is the project's code, not the framework's.

djust is ONE distribution whose Python source is the ``python/`` tree
(``pyproject.toml``: ``python-source = "python"``), and that tree holds one
top-level package, ``djust``. The former standalone ``djust-<name>`` packages
were folded in as ``djust.<name>`` subpackages (ADR-007), so they are covered
by the same rule. ``test_framework_code_4094.py`` pins :func:`framework_top_level_packages` against the
``python/`` tree, so a second top-level package cannot be added without
updating it.
"""

from __future__ import annotations

import functools
import logging
from typing import FrozenSet

logger = logging.getLogger(__name__)

__all__ = ["framework_top_level_packages", "is_djust_module", "is_djust_production_module"]


@functools.lru_cache(maxsize=None)
def framework_top_level_packages() -> FrozenSet[str]:
    """Top-level packages the djust distribution ships (read once, lazily)."""
    names = {"djust"}
    try:
        from importlib.metadata import packages_distributions

        for top_level, dists in packages_distributions().items():
            if any(dist.lower() == "djust" for dist in dists) and not top_level.startswith("_"):
                names.add(top_level)
    except Exception:  # noqa: BLE001 — metadata is optional (editable installs list none)
        logger.debug("djust: distribution metadata unavailable; using the package name only")
    return frozenset(names)


def is_djust_module(module: str) -> bool:
    """True when ``module`` belongs to a package djust itself ships."""
    if not isinstance(module, str) or not module:
        return False
    return module.split(".", 1)[0] in framework_top_level_packages()


def is_djust_production_module(module: str) -> bool:
    """djust's own code other than its tests and examples (which are checked
    like a project's, as dogfooding)."""
    return is_djust_module(module) and "test" not in module and "example" not in module
