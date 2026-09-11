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

### Build the extension once per native Python job

The Python 3.12 shard 1 log showed `uv sync` building the project for about 121
seconds, followed by a separate 73-second `maturin develop` build. Install only
dependencies first, then explicitly build the release extension:

```bash
uv sync --frozen --extra dev --no-install-project
uv run --no-sync maturin develop --release
```

`--no-sync` matters: an ordinary `uv run` can synchronize the project again before
executing maturin. The Python matrix, serial benchmark job, Django scoreboard, and scheduled
main-health job use this install sequence; their later uv commands also use `--no-sync` so they
cannot undo it. The isolated worktree was installed and tested with this exact
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

## Local hooks and CI responsibilities

Tests are separated by language and execution stage, but the Python CI gate still
runs the complete collection rather than excluding slow tests.

| Stage | Responsibility |
| --- | --- |
| Commit | Formatting, linting, secret detection, generated assets, and quick consistency checks on relevant files. |
| Push | Affected Python tests, Rust crate tests, full JavaScript tests for JS changes, compile-heavy Clippy, and applicable audits. Shared-engine/harness changes fall back to the full local suite. |
| PR CI | Full Python 3.12 shards, Rust/JS suites, integration/security gates, serial benchmarks, Django compatibility scoreboard, and free-threaded smoke checks. |
| Main / scheduled CI | Additional Python versions on main and the daily serial Python run that exposes execution-order dependencies. |

Full JavaScript tests and Clippy now explicitly use the `pre-push` stage, matching
the existing Python and Rust test hooks. They previously inherited all stages and
therefore ran on both commit and push. Their commands, arguments, and file filters
are unchanged. A real pre-commit dispatch probe confirmed that they do not execute
at commit, still execute at push, and still reject a push when a check fails.

Cheap checks can run again on push because the pushed range may contain multiple
commits. Expensive suites should not repeat at both local stages. CI remains
necessary because it checks the complete branch in a clean runner environment.
Local hooks do not replace it, and green targeted tests do not prove the full
suite passes. Clippy currently has stricter local flags than the CI invocation;
keep its push gate until any proposed CI-only policy establishes equivalent
coverage.

## Doctor scenarios and shard validation

`make test-harness` runs the doctor and shard-collection checks with timings.
The doctor suite retains one real tool integration smoke test. Its verdict
scenarios execute the same shell script with controlled Cargo/Node commands on
PATH, so an unrelated cold Cargo build no longer repeats for each scenario.
Additional cases cover successful Cargo execution, bootstrap/link/probe failures,
a generic failure, and a real timeout with a shortened deadline. They continue
to assert that later checks run and the expected verdict/remedy appears.

The shard guard now uses `scripts/collect-test-shards.py` to collect the configured
suite once and call the same configured splitter used by the CI invocation for
each group (including the shared-corpus adapter below).
It checks collection success, duration staleness, shard balance, exact coverage,
and absence of duplicate assignments. The former helper cleared pytest's
configured exclusions and accepted partial node IDs even when collection failed;
the new probe preserves those exclusions and refuses a partial snapshot.
Small-corpus tests compare both supported splitting algorithms against ordinary
CLI invocations, and verify that the probe collects only once and never runs test
bodies. The helper depends on pytest-split's plugin API; these comparisons must
continue to pass when upgrading that dependency.

On a local Python 3.12 release build, the previous two full-collection checks took
42.74 seconds combined; the new combined guard took 5.64 seconds. The expanded
focused suite passed 34 tests in 13.77 seconds. The old suite passed 22 tests in
64.07 seconds, but its first doctor run also warmed the Cargo cache, so that total
comparison is not an isolated measure of the code change. In the preceding
successful CI run, six doctor calls each took about 30 seconds. Verify savings
on the next successful CI run; these local timings do not predict its wall time.

## Share the full differential sweep within a shard

The six full-sweep readers request the session-scoped `corpus_payload` fixture.
The root plugin `tests/corpus_shards.py` recognizes this fixture through pytest's
fixture dependency closure and presents its readers to pytest-split's installed
algorithm as one indivisible scheduling unit. It then expands the assignment
back to the original test Items in collection order. Test names, individual
assertions, reports, and duration artifact IDs remain unchanged. Mutated scripts
continue to request `corpus` directly and retain their separate content-keyed
computations. Ordinary unsharded runs are unaffected; pytest-split is not needed
in environments that do not request splitting.

A shared unit is charged at the **maximum** recorded reader duration, rather
than the sum of readers waiting for the same sweep. This is a planning estimate,
not a measurement of pure CPU time or a wall-time guarantee. Ordinary tests
retain their own recorded durations. Missing durations use the mean of known
collected tests, matching pytest-split's fallback. The committed timing file was
refreshed from successful run **34559799610** using
`make test-durations-from-ci RUN=34559799610`; its entries were not hand-adjusted.

Small-corpus subprocess controls execute every shard: the original splitter
starts four independent sweeps, while the adapter starts one and still runs
all tests exactly once. Both splitting algorithms are checked against the
collection probe. Controls also cover missing durations, indirect fixture
consumers, and environments without pytest-split. The full-suite guard requires
all shared readers to land in a single shard as well as complete, non-overlapping
coverage. The original cache still coordinates workers inside the owning shard.
A local instrumented run of all six real readers with two workers passed and
recorded exactly one full-sweep subprocess; the corpus generator was unchanged.

Compare the next successful CI run with the 7m23s parent run before claiming an
elapsed-time improvement. Removing duplicate work can still leave the corpus
shard as the longest job; use its measured duration to guide further balancing.

## Alternatives worth investigating next

| Alternative | Expected benefit | Tradeoff / verification needed |
| --- | --- | --- |
| Build one wheel per Python version, then distribute it to shards | Reduce repeated native builds and runner minutes | Adds a prerequisite job and artifact transfers; may improve cost more than wall time. Verify ABI, commit identity, installed package path, and Python source under test. |
| Partition the Unicode sweep into balanced chunks | Distribute its remaining serial tail | Preserve every scalar/context and the global skew limit; account for extra collection and fixture overhead. |
| Tune local worker budgets by workload | Reduce CPU/RAM contention when Python, Rust, and JS run together | Compare fixed worker counts with `auto`; more workers can make subprocess-heavy tests slower. |
| Persistent content-addressed corpus artifacts | Reuse expensive sweeps across runs | Invalidation must cover native build, Python source, interpreter, settings, script input, and environment; session-only caching is currently easier to trust. |
| Move exhaustive tests to nightly only | Faster PR checks | Loses pre-merge evidence. Do not make this the default while template compatibility is an active release concern. |

After measuring corpus sharing, a build-once wheel job is the next infrastructure
experiment. Template-compilation reuse inside the differential is another
profiling candidate, provided stateful tags and exception outcomes stay equivalent.

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
