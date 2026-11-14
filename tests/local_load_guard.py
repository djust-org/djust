"""Keep a local pytest run from flattening the machine (#4111).

CI runs the full 3.12-3.15 matrix on every PR head and on every ``main`` push, so a
whole-suite run on a developer machine adds no information the CI result will not, and
it costs the wall clock and CPU of everything else running there (load averages of 23-25
with three agents each running ``-n 8``; on 2026-11-13 five whole-suite runs at once, load
above 90). A note in a doc did not stop it, and neither did an opt-in variable that
anything could set. Outside CI:

* a run that collects more than ``DJUST_LOCAL_MAX_ITEMS`` tests (default 5000, against
  about 35,000 in the full suite) needs an **approval**: the file named by
  ``APPROVAL_NAME`` in ``~/.djust/``, holding a Unix time in the future, at most
  ``MAX_APPROVAL_SECONDS`` ahead. The developer writes it deliberately; nothing in the
  repository does. ``DJUST_FULL_LOCAL=1`` alone no longer lets a run past the limit;
* an approved run takes a machine-wide lock (``~/.djust/full-run.lock``), so a second
  run over the limit refuses at once while one is in progress, from any worktree;
* xdist is held to ``DJUST_LOCAL_MAX_WORKERS`` workers (default 2), however many were
  asked for with ``-n`` (``-n auto`` included), unless an approval is valid.

Targeted runs (a file, a class, ``-k``) and ``make test-early`` (``--early-only``, a
bounded tier) are untouched. CI (``CI`` set) is unaffected.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

DEFAULT_MAX_WORKERS = 2
DEFAULT_MAX_ITEMS = 5000
#: An approval further in the future than this is not accepted: it must be time-limited.
MAX_APPROVAL_SECONDS = 24 * 3600
APPROVAL_NAME = "full-run-approval"
LOCK_NAME = "full-run.lock"

#: The open lock file of this process's approved run (held until the process exits).
_LOCK_HANDLE = None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def active(environ=None) -> bool:
    """True when the guard applies: everywhere but CI."""
    env = os.environ if environ is None else environ
    return not _truthy(env.get("CI"))


def _state_dir(home: Path | None = None) -> Path:
    return (Path.home() if home is None else home) / ".djust"


def approval_until(home: Path | None = None, now: float | None = None) -> float | None:
    """The time a valid approval runs until, or ``None`` when there is none.

    Valid: the file holds one number, a Unix time after ``now`` and no more than
    ``MAX_APPROVAL_SECONDS`` after it.
    """
    now = time.time() if now is None else now
    try:
        text = (_state_dir(home) / APPROVAL_NAME).read_text(encoding="utf-8").strip()
        until = float(text)
    except (OSError, ValueError):
        return None
    if not now < until <= now + MAX_APPROVAL_SECONDS:
        return None
    return until


def acquire_full_run_lock(home: Path | None = None):
    """Take the machine-wide lock for a run over the limit.

    Returns an open handle that keeps the lock until it is closed (or the process
    exits), or ``None`` when another run holds it.
    """
    import fcntl

    directory = _state_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / LOCK_NAME, "a+", encoding="utf-8")  # noqa: SIM115 - held open
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write("%d %s\n" % (os.getpid(), os.getcwd()))
    handle.flush()
    return handle


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


def _ways_out() -> str:
    return (
        "  - run what you changed: pytest path/to/test_file.py   (or -k NAME)\n"
        "  - the bounded early tier: make test-early\n"
        "  - affected tests:       make test-selected\n"
        "  - results for a pushed commit: gh pr checks N, or "
        "gh run list --workflow Tests --commit <full-sha>"
    )


def refusal_message(count: int, limit: int) -> str:
    return (
        f"This run collected {count} tests, over the local limit of {limit}. CI runs the full "
        "matrix on your PR head, so a whole-suite run here only costs the machine.\n"
        + _ways_out()
        + "\n  - a full local run, on purpose (the make test* full gates included): the "
        f"developer approves it for a limited time by writing a Unix time at most 24 h ahead "
        f"into ~/.djust/{APPROVAL_NAME}, e.g. two hours:\n"
        "      python3 -c 'import time; print(int(time.time()) + 7200)'\n"
        "    DJUST_FULL_LOCAL=1 no longer bypasses this limit."
    )


def busy_message(count: int, limit: int, home: Path | None = None) -> str:
    try:
        holder = (_state_dir(home) / LOCK_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        holder = ""
    return (
        f"This run collected {count} tests, over the local limit of {limit}, and another "
        "full run is already in progress on this machine"
        + (f" (pid and directory: {holder})" if holder else "")
        + ". One heavy run at a time: wait for it to finish, or\n"
        + _ways_out()
    )


def _early_only(config) -> bool:
    return bool(getattr(config.option, "early_only", False))


def over_limit_refusal(count: int, home: Path | None = None) -> str | None:
    """The refusal for a local run of ``count`` tests, or ``None`` when it may run.

    It may run when it is within the limit, or when it is approved and this
    process holds (or now takes) the machine-wide lock.
    """
    global _LOCK_HANDLE
    limit = max_items()
    if count <= limit:
        return None
    if approval_until(home) is None:
        return refusal_message(count, limit)
    if _LOCK_HANDLE is None:
        _LOCK_HANDLE = acquire_full_run_lock(home)
        if _LOCK_HANDLE is None:
            return busy_message(count, limit, home)
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    """Hold xdist to the local worker cap, whatever ``-n`` asked for.

    xdist resolves ``-n N`` / ``-n auto`` in its own ``pytest_cmdline_main`` and then applies
    ``--maxprocesses`` as a ceiling, so setting that ceiling here (this plugin is registered
    from the root conftest, after xdist, so this runs first) covers both spellings with the
    one mechanism xdist documents for it. An approved full run keeps its workers.
    """
    if not active() or not hasattr(config.option, "maxprocesses"):
        return
    if approval_until() is not None:
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
    if not active() or hasattr(config, "workerinput") or _early_only(config):
        return
    refusal = over_limit_refusal(len(items))
    if refusal is not None:
        raise pytest.UsageError(refusal)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    """The controller's view of one worker's collection: stop the run with a clean message."""
    if not active() or _early_only(node.config):
        return
    refusal = over_limit_refusal(len(ids))
    if refusal is not None:
        pytest.exit(refusal, returncode=4)
