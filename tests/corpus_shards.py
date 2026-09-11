"""Keep full-corpus readers in one CI shard without changing test identities.

pytest-split still balances the work. Its algorithm sees the shared fixture as
one indivisible unit, charged at the slowest recorded reader's duration rather
than the sum of several readers waiting for the same computation. Only real
pytest Items reach pytest's selection/deselection hooks.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

CORPUS_FIXTURE = "corpus_payload"


def shared_readers(items: list) -> list:
    return [item for item in items if CORPUS_FIXTURE in getattr(item, "fixturenames", ())]


@dataclass(frozen=True)
class _SharedSweep:
    nodeid: str = "@@djust-shared-corpus-sweep"

    def __str__(self) -> str:
        return self.nodeid


def split_groups(items: list, durations: dict[str, float], splits: int, algorithm: str) -> list:
    from pytest_split.algorithms import Algorithms, TestGroup

    algo = Algorithms[algorithm].value
    readers = shared_readers(items)
    if not readers:
        return algo(splits, items, durations)

    reader_ids = {item.nodeid for item in readers}
    known = [durations[item.nodeid] for item in items if item.nodeid in durations]
    default = sum(known) / len(known) if known else 1.0
    weights = {item.nodeid: durations.get(item.nodeid, default) for item in items}
    unit = _SharedSweep()
    assert unit.nodeid not in weights, "shared-work id collides with a collected test"
    units = []
    for item in items:
        if item.nodeid not in reader_ids:
            units.append(item)
        elif item is readers[0]:
            units.append(unit)
    weights[unit.nodeid] = max(weights.pop(nodeid) for nodeid in reader_ids)
    groups = []
    for group in algo(splits, units, weights):
        selected_ids = {item.nodeid for item in group.selected if item is not unit}
        if unit in group.selected:
            selected_ids.update(reader_ids)
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
