#!/usr/bin/env python3
"""Union the per-shard `.test_durations` CI uploads into one file (#2584).

The committed `.test_durations` decides how CI deals its four pytest-split
shards. Recording it locally records the WRONG MACHINE: on run 34173511325
the py3.12 shards took 182/235/584/204s of pytest against 280/278/328/193s
recorded on a 12-core Mac — per-shard slowdown factors of 2.6x to 7.1x, so
no single scale factor maps one to the other and no local run can balance
the runner.

So CI records it instead. Each `python-tests` shard runs with
``--store-durations --clean-durations``, which makes pytest-split rewrite the
durations file at sessionfinish holding EXACTLY the tests that shard ran, and
uploads it as ``test-durations-shard-N``. The four are disjoint and their
union is the whole suite, measured on the runner.

This script performs that union, and refuses rather than writing a file that
would silently unbalance the shards:

* a duplicate id across two inputs means the shards were not disjoint — the
  deal changed under the run, and neither value is trustworthy;
* an id outside the three CI test roots means the file came from a different
  invocation than the one CI shards (the same check
  ``tests/test_ci_python_test_shards.py`` makes of the committed file);
* a non-numeric or negative duration is not a duration.

A partial merge is worse than no merge: it looks like a fresh file and
count-balances the ids it is missing. Hence ``--expect N`` (default 4), which
fails when fewer than N inputs are given.

Usage::

    scripts/merge-test-durations.py -o .test_durations shard*/.test_durations
    make test-durations-from-ci RUN=<github-run-id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TEST_ROOTS = ("tests/", "python/tests/", "python/djust/tests/")


def load(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path}: expected a non-empty {{nodeid: seconds}} map")
    for key, value in data.items():
        if not key.startswith(TEST_ROOTS):
            raise ValueError(
                f"{path}: {key!r} is outside {TEST_ROOTS} — this file was recorded "
                "by a different invocation than the one CI shards"
            )
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{path}: {key!r} has a non-duration value {value!r}")
    return data


def merge(sources: dict[str, dict[str, float]]) -> dict[str, float]:
    """Union the inputs, refusing any id that appears in more than one."""
    merged: dict[str, float] = {}
    owner: dict[str, str] = {}
    duplicates: list[str] = []
    for name, data in sources.items():
        for key, value in data.items():
            if key in merged:
                duplicates.append(f"{key} (in {owner[key]} and {name})")
                continue
            merged[key] = value
            owner[key] = name
    if duplicates:
        raise ValueError(
            "shards are not disjoint — %d id(s) recorded by more than one shard, "
            "so the deal changed mid-run and neither value can be trusted:\n  %s"
            % (len(duplicates), "\n  ".join(duplicates[:10]))
        )
    return merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inputs", nargs="+", type=Path, help="per-shard durations files")
    parser.add_argument(
        "-o", "--output", type=Path, required=True, help="where to write the merged file"
    )
    parser.add_argument(
        "--expect",
        type=int,
        default=4,
        help="required number of inputs (default 4: the four CI shards). "
        "A partial merge count-balances every id it is missing.",
    )
    args = parser.parse_args(argv)

    if len(args.inputs) < args.expect:
        print(
            f"refusing to merge {len(args.inputs)} file(s): expected {args.expect}. "
            "A shard whose artifact is missing (cancelled, or its upload failed) "
            "leaves its quarter of the suite unrecorded, and pytest-split "
            "count-balances exactly those ids — the condition this file exists "
            "to avoid. Re-run the workflow, or pass --expect to override.",
            file=sys.stderr,
        )
        return 1

    sources = {}
    for path in args.inputs:
        try:
            sources[str(path)] = load(path)
        except (OSError, ValueError) as exc:
            print(f"cannot use {path}: {exc}", file=sys.stderr)
            return 1

    try:
        merged = merge(sources)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # pytest-split's own on-disk shape: sorted keys, indent=4.
    args.output.write_text(json.dumps(merged, sort_keys=True, indent=4) + "\n", encoding="utf-8")
    total = sum(merged.values())
    print(
        f"merged {len(sources)} shard file(s) -> {args.output}: "
        f"{len(merged)} tests, {total:.0f}s recorded"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
