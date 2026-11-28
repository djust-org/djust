# Component benchmark

`scripts/bench_components.py` (`make benchmark-components`) measures each layer
of djust on its own, records the results in one JSON file, compares two runs,
and profiles any case. Use it to pin a regression or a hotspot to the layer it
lives in; use `make benchmark-transports` for complete turns over each
transport and `make benchmark-rust` for the Criterion kernels.

## Suites

| suite | what one op is |
|---|---|
| `tag` | a `{% load djust_components %}` tag through the `djust` template backend: compile and render, and render of a compiled template |
| `component` | construct and `render()` each Python component in the gallery, on its first example |
| `render` | Rust template engine: cold (new `RustLiveView`, parse, render) and warm, for a 1,000-row table and a page of 30 includes; the table through the `djust` backend |
| `vdom` | `diff_html` (parse both documents and diff) and a `render_with_diff` turn, with Rust's own render / parse / diff / serialize split (`rust_*` fields) |
| `page` | a full-page GET through middleware and URLconf, and a socket mount of the page it served; inline-template workloads plus `includes`, `plain-includes` and `big` file templates (the shape of #4363) |
| `turn` | one event turn that changes one value, per workload over the WebSocket, and the table over SSE and the HTTP fallback |

Every case reports p50 / p95 / mean wall time, CPU per op, and peak memory one
op allocates (`tracemalloc`, a separate untimed pass). `--memory` adds what one
open page retains.

## Recording and comparing

```bash
make benchmark-components JSON=before.json            # all suites, 30 rounds
# ... change something, rebuild (release) ...
make benchmark-components COMPARE=before.json         # % change per case, marked at >= 10%
make benchmark-components SUITES=page CASES=includes  # a subset
```

## Profiling

```bash
make benchmark-components CASES=page/includes PROFILE=prof
```

writes one cProfile file per case (`prof/<case>.prof`, open with `snakeviz`)
and `prof/hotspots.txt`: the top functions by own time per case, and a list
across cases ranked by each function's share of each case's time, summed, so a
function that is 30% of five cases outranks one that is 90% of one. Rust
functions show as one built-in each (`djust._rust.diff_html`, `RustLiveView.render`).
To see inside them:

```bash
pip install py-spy
make benchmark-components CASES=vdom PYSPY=prof       # prof/pyspy.svg, --native frames
make benchmark-components SUITES=turn STATE_STORE=djust.state_store.DatabaseViewStateStore
make benchmark-components SUITES=turn SAVE_INTERVAL=0  # save before every reply
```

Profiling slows the timed rounds; record timings from a run without it.
