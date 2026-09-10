"""A run that loses collected items goes RED, not green (#2746).

The failure shape this guards: an xdist run that drops a slice of the suite
reports fewer tests and still exits 0. Nothing in pytest distinguishes "5
tests passed" from "5 tests never ran" — the summary line just gets shorter.
``scripts/check-test-coverage.py`` pins which *directories* are collected,
not how many *items*, so a lost slice inside a collected directory passes it.

Two checks, both evaluated on the process whose exit code the caller sees —
the xdist CONTROLLER under ``-n``, the one process under a serial run:

1. **Every selected item produced a report.** The set of node ids selected
   after deselection (``-k``, ``-m``, pytest-split's ``--group``) is compared
   with the set of node ids that received a ``pytest_runtest_logreport`` in
   any phase (setup / call / teardown — an item skipped at setup still reports).
   A shortfall prints the delta AND the missing node ids, and sets the exit
   status to ``TESTS_FAILED``.

2. **Collected-count floor** — CI-only, enabled by ``DJUST_COLLECTED_FLOOR=1``
   so a local ``-k`` / single-file run is unaffected. The PRE-deselection
   collected count must be at least the integer in ``.test_collected_floor``
   at the pytest rootdir. The file is derived, never hand-typed (#2727):
   ``make test-collected-floor`` runs a real collection with
   ``DJUST_COLLECTED_FLOOR_WRITE=<path>`` set, and this plugin writes the
   number it counted. A missing file or an unmeasurable count is a failure,
   not a pass — a harness that cannot measure must say so (#2730).

Where the numbers come from under xdist (pytest-xdist 3.x):

- The controller never collects (``DSession.pytest_collection`` returns True),
  and workers forward only FAILED collect reports (``remote.py``
  ``pytest_collectreport``, the #330 optimisation), so the pre-deselection
  count has to travel in ``config.workeroutput`` — the same channel
  pytest-cov uses — and is read on the controller in ``pytest_testnodedown``.
- The post-deselection ids DO reach the controller:
  ``pytest_xdist_node_collection_finished(node, ids)`` carries each worker's
  ``session.items`` after ``pytest_collection_modifyitems`` (which is where
  pytest-split deselects). Every worker collects the whole suite, so the
  union across workers is the selected set.
- Test reports are relayed one-to-one: ``DSession.worker_testreport`` calls
  ``pytest_runtest_logreport`` on the controller for every report a worker
  emitted.

Enforcement is gated on the run being otherwise clean: a fail-fast stop
(``-x``, ``--maxfail``, ``--sw``, Ctrl-C) or a collection error legitimately
leaves items unrun and already exits non-zero, so the guard stays quiet there
rather than shouting about a loss that is not a loss.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

FLOOR_ENV = "DJUST_COLLECTED_FLOOR"
FLOOR_WRITE_ENV = "DJUST_COLLECTED_FLOOR_WRITE"
FLOOR_FILE = ".test_collected_floor"
WORKEROUTPUT_KEY = "djust_collected_pre_deselection"
SECTION = "djust lost-items guard (#2746)"


def read_floor(path: Path) -> int:
    """The floor is the first non-blank, non-``#`` line, as an int."""
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return int(stripped)
    raise ValueError(f"{path} has no integer line")


def write_floor(path: Path, count: int) -> None:
    path.write_text(
        "# Collected-count floor for the CI python-tests roots (#2746).\n"
        "# DERIVED, never hand-typed: `make test-collected-floor` regenerates it\n"
        "# from a real collection. CI (DJUST_COLLECTED_FLOOR=1) fails a run that\n"
        "# collects fewer items than this. Adding tests never trips it; removing\n"
        "# tests means regenerating it in the same PR.\n"
        f"{count}\n"
    )


class LostItemsGuard:
    def __init__(self, config: pytest.Config) -> None:
        self._floor_armed = self._consume_floor_env()
        self.config = config
        # xdist stamps `workerinput` on worker configs only.
        self.is_worker = hasattr(config, "workerinput")
        # Pre-deselection Items, counted the way TerminalReporter counts
        # `_numcollected` (one CollectReport per collector; each Item appears
        # in exactly one parent's `result`).
        self.collected = 0
        # Post-deselection node ids. None until a collection finishes, so
        # "nothing was ever selected" is distinguishable from "empty set".
        self.selected: set[str] | None = None
        self.reported: set[str] = set()
        # Controller only: each worker's pre-deselection count (None when the
        # worker died before reporting one).
        self.worker_collected: dict[str, int | None] = {}
        self._problems: list[str] | None = None
        self._info: list[str] = []

    # -- counting ---------------------------------------------------------

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        self.collected += sum(1 for x in report.result if isinstance(x, pytest.Item))

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        # Serial run, or an xdist worker. Not called on the xdist controller.
        self.selected = {item.nodeid for item in session.items}

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: object, ids: list[str]) -> None:
        # Controller: this worker's post-deselection ids.
        if self.selected is None:
            self.selected = set()
        self.selected.update(ids)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: object, error: object) -> None:
        # Controller: the worker's pre-deselection count rides workeroutput,
        # which xdist attaches on `workerfinished` (absent when it crashed).
        output = getattr(node, "workeroutput", None) or {}
        gateway = getattr(node, "gateway", None)
        worker_id = getattr(gateway, "id", repr(node))
        self.worker_collected[worker_id] = output.get(WORKEROUTPUT_KEY)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reported.add(report.nodeid)

    # -- verdict ----------------------------------------------------------

    def _pre_deselection_count(self) -> tuple[int | None, str | None]:
        """(count, why-None). Under xdist the workers must all agree."""
        if not self.worker_collected:
            return self.collected, None
        values = set(self.worker_collected.values())
        if None in values:
            dead = sorted(w for w, n in self.worker_collected.items() if n is None)
            return None, f"worker(s) {dead} reported no collected count (died?)"
        if len(values) > 1:
            return None, f"workers disagree on the collected count: {self.worker_collected}"
        return values.pop(), None

    def _floor_enabled(self) -> bool:
        return self._floor_armed

    @staticmethod
    def _consume_floor_env() -> bool:
        """Read ``DJUST_COLLECTED_FLOOR`` once and REMOVE it from the environment.

        The floor is a property of the outer CI invocation. A test that spawns
        its own ``pytest`` subprocess (the #2747 hygiene pin runs single cases
        in a fresh interpreter) would otherwise inherit the variable and be
        floored on a one-item collection — which is exactly what turned every
        shard red on PR #2761's first CI run. Consuming the variable here
        means no child of this process can see it, whatever spawns it.
        """
        armed = os.environ.pop(FLOOR_ENV, "") not in ("", "0")
        return armed

    def _evaluate(self, session: pytest.Session, exitstatus: int) -> list[str]:
        if self._problems is not None:
            return self._problems
        problems: list[str] = []

        if self.selected is not None:
            missing = sorted(self.selected - self.reported)
            if missing:
                problems.append(
                    f"{len(missing)} of {len(self.selected)} selected item(s) produced "
                    f"NO report — they never ran, and nothing else would have said so:"
                )
                problems.extend(f"  LOST {nodeid}" for nodeid in missing)

        if self._floor_enabled():
            count, why = self._pre_deselection_count()
            floor_path = Path(self.config.rootpath) / FLOOR_FILE
            if count is None:
                problems.append(f"collected-count floor: could not measure — {why}")
            elif not floor_path.is_file():
                problems.append(
                    f"collected-count floor: {floor_path} is missing — "
                    f"run `make test-collected-floor` and commit it"
                )
            else:
                floor = read_floor(floor_path)
                if count < floor:
                    problems.append(
                        f"collected-count floor: collected {count} items, floor is {floor} "
                        f"({floor - count} short). If tests were deliberately removed, "
                        f"regenerate the floor with `make test-collected-floor`; "
                        f"otherwise a slice of the suite was lost."
                    )
                else:
                    self._info.append(f"collected {count} items >= floor {floor}")

        self._problems = problems
        return problems

    def _enforcing(self, session: pytest.Session, exitstatus: int) -> bool:
        if self.is_worker or self.config.option.collectonly:
            return False
        if session.shouldstop or session.shouldfail:
            return False  # fail-fast: unrun items are expected, and it is already red
        return exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if self.is_worker:
            # Ship the pre-deselection count to the controller.
            self.config.workeroutput[WORKEROUTPUT_KEY] = self.collected  # type: ignore[attr-defined]
            return

        write_to = os.environ.get(FLOOR_WRITE_ENV)
        if write_to:
            if exitstatus not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
                raise pytest.UsageError(
                    f"{FLOOR_WRITE_ENV}: refusing to write a floor from a run that "
                    f"exited {exitstatus!r} — a collection error under-counts."
                )
            count, why = self._pre_deselection_count()
            if count is None:
                raise pytest.UsageError(f"{FLOOR_WRITE_ENV}: could not measure — {why}")
            write_floor(Path(write_to), count)
            self._info.append(f"wrote floor {count} to {write_to}")

        if not self._enforcing(session, exitstatus):
            return
        problems = self._evaluate(session, exitstatus)
        if problems and exitstatus == pytest.ExitCode.OK:
            # `wrap_session` returns `session.exitstatus` AFTER this hook, so
            # the mutation is the process exit code (proved by
            # tests/test_lost_items_guard.py::test_exit_code_is_nonzero_in_a_subprocess).
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        if self.config.pluginmanager.get_plugin("terminalreporter") is None:
            for line in self._render(problems):
                print(line, file=sys.stderr)

    def _render(self, problems: list[str]) -> list[str]:
        if not problems:
            return [f"{SECTION}: {line}" for line in self._info]
        return [f"!!! {SECTION} !!!", *problems, "(exit status forced to TESTS_FAILED)"]

    def pytest_terminal_summary(self, terminalreporter, exitstatus: int) -> None:
        if self._problems is None and not self._info:
            return
        problems = self._problems or []
        if problems:
            terminalreporter.write_sep("!", SECTION, red=True, bold=True)
            for line in problems:
                terminalreporter.write_line(line, red=True)
            terminalreporter.write_line("(exit status forced to TESTS_FAILED)", red=True, bold=True)
        else:
            for line in self._info:
                terminalreporter.write_line(f"{SECTION}: {line}")


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(LostItemsGuard(config), "djust_lost_items_guard")
