#!/usr/bin/env python3
"""Collect once and ask the installed pytest-split plugin for every shard.

This is a read-only probe for the CI configuration tests, not a replacement
scheduler. Each group goes through pytest-split's real collection hook using
the same collected Item objects and durations as a normal invocation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest


class ShardSnapshot:
    def __init__(self, splits: int) -> None:
        self.splits = splits
        self.result: dict | None = None

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if session.testsfailed:
            return
        from pytest_split.plugin import PytestSplitPlugin

        config = session.config
        items = list(session.items)
        groups = []
        # Leave normal collection unsplit. Only the probe calls the real
        # splitter, after other collection hooks have finished their work.
        original = config.option.splits, config.option.group
        try:
            config.option.splits = self.splits
            splitter = PytestSplitPlugin(config)
            for group in range(1, self.splits + 1):
                config.option.group = group
                selected = items.copy()
                splitter.pytest_collection_modifyitems(config, selected)
                groups.append([item.nodeid for item in selected])
        finally:
            config.option.splits, config.option.group = original
        self.result = {"collected": [item.nodeid for item in items], "groups": groups}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--splits", type=int, required=True)
    parser.add_argument("--splitting-algorithm", required=True)
    parser.add_argument("--durations-path", type=Path, required=True)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    if args.splits < 1:
        parser.error("--splits must be positive")
    # A failed collection must never leave an earlier successful snapshot.
    args.output.unlink(missing_ok=True)
    probe = ShardSnapshot(args.splits)
    status = pytest.main(
        [
            *args.paths,
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            "--splitting-algorithm",
            args.splitting_algorithm,
            "--durations-path",
            str(args.durations_path),
        ],
        plugins=[probe],
    )
    if status == pytest.ExitCode.OK and probe.result is not None:
        args.output.write_text(json.dumps(probe.result), encoding="utf-8")
        return 0
    return int(status) or 1


if __name__ == "__main__":
    raise SystemExit(main())
