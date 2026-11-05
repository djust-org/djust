"""Keep a local pytest run from flattening the laptop (the third time this came up).

CI runs the full 3.12-3.15 matrix on every PR head and on every ``main`` push, so a
whole-suite run on a developer machine adds no information the CI result will not, and
it costs the wall clock and CPU of everything else running there (load averages of 23-25
with three agents each running ``-n 8``). A note in a doc did not stop it; this does.

Outside CI, and unless ``DJUST_FULL_LOCAL=1`` is set (the ``make test*`` targets and
``scripts/pre-push-pytest.sh`` set it: they ARE the deliberate full gates):

* xdist is held to ``DJUST_LOCAL_MAX_WORKERS`` workers (default 2), however many were
  asked for with ``-n`` (``-n auto`` included);
* a run that collects more than ``DJUST_LOCAL_MAX_ITEMS`` tests (default 5000, against
  about 16,000 in the full suite) stops before running any of them, with the way out in
  the message.

Targeted runs (a file, a class, ``-k``) are far below the limit and are untouched.
"""

from __future__ import annotations

import os

import pytest

DEFAULT_MAX_WORKERS = 2
DEFAULT_MAX_ITEMS = 5000


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def active(environ=None) -> bool:
    """True when the guard applies: not CI, and no explicit opt-in to a full local run."""
    env = os.environ if environ is None else environ
    return not (_truthy(env.get("CI")) or _truthy(env.get("DJUST_FULL_LOCAL")))


def _int_env(name: str, default: int, environ=None) -> int:
    env = os.environ if environ is None else environ
    try:
        value = int(env.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def max_workers(environ=None) -> int:
    return _int_env("DJUST_LOCAL_MAX_WORKERS", DEFAULT_MAX_WORKERS, environ)


def max_items(environ=None) -> int:
    return _int_env("DJUST_LOCAL_MAX_ITEMS", DEFAULT_MAX_ITEMS, environ)


def refusal_message(count: int, limit: int) -> str:
    return (
        f"This run collected {count} tests, over the local limit of {limit}. CI runs the full "
        "matrix on your PR head, so a whole-suite run here only costs the machine.\n"
        "  - run what you changed: pytest path/to/test_file.py   (or -k NAME)\n"
        "  - affected tests:       make test-selected\n"
        "  - the full gate, on purpose: DJUST_FULL_LOCAL=1 make test   (or set it for pytest)\n"
        "  - results for a pushed commit: gh pr checks N, or "
        "gh run list --workflow Tests --commit <full-sha>"
    )


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    """Hold xdist to the local worker cap, whatever ``-n`` asked for.

    xdist resolves ``-n N`` / ``-n auto`` in its own ``pytest_cmdline_main`` and then applies
    ``--maxprocesses`` as a ceiling, so setting that ceiling here (this plugin is registered
    from the root conftest, after xdist, so this runs first) covers both spellings with the
    one mechanism xdist documents for it.
    """
    if not active() or not hasattr(config.option, "maxprocesses"):
        return
    cap = max_workers()
    asked = config.option.maxprocesses
    config.option.maxprocesses = cap if not asked else min(asked, cap)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """After ``-k`` / ``-m`` have deselected: refuse a run that is still the whole suite.

    Under xdist the controller does not collect, so the check there is
    :func:`pytest_xdist_node_collection_finished`; a worker raising here would surface as an
    INTERNALERROR traceback after every worker had collected the lot.
    """
    if not active() or hasattr(config, "workerinput"):
        return
    limit = max_items()
    if len(items) > limit:
        raise pytest.UsageError(refusal_message(len(items), limit))


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    """The controller's view of one worker's collection: stop the run with a clean message."""
    if not active():
        return
    limit = max_items()
    if len(ids) > limit:
        pytest.exit(refusal_message(len(ids), limit), returncode=4)
