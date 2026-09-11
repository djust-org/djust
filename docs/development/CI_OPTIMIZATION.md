# Test performance

Optimize repeated work before reducing coverage. CI remains the full gate; local
pre-push selection is already available through `scripts/select-tests.py`, with
`DJUST_PREPUSH_FULL=1` to request the full suite.

## Measured baseline

[Main run 34554928145](https://github.com/djust-org/djust/actions/runs/34554928145)
completed successfully on 2026-09-11 UTC at `acf38531`. It took 11m55s from creation
to completion, including runner scheduling. Its Python jobs spent 165–197 seconds
installing dependencies/building the extension and 224–367 seconds in pytest.
These are observations from one run, not a fixed CI duration or a promise about
future speedups.

The current architecture already runs language-specific jobs concurrently, uses
four duration-balanced Python shards, runs xdist inside each shard, and caches
identical differential corpus inputs within a pytest session. PRs use Python 3.12;
main also exercises 3.13 and 3.14. A separate job exercises free-threaded Python.

The downloaded Python 3.12 duration artifacts contained 27,453 node IDs and about
2,975 aggregate test-seconds. The largest file totals were:

| Area | Recorded aggregate seconds |
| --- | ---: |
| Differential reachability manifest | 793 |
| Exhaustive Unicode title test and related filter tests | 178 |
| `make doctor` checks | 174 |
| Refusal-comparison tests | 160 |
| System checks | 160 |
| Tag bridge object parity | 144 |
| CI shard self-tests | 70 |

Aggregate seconds include time waiting on shared fixture results; they are not
CPU measurements. In particular, a cached corpus sweep is shared across workers
in **one pytest session**, not across four independent CI shard jobs. Adding up
its readers' durations can exaggerate the amount of distinct computation.

## Changes in this iteration

### Build the extension once per Python job

The Python 3.12 shard 1 log showed `uv sync` building the project for about 121
seconds, followed by a separate 73-second `maturin develop` build. Install only
dependencies first, then explicitly build the release extension:

```bash
uv sync --frozen --extra dev --no-install-project
uv run --no-sync maturin develop --release
```

`--no-sync` matters: an ordinary `uv run` can synchronize the project again before
executing maturin. The isolated worktree was installed and tested with this exact
sequence. Avoid interpreting the eliminated 121-second step as a guaranteed
end-to-end saving: cache state and scheduling still affect the workflow.

### Batch the exhaustive Unicode inputs

The title differential still tests every Unicode scalar in four contexts: alone,
preceded by `a`, followed by `a`, and surrounded by `a`. That is exactly 4,448,256
comparisons. It now sends bounded batches through both template engines, with the
same title filter and autoescape behavior. Cell delimiters and exact result
counts prevent missing outputs from disappearing into concatenated strings.

A separate test compares batching with individual template renders. Replacing the
batch's title filter with a lower filter makes that test fail. The original skew
allowance and global skew bound remain intact.

On the same local Python 3.12 release build, the exhaustive test's call time went
from 39.74s to 26.46s (33% less time). This is a local before/after measurement;
runner results should be recorded separately.

### Keep local coverage complete

`make test` now includes `python/djust/tests/` alongside `tests/` and
`python/tests/`, matching the CI and dedicated Python targets. Explicit pytest
paths override configured discovery, so omitting a root is lost coverage, not an
optimization. A dry-run test checks the command that make actually expands.

## Alternatives worth investigating next

| Alternative | Expected benefit | Tradeoff / verification needed |
| --- | --- | --- |
| Keep expensive corpus readers in the same CI shard | Avoid one full identical sweep per independent shard | Schedule by shared fixture cost, not inflated per-reader waits; prove shard union and disjointness and benchmark the longest shard. |
| Build one wheel per Python version, then distribute it to shards | Reduce repeated native builds and runner minutes | Adds a prerequisite job and artifact transfers; may improve cost more than wall time. Verify ABI, commit identity, installed package path, and Python source under test. |
| Collect once for the shard self-tests | Avoid five repeated full collections | Exercise the actual pytest-split plugin and actual collected IDs, preserving staleness, coverage, and balance checks; do not replace it with a handwritten approximation. |
| Separate one real doctor smoke run from scenario tests | Avoid repeatedly timing out on a cold Cargo smoke build | Scenario tests can control unrelated tools, but retain a real integration run and tests for actual timeout/error behavior. |
| Partition the Unicode sweep into balanced chunks | Distribute its remaining serial tail | Preserve every scalar/context and the global skew limit; account for extra collection and fixture overhead. |
| Tune local worker budgets by workload | Reduce CPU/RAM contention when Python, Rust, and JS run together | Compare fixed worker counts with `auto`; more workers can make subprocess-heavy tests slower. |
| Persistent content-addressed corpus artifacts | Reuse expensive sweeps across runs | Invalidation must cover native build, Python source, interpreter, settings, script input, and environment; session-only caching is currently easier to trust. |
| Move exhaustive tests to nightly only | Faster PR checks | Loses pre-merge evidence. Do not make this the default while template compatibility is an active release concern. |

The first follow-up to measure is corpus affinity. A build-once wheel job is the
next infrastructure experiment. Neither should be claimed faster without a
current-head CI comparison.

## Measurement workflow

1. Download step timings and all four `test-durations-shard-*` artifacts from a
   successful run. Separate job setup, execution, queue time, and aggregate test
   duration.
2. Reproduce the largest avoidable cost in an isolated worktree with its own uv
   environment and a release native build.
3. Run the same correctness checks before and after. For a changed harness, keep
   a control or mutation that proves it still detects the original failure.
4. Run normal commit/push hooks and inspect the new commit's complete CI rollup.
5. After a representative successful CI run, refresh runner-specific timings with
   `make test-durations-from-ci RUN=<run-id>`. Do not replace them with local
   machine durations to claim runner balance.

Keep full logs in `context/terminal/` and temporary measurement scripts in
`scratch/`. Record negative results too: an optimization that merely moves time
into another job or lets tests disappear is not a faster test suite.
