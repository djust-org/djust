"""Official adapter extensions (ADR-025 milestone C).

One-file, no-build adapters shipped inside the wheel. The user brings the
third-party library; djust ships only the morph-safe glue (a pre-written
``dj-hook`` plus pre-registered ``JS.ext`` commands).

Adapters are opt-in and cost nothing when unused -- no script tag is emitted
and no work is done unless the name is listed::

    # settings.py
    DJUST_CONFIG = {"extensions": ["chart"]}

This module is the single source of truth for which adapters exist. Both the
script injector (``mixins/post_processing.py``) and the system check
(``checks/configuration.py``) resolve through here, so the two cannot drift
apart -- the parallel-path-drift class the project has hit repeatedly.
"""

from __future__ import annotations

from typing import Dict, List

from .config import get_djust_config

#: Adapter name -> static path of the file that implements it.
#: Adding an entry here is all that is required to make a new adapter
#: configurable, injectable, and check-validated.
AVAILABLE_EXTENSIONS: Dict[str, str] = {
    "chart": "djust/ext/dj-chart.js",
}


def get_configured_extensions() -> List[str]:
    """Return the raw ``DJUST_CONFIG['extensions']`` list, unvalidated.

    Used by the system check, which needs to see unknown names in order to
    report them. Non-list values (a bare string, say) yield an empty list --
    the check reports the type error separately.
    """
    raw = get_djust_config().get("extensions", [])
    if isinstance(raw, (list, tuple)):
        return [str(name) for name in raw]
    return []


def get_enabled_extensions() -> List[str]:
    """Return configured adapter names that actually exist, in config order.

    Unknown names are dropped rather than raising: a typo must not 500 every
    page. The system check (``djust.C015``) is what makes the typo loud.
    """
    return [name for name in get_configured_extensions() if name in AVAILABLE_EXTENSIONS]


def get_extension_static_paths() -> List[str]:
    """Static paths for the enabled adapters, in config order."""
    return [AVAILABLE_EXTENSIONS[name] for name in get_enabled_extensions()]
