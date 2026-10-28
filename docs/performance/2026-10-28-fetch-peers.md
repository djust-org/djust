# Django 6.1 fetch modes: queryset rendering benchmark

Measured locally on 2026-10-28 using Python 3.12.9, Django 6.1.2, SQLite and the djust 2.0 worktree. This is a focused in-process microbenchmark, not a WebSocket/HTTP latency or production-capacity measurement. The host was shared with other work. It measures fresh queryset evaluation, djust JIT serialization and Rust template rendering; fixture creation is excluded. Each render uses 50 rows, each with a distinct related object. Results are from one pytest-benchmark run with at least 15 rounds per variant; times are medians, not guarantees.

| Template access | Fetch strategy | SQL queries | Median per render |
| --- | --- | ---: | ---: |
| Property accesses a related object for every row | Django default | 51 | 5.340 ms |
| Same property | `FETCH_PEERS` | 2 | 1.446 ms |
| Same property | Explicit `select_related()` | 1 | 1.126 ms |
| Property accesses a relation for only the first row | Django default | 2 | 0.890 ms |
| Same conditional property | `FETCH_PEERS` | 2 | 1.330 ms |
| Template names the relation directly | Django default, djust JIT optimization | 1 | Not timed in this run |
| Same direct template access | `FETCH_PEERS`, djust JIT optimization | 1 | Not timed in this run |

Output is checked for equality. The all-row property case illustrates a dynamic access djust's static template-path analysis cannot turn into a join. In the conditional case, peer fetching does not reduce query count and loads peers that the property does not subsequently use; that illustrates why enabling it globally can be a regression. The directly named relation already takes the JIT's `select_related()` path. This does not establish benefits for every template, real database, application or workload. Django's fetch modes do not replace deliberate collection prefetching.

Regression and benchmark cases are in [`tests/benchmarks/test_fetch_peers_61.py`](../../tests/benchmarks/test_fetch_peers_61.py). With a working djust test environment running Django 6.1, reproduce with:

```sh
bash scripts/run-with-venv-python.sh -m pytest tests/benchmarks/test_fetch_peers_61.py -q \
  --benchmark-only --benchmark-min-rounds=15 --benchmark-max-time=0.4
```

Run without `--benchmark-only` (or with `--benchmark-disable`) for the query-count and output assertions. Older Django versions skip the `FETCH_PEERS` variants while retaining the default and explicit-join controls. The test also checks a sliced queryset and verifies that mode survives the queryset serialization path. No performance threshold is enforced. Raw local benchmark output is in `context/terminal/fetch-peers-final-20261028.log` and benchmark JSON is in `/private/tmp/djust-django61-probe/fetch-final-after-compat.json`; these scratch artifacts are not versioned.
