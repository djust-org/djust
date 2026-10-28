"""Duration-derived ``slow`` tier.

Every test whose runner-recorded duration in ``.test_durations`` exceeds
``SLOW_SECONDS`` gets the ``slow`` marker at collection, so ``-m "not slow"``
is a fast tier that stays current whenever ``make test-durations-from-ci``
refreshes the file — there is no hand-maintained list to drift.

Why: test time is extremely concentrated. With the 2026-10 file, the 781 of
47,927 recorded tests (1.6%) over 1.0s carry 82% of the recorded time — the
exhaustive differential sweeps, suite-wide system-check runs and
whole-collection meta-tests. Everything else (98%+ of tests) runs in the fast
tier.

Who uses it:

- ``scripts/pre-push-pytest.sh``: when the selector falls back to the full
  suite, the push runs ``-m "not slow"``. A targeted selection still runs its
  slow tests (they are about the change). ``DJUST_PREPUSH_FULL=1`` runs all.
- ``make test-fast``.

Explicit ``@pytest.mark.slow`` keeps working — this only adds markers.

``@pytest.mark.pr_tier`` is the opt-out: a test carrying it stays in the fast tier
however long ``.test_durations`` says it took. It is for a ratchet that MUST run
on every PR and whose cost is a fixture rather than the assertion — a module
fixture is charged to the first test that asks for it, so one slow setup would
otherwise push the only test that asserts the ratchet into the deselected tier
the first time the durations are refreshed.
``DJUST_SLOW_TIER=0`` turns the derived markers off.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

SLOW_SECONDS = 1.0
DURATIONS_FILE = ".test_durations"
DISABLE_ENV = "DJUST_SLOW_TIER"


def slow_ids(durations: dict[str, float], threshold: float = SLOW_SECONDS) -> set[str]:
    return {nodeid for nodeid, seconds in durations.items() if seconds > threshold}


def _load(rootpath: Path) -> dict[str, float]:
    try:
        data = json.loads((rootpath / DURATIONS_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# tryfirst: the markers must exist before pytest's own -m deselection (and
# before pytest-split's --group) look at the items.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(DISABLE_ENV) == "0":
        return
    slow = slow_ids(_load(config.rootpath))
    if not slow:
        return
    marker = pytest.mark.slow(reason="> %.1fs in %s" % (SLOW_SECONDS, DURATIONS_FILE))
    for item in items:
        if item.nodeid in slow and item.get_closest_marker("pr_tier") is None:
            item.add_marker(marker)
