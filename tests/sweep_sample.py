"""Exhaustive sweeps: in full nightly, sampled before merge (#4074).

A handful of security sweeps drive every component class, tag, sink or analysis mutant with
hostile values. Together they were about 30% of all recorded test time, so PR and merge-queue CI
ran none of them and every push to ``main`` ran all of them in every matrix cell. Now:

* **Full** — every unit. The default for a bare ``pytest`` (and so for the nightly
  ``main-health`` run, which passes no ``-m``) and for ``make test-sweeps-full``.
* **Sampled** — the units most likely to break plus a seeded, rotating remainder. On whenever the
  run deselects the ``slow`` tier (``-m "... not slow"``: PR and merge-queue CI, ``make
  test-fast``, the pre-push fallback), or when ``DJUST_SWEEP_SAMPLE=1`` (CI sets it for the push
  to ``main``). ``DJUST_SWEEP_SAMPLE=0`` forces full.

A sweep test carries ``@pytest.mark.sweep("<population>")`` and asks :func:`select` (or
:func:`skip_unless_sampled`) which units to run. In sampled mode the selection is:

1. every unit the caller says is ``always`` run (regression-pinned cases);
2. every unit whose source paths intersect the changed paths (``DJUST_SWEEP_CHANGED_FILE``: a
   file listing one path per line, which CI writes from the PR's diff; or
   ``DJUST_SWEEP_CHANGED``: a comma-separated list; neither set, locally, it is ``git diff``
   against the merge base with ``origin/main`` plus the working tree); every unit, if a path the
   whole sweep runs through changed;
3. ``budget`` more units drawn with ``random.Random(f"{seed}/{population}")``.

The seed is ``DJUST_SWEEP_SEED`` (CI sets the commit SHA and the matrix cell, so each cell draws a
different sample and successive commits rotate through the whole population), else a fresh random
token. It is printed in the report header, and a failing sweep test prints the exact command that
re-runs the same selection locally.

``tests/test_sweep_tiers_4074.py`` keeps this honest: a sampled selection is never empty, the
full populations only grow (a ratchet on their sizes), and every slow test in a sweep file is
sampled, nightly-only, or a structural pin in the PR tier.
"""

from __future__ import annotations

import os
import random
import secrets
import shlex
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

MODE_ENV = "DJUST_SWEEP_SAMPLE"
SEED_ENV = "DJUST_SWEEP_SEED"
CHANGED_ENV = "DJUST_SWEEP_CHANGED"  # comma-separated paths
CHANGED_FILE_ENV = "DJUST_SWEEP_CHANGED_FILE"  # a file listing one path per line (CI)

ROOT = Path(__file__).resolve().parents[1]

_state: dict = {"sampled": False, "seed": "", "changed": frozenset(), "used": {}, "matched": set()}


def configure(*, sampled: bool, seed: str = "", changed: Iterable[str] = ()) -> None:
    """Set the mode directly (the plugin does this at start-up; tests of the sampler use it)."""
    _state.update(
        sampled=sampled,
        seed=seed or secrets.token_hex(4),
        changed=frozenset(changed),
        used={},
        matched=set(),
    )


def is_sampled() -> bool:
    return bool(_state["sampled"])


def seed() -> str:
    return str(_state["seed"])


def changed_paths() -> frozenset[str]:
    return frozenset(_state["changed"])


def select(
    population: str,
    units: Iterable[str],
    *,
    budget: int,
    always: Iterable[str] = (),
    paths: Callable[[str], Iterable[str]] | None = None,
    shared: Iterable[str] = (),
) -> list[str]:
    """The units of ``population`` this run drives, in their original order.

    Full mode: all of them. Sampled: all of them if a ``shared`` path (code every unit runs
    through, or the sweep itself) changed; otherwise ``always``, the units whose
    ``paths(unit)`` changed, and ``budget`` more drawn from the rest. The draw is seeded by the
    run's seed, the population and the current test, so two tests sweeping the same units draw
    different ones and a re-run of one test draws the same ones again.
    """
    units = list(dict.fromkeys(units))
    changed = _state["changed"]
    hit_shared = changed.intersection(_norm(p) for p in shared) if _state["sampled"] else set()
    if not _state["sampled"] or hit_shared:
        _state["matched"].update(hit_shared)
        _state["used"][population] = (len(units), len(units), len(units) if hit_shared else 0)
        return units
    pinned = set(always)
    forced: set[str] = set()
    for unit in units:
        if unit in pinned:
            forced.add(unit)
            continue
        if paths is not None and changed:
            hit = changed.intersection(_norm(p) for p in paths(unit))
            if hit:
                forced.add(unit)
                _state["matched"].update(hit)
    rest = [u for u in units if u not in forced]
    rng = random.Random(f"{_state['seed']}/{population}/{_current_test()}")
    drawn = set(rng.sample(rest, min(max(budget, 0), len(rest))))
    chosen = [u for u in units if u in forced or u in drawn]
    _state["used"][population] = (len(chosen), len(units), len(forced))
    return chosen


def skip_unless_sampled(population: str, unit: str, units: Iterable[str], **kwargs) -> None:
    """For a sweep parametrized by unit: skip this case unless the run selected it."""
    if unit not in select(population, units, **kwargs):
        pytest.skip(f"{population}: {unit!r} is not in this run's sample (seed {seed()})")


def floor(full: int, chosen: int, total: int) -> int:
    """A "the sweep barely ran" floor scaled to the fraction of the population this run drives.

    Halved, because the units are not equally productive and a small sample can draw the quiet
    ones; the full run keeps the full floor.
    """
    if not _state["sampled"] or total == 0 or chosen >= total:
        return full
    return int(full * chosen / total / 2)


def module_paths(obj: object) -> list[str]:
    """Repository-relative source path of a class or function, for the changed-path mapping."""
    import inspect

    try:
        path = Path(inspect.getsourcefile(obj) or "")
    except TypeError:
        return []
    try:
        return [path.resolve().relative_to(ROOT).as_posix()]
    except ValueError:
        return []


def _current_test() -> str:
    # "path::test[param] (call)" -> "path::test[param]"; empty outside a test
    return os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" (", 1)[0]


def _norm(path: str) -> str:
    path = path.strip()
    return path[2:] if path.startswith("./") else path


def _read_changed(listed: str | None, listing: str | None) -> frozenset[str]:
    if listing is not None:
        try:
            lines = Path(listing).read_text().splitlines()
        except OSError:
            lines = []
        return frozenset(_norm(p) for p in lines if p.strip())
    if listed is not None:
        return frozenset(_norm(p) for p in listed.split(",") if p.strip())
    # Local default: what this branch changes against origin/main, committed or not.
    out: set[str] = set()
    for args in (["diff", "--name-only", "origin/main...HEAD"], ["diff", "--name-only", "HEAD"]):
        try:
            res = subprocess.run(
                ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20, check=False
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0:
            out.update(_norm(p) for p in res.stdout.splitlines() if p.strip())
    return frozenset(out)


def _excludes_slow(markexpr: str) -> bool:
    return "not slow" in " ".join(markexpr.split())


def pytest_configure(config: pytest.Config) -> None:
    env = os.environ.get(MODE_ENV)
    sampled = env == "1" if env in ("0", "1") else _excludes_slow(config.option.markexpr or "")
    changed = (
        _read_changed(os.environ.get(CHANGED_ENV), os.environ.get(CHANGED_FILE_ENV))
        if sampled
        else frozenset()
    )
    # An xdist worker takes the controller's seed and changed paths, so every worker draws the
    # same sample (a fresh random seed per worker would make the printed seed meaningless).
    given = getattr(config, "workerinput", {}).get("djust_sweep")
    if given:
        configure(sampled=given["sampled"], seed=given["seed"], changed=given["changed"])
    else:
        configure(sampled=sampled, seed=os.environ.get(SEED_ENV, ""), changed=changed)
    if config.pluginmanager.hasplugin("xdist"):
        config.pluginmanager.register(_XdistSeed(), "djust-sweep-xdist")


class _XdistSeed:
    def pytest_configure_node(self, node) -> None:  # noqa: ANN001
        node.workerinput["djust_sweep"] = {
            "sampled": _state["sampled"],
            "seed": _state["seed"],
            "changed": sorted(_state["changed"]),
        }


def pytest_report_header(config: pytest.Config) -> str:
    if not _state["sampled"]:
        return "sweeps: full (tests/sweep_sample.py)"
    return "sweeps: SAMPLED, %s=%s, %d changed path(s) (tests/sweep_sample.py)" % (
        SEED_ENV,
        seed(),
        len(_state["changed"]),
    )


def repro_command(nodeid: str) -> str:
    env = [f"{MODE_ENV}=1", f"{SEED_ENV}={shlex.quote(seed())}"]
    matched = sorted(_state["matched"])
    env.append(f"{CHANGED_ENV}={shlex.quote(','.join(matched))}")
    return " ".join(env) + " .venv/bin/python -m pytest " + shlex.quote(nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call):  # noqa: ANN001, ANN201
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and item.get_closest_marker("sweep") is not None:
        # user_properties travel from an xdist worker to the controller's summary
        report.user_properties.append(("djust_sweep", dict(_state["used"])))
    if report.failed and _state["sampled"] and item.get_closest_marker("sweep") is not None:
        report.sections.append(
            (
                "sweep sample",
                "This sweep ran a SAMPLE (seed %s). Re-run the same selection:\n  %s\n"
                "Run every unit: %s=0 .venv/bin/python -m pytest %s"
                % (seed(), repro_command(item.nodeid), MODE_ENV, shlex.quote(item.nodeid)),
            )
        )


_seen: dict[str, tuple[int, int, int]] = {}


def pytest_runtest_logreport(report) -> None:  # noqa: ANN001
    for name, value in getattr(report, "user_properties", ()):
        if name == "djust_sweep":
            _seen.update({k: tuple(v) for k, v in value.items()})


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ANN001
    used = {**_seen, **_state["used"]}
    if not used:
        return
    mode = "sampled, seed %s" % seed() if _state["sampled"] else "full"
    terminalreporter.write_sep("-", "sweeps (%s)" % mode)
    for population, (chosen, total, forced) in sorted(used.items()):
        terminalreporter.write_line(
            "  %-40s %5d of %5d units (%d forced)" % (population, chosen, total, forced)
        )
