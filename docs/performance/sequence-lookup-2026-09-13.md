# Numeric sequence lookup optimization — 2026-09-13

## Change

The template profiler found repeated `dir()` allocation and sorting when a live Python sequence lookup fell through from a numeric attribute miss to integer indexing. Exact built-in `list`, `tuple`, and `str` objects have no all-digit attribute names, so `name_exists_on` can answer this probe without constructing a directory.

The existing helper remains the single implementation for both lookup paths. Item access, attribute access, index conversion, auto-call behavior, and serialization protection are unchanged. Subclasses and nonnumeric names retain the general Django-compatible probe; there is no global cache, new dependency, feature flag, or alternative renderer.

## Repeated release-build comparison

Measured on macOS ARM64, Python 3.12.9, Django 5.2.16, with 2,000 slotted Python rows. Both extensions use opt-level 3, LTO and debug symbols; the baseline is `ec60cc53`. Each result is the median of 11 batches of four renders, with shuffled engine order. Compiled templates are warm. Retained-view timings exclude initial state conversion; backend timings include it. All outputs were compared with Django before timing.

| Workload | Path | Before ms | After ms | Reduction |
|---|---|---:|---:|---:|
| object_list_index | view | 31.39 | 15.85 | 49.5% |
| object_list_index | backend | 43.22 | 28.26 | 34.6% |
| object_list_index | django | 24.50 | 24.75 | -1.0% |
| wide_table | view | 74.54 | 57.89 | 22.3% |
| wide_table | backend | 85.69 | 68.78 | 19.7% |
| wide_table | django | 72.78 | 72.04 | 1.0% |
| no_index_control | view | 15.89 | 15.79 | 0.6% |
| no_index_control | backend | 27.92 | 27.86 | 0.2% |
| no_index_control | django | 7.67 | 7.71 | -0.6% |

The workload without numeric indices serves as a control. A prior run also showed the same direction of improvement. These are local microbenchmarks, not an end-to-end application latency claim.

## Compatibility and regression coverage

The characterization tests cover direct Rust rendering, `DjustTemplateBackend` and `RustLiveView`: exact sequence types, empty and out-of-range indices, leading zeroes, integer overflow, escaping, live values, subclass string-key overrides, numeric attributes, and custom `__dir__` descriptor exceptions. They pass on both the baseline and optimized builds. This preserves behavior; the separate A/B benchmark measures the performance change.

`tests/benchmarks/test_template_render.py::test_live_object_numeric_sequence_lookup` retains the profiled shape as a repeatable benchmark and verifies the full escaped output. The seeded torture workload checks another 1,500 output comparisons across the three djust paths.

## Reproduction evidence

The worktree `/private/tmp/djust-template-lookups` retains `scratch/lookup_bench.py`, `scratch/results/{before,after}-repeat.json`, extension hashes in the matching `.metadata.json` files, and logs under `context/terminal/`. The baseline extension remains separately installed in `/private/tmp/djust-template-torture`. Temporary profiling files are not part of the library.

Run the committed benchmark with:

```sh
uv run pytest tests/benchmarks/test_template_render.py::test_live_object_numeric_sequence_lookup --benchmark-only
```

Larger opportunities from the torture profile, such as avoiding full HTML serialization after small updates or reducing eager context conversion, require separate changes and validation. This optimization does not alter those contracts.

## Validation

The complete local `make test` run passed: 27,200 Python tests (486 skipped),
1,823 JavaScript tests, and both Rust phases run by that target. The 129 targeted
compatibility cases also passed independently before and after the change, and
all 1,500 seeded differential comparisons passed on the optimized build.
