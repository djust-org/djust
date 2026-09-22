# ADR-038 E6-1: explicit versus legacy cost

Measurement, not a threshold. ADR-038 E6 asks for serialization and render cost
evidence before activation. These are the numbers from two runs on one laptop.
They are not a performance claim beyond that machine.

**Benchmark:** `tests/benchmarks/test_exposure_cost.py`. It is collected only
when `tests/benchmarks/` is targeted, which `make benchmark-python` does, and
it has no pass/fail thresholds. The same view logic runs under both policies:

- `LegacyCostView` keeps its state in plain attributes and relies on legacy
  automatic context and persistence.
- `ExplicitCostView` declares `title`, `count` and `items` with
  `state(persist="server")` and supplies them from `get_context_data`.

The payload is 50 rows of mixed primitives, about 6 KB as JSON. Explicit mode
is still behind the constructor guard, so the benchmark bypasses it the way
the `test_exposure_*` suites do.

## Environment

| | |
| --- | --- |
| Machine | Apple M2 Max, 12 cores, 64 GB, macOS 27.0 (26A428) |
| Load | load average 4.4 to 4.7 during both runs (a shared, busy laptop) |
| Python | CPython 3.12.9, pytest-benchmark 5.2.3, Django 5.2.16 |
| Code | branch `adr038/e6-measure`, framework code at `bc3a3d156` (this branch adds only the benchmark, the inventory command and docs) |
| Rust extension | `_rust.cpython-312-darwin.so` copied from the coordinator's worktree (8.5 MB). The build profile was not verified. Both policies use the same binary. |
| Database | pytest-django's SQLite test database, which is not a networked database |
| Command | `PYTHONPATH=. .venv/bin/python -m pytest tests/benchmarks/test_exposure_cost.py --benchmark-only -p no:cacheprovider -W ignore -q --benchmark-columns=min,median,mean,stddev,iqr,max,rounds --benchmark-sort=name`, run twice |

## Results

Medians are shown as run 1 / run 2. The IQR column is from run 1.

| Segment | Legacy median | Explicit median | Explicit vs legacy | IQR (legacy / explicit), run 1 |
| --- | --- | --- | --- | --- |
| HTTP GET: `as_view()`, mount, render, session write | 8.97 / 9.02 ms | 10.35 / 10.37 ms | about +1.4 ms (+15%) | 0.42 / 0.79 ms |
| WS connect + mount + disconnect, same session | 12.51 / 12.61 ms | 10.52 / 11.20 ms | 1.3 to 2.0 ms **faster** (−11% to −16%) | 0.69 / 0.50 ms |
| WS event: `increment` with re-render and reply | 3.89 / 4.00 ms | 7.84 / 7.31 ms | about +3.3 to +3.9 ms (1.8x to 2.0x) | 0.30 / 0.65 ms |
| Repeated mount, renderer setup + first render | warm hit 517 / 526 µs; cold miss 415 / 409 µs | 419 / 414 µs | same as a legacy cold miss, about 100 µs faster than a warm hit | warm 56, cold 43 / 35 µs |
| `ServerStateSession.save`, DB session | n/a | 1.48 / 1.48 ms | | 0.18 ms |
| `ServerStateSession.save`, cache session | n/a | 1.29 / 1.28 ms | | 0.10 ms |
| `ServerStateSession.load`, DB session, fresh store | n/a | 1.46 / 1.46 ms | | 0.13 ms |
| `ServerStateSession.load`, cache session, fresh store | n/a | 1.29 / 1.27 ms | | 0.11 ms |

## Interpretation

- **Events cost the most.** An explicit WebSocket event took about twice as
  long as a legacy one, 3.3 to 3.9 ms more. The explicit path reloads session
  authentication for each event and saves the declared server state for each
  event. The save alone measures about 1.3 to 1.5 ms. I did not profile the
  rest; the fresh-auth session load is the likely remainder, but that is
  unverified. For a latency-sensitive view, per-event persistence is the cost
  to look at before activation. It is a design cost, not noise: the IQRs are
  under 0.7 ms.
- **HTTP GET is about 15% slower.** The difference, about 1.4 ms, is close to
  the measured envelope save. That is consistent with the envelope write,
  bounded clone and validation replacing the legacy session write, but I did
  not profile it.
- **WebSocket mount was faster under explicit** in both runs. Legacy mount does
  work explicit mode skips, such as automatic context collection over every
  attribute and the shared render-cache round trip. I have not attributed the
  difference further.
- **Losing shared render-cache reuse cost nothing here.** For this template, a
  legacy warm cache hit was about 100 µs *slower* than building a fresh
  renderer. The in-memory backend returns an isolated copy through
  `deserialize_msgpack` (#1353), and parsed templates are already cached
  process-wide in Rust (`TEMPLATE_CACHE`). So the explicit path loses no
  parse. What it does lose is the cached VDOM baseline. The first event after
  a reconnect has no warm diff baseline, and these benchmarks do not measure
  that cost. A Redis backend adds network I/O to the legacy hit and was not
  measured.
- **Envelope I/O is mostly CPU.** Save and load differ by only about 0.2 ms
  between the SQLite DB session and the local-memory cache session. Most of
  the 1.3 ms is the bounded `clone_json_state` walk, which runs twice on save
  (projection, then envelope), plus JSON and validation. A networked database
  or cache adds its own round trip on top.

## Variance and limits

- The medians agree between runs to within about 7%. The largest spread was
  the explicit WS mount, 10.52 and 11.20 ms. Means and maxima are much less
  stable. Single-round outliers of 15 to 39 ms appear in both policies, and
  run 1's `legacy_cold` mean was 498 µs because of one 7.7 ms round. The
  conftest docstring (#2156) records the same behavior. Compare medians only.
- pytest-benchmark calibrates the number of rounds itself. The legacy HTTP GET
  got only 14 rounds per run, so treat its median as the least certain.
- The machine was loaded, with a load average of about 4.5. The same
  benchmark varies by roughly 11x across environments (#2156). Ratios between
  policies within one run are more meaningful than absolute numbers.
- These benchmarks exclude actors, Redis, networked databases, templates with
  `{% extends %}`, and ORM-backed context.
