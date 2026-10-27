# Bounded fingerprinting, template cache and widget updates

Measured locally on the 2.0 worktree on 2026-10-27. The baseline source was `ba5ada4f8`, using Python 3.12.9 and a release Rust extension. These are targeted microbenchmarks, not application latency or capacity claims. Other work was running on the host and samples varied; the raw results and scripts are retained in this worktree under `scratch/`.

| Workload | Before median | After median | What was measured |
| --- | ---: | ---: | --- |
| 100,000-item list, 20,000-node fingerprint budget | 21.238 ms | 3.255 ms | Seven alternating samples, walker only. |
| 500,000-item list, same budget | 183.800 ms | 4.437 ms | Seven alternating samples, walker only; full conversion/render excluded. |
| 1,000-row Rust view, unchanged template property | 5.7813 ms | 2.0835 ms | 40 samples; fragment/text fast-path usage 0/40 to 40/40. Template still renders in full. |
| 100 opted-in Chart hooks, 20 unchanged update sweeps | 24.01 ms | 5.43 ms | Median of five batches in jsdom using a simulated chart; redraws over 100 sweeps fell from 10,000 to zero. |

The 1,000-item fingerprint control was 0.125 ms before and 0.112 ms after. The direct Rust benchmark uses the worktree's original extension versus the rebuilt extension; extension hashes and raw samples are in `scratch/template-cache/`. A separate real LiveView property benchmark moved from 5.5659 ms to 3.7919 ms, but other Python changes were present between its runs, so it is not an isolated A/B comparison. The widget benchmark is not a browser or real Chart.js measurement. Baseline and resulting shipped client gzip sizes were 83,805 and 83,907 bytes, respectively.

The fingerprint walker collapses exhausted results and framework consumers conservatively invalidate them. Consequently oversized computed dependencies recompute on each access, and otherwise unchanged events may render. The fused conversion still processes all values when conversion is needed; bounded fingerprinting does not bound total conversion time.

Identical dynamic-template sources retain only the previous HTML and text-node index. Template rendering still executes, and fragment and loop caches are cleared, so mutable custom filters, registry changes and render-environment changes remain observable. Widget hooks preserve existing default lifecycle behavior; `shouldUpdate()` is opt-in and fails open. Chart attribute-based skipping is also opt-in via `dj-chart-update="attributes"`; `dj-chart-revision` can invalidate it when external state changes.

Local reproduction artifacts: `scratch/fingerprint-budget/bench.py` and `results-final2.jsonl`; `scratch/template-cache/bench.py`, `before.json`, `after.json` and extension hashes; `scratch/widgets-benchmark/bench.mjs`, `before.json` and `after.json`. These scratch artifacts are local to this worktree and are not tracked in Git.
