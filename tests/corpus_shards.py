"""Keep the readers of a heavy shared fixture in one CI shard, without changing test identities.

pytest-split still balances the work. Its algorithm sees each shared fixture as one
indivisible unit, charged at the recorded worker time of its readers. Waiting readers occupy
workers too, so their durations must be budgeted. Only real pytest Items reach pytest's
selection/deselection hooks.

``SHARED_FIXTURES`` groups the differential-reachability, URL-scheme, and traced safe-markup
sweeps. Without grouping, each shard with a reader runs the whole fixture again (#3781).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class SharedFixture:
    """A fixture whose setup is so heavy that all its readers must share one shard."""

    unit: str  # the id the scheduler sees for the whole reader group
    fixture: str  # the fixture name, matched against item.fixturenames (its dependency closure)
    module: str | None = None  # restrict to one module, for a name too generic to claim globally

    def reads(self, item) -> bool:
        if self.fixture not in getattr(item, "fixturenames", ()):
            return False
        return self.module is None or item.nodeid.startswith(self.module + "::")


CORPUS_FIXTURE = "corpus_payload"

SHARED_FIXTURES = (
    SharedFixture("@@djust-shared-corpus-sweep", CORPUS_FIXTURE),
    SharedFixture(
        "@@djust-shared-url-sweep",
        "runs",
        module="python/tests/test_url_scheme_sweep_3665.py",
    ),
    SharedFixture(
        "@@djust-shared-traced-sweep",
        "traced_sweep",
        module="python/tests/test_safe_markup_audit_3774.py",
    ),
)


def shared_groups(items: list) -> dict[str, list]:
    """The readers of each shared fixture, keyed by unit id; fixtures nobody reads are absent.

    A reader of two shared fixtures would have to sit in one unit; no test does, and the
    scheduler refuses to guess (see ``split_groups``).
    """
    groups = {f.unit: [item for item in items if f.reads(item)] for f in SHARED_FIXTURES}
    return {unit: readers for unit, readers in groups.items() if readers}


def shared_readers(items: list) -> list:
    """Every reader of any shared fixture, in collection order."""
    in_a_group = {item.nodeid for readers in shared_groups(items).values() for item in readers}
    return [item for item in items if item.nodeid in in_a_group]


@dataclass(frozen=True)
class _SharedUnit:
    nodeid: str

    def __str__(self) -> str:
        return self.nodeid


def split_groups(items: list, durations: dict[str, float], splits: int, algorithm: str) -> list:
    from pytest_split.algorithms import Algorithms, TestGroup

    algo = Algorithms[algorithm].value
    shared = shared_groups(items)
    if not shared:
        return algo(splits, items, durations)

    owner: dict[str, _SharedUnit] = {}  # reader nodeid -> its unit
    for unit_id, readers in shared.items():
        unit = _SharedUnit(unit_id)
        for item in readers:
            assert item.nodeid not in owner, f"{item.nodeid} reads two shared fixtures"
            owner[item.nodeid] = unit
    known = [durations[item.nodeid] for item in items if item.nodeid in durations]
    default = sum(known) / len(known) if known else 1.0
    weights = {item.nodeid: durations.get(item.nodeid, default) for item in items}
    for unit_id in shared:
        assert unit_id not in weights, "shared-work id collides with a collected test"
    units: list = []
    placed: set[str] = set()
    members: dict[str, list[str]] = {unit_id: [] for unit_id in shared}
    for item in items:
        unit = owner.get(item.nodeid)
        if unit is None:
            units.append(item)
            continue
        members[unit.nodeid].append(item.nodeid)
        if unit.nodeid not in placed:
            placed.add(unit.nodeid)
            units.append(unit)
    for unit_id, reader_ids in members.items():
        weights[unit_id] = sum(weights.pop(nodeid) for nodeid in reader_ids)
    groups = []
    for group in algo(splits, units, weights):
        selected_ids = {item.nodeid for item in group.selected if not isinstance(item, _SharedUnit)}
        for item in group.selected:
            if isinstance(item, _SharedUnit):
                selected_ids.update(members[item.nodeid])
        groups.append(
            TestGroup(
                selected=[item for item in items if item.nodeid in selected_ids],
                deselected=[item for item in items if item.nodeid not in selected_ids],
                duration=group.duration,
            )
        )
    return groups


def create_splitter(config: pytest.Config):
    # Some smoke environments install pytest without pytest-split. Import it
    # only when splitting is requested (or the collection probe asks for it).
    from pytest_split.ipynb_compatibility import ensure_ipynb_compatibility
    from pytest_split.plugin import PytestSplitPlugin

    class CorpusSplitPlugin(PytestSplitPlugin):
        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, config: pytest.Config, items: list) -> None:
            if not shared_readers(items):
                return super().pytest_collection_modifyitems(config, items)
            groups = split_groups(
                items,
                self.cached_durations,
                config.option.splits,
                config.option.splitting_algorithm,
            )
            group = groups[config.option.group - 1]
            ensure_ipynb_compatibility(group, items)
            items[:] = group.selected
            config.hook.pytest_deselected(items=group.deselected)
            self.writer.line(
                f"[pytest-split] Group {config.option.group}/{config.option.splits}, "
                f"{config.option.splitting_algorithm} with shared corpus "
                f"(estimated duration: {group.duration:.2f}s)"
            )

    return CorpusSplitPlugin(config)


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    original = config.pluginmanager.get_plugin("pytestsplitplugin")
    if original is not None:
        config.pluginmanager.unregister(original)
        config.pluginmanager.register(create_splitter(config), "pytestsplitplugin")
