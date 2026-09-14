# Template pipeline performance batch

This batch follows the numeric sequence lookup optimization in PR #2819.
It targets four measured costs while retaining the public rendering and state
contracts. The baseline is main at `d71dd40b`.

## Changes and compatibility boundaries

1. **VDOM serialization uses one output buffer.** Descendants and escaped
   attributes append directly rather than allocating intermediate strings.
   Text and attribute escaping retain their different quote/nonbreaking-space
   rules, attribute ordering remains deterministic, and raw script/style text,
   comments, void elements and cached ignored subtrees keep their existing bytes.
   `render_with_diff()` still returns full hydrated HTML with its patches.
2. **Bare exact temporal values reuse their existing carrier.** After the normal
   live lookup and protection floor, a result identical to the original handle
   can reuse its snapshot for exact `date`, `timedelta`, and naive `datetime` or
   `time` values. Subclasses and aware values still convert live; arbitrary
   `tzinfo` implementations may be mutable. Dotted lookups still walk live,
   including `timestamp()` after a process timezone change. No shared protection
   callback is cached or bypassed.
3. **Filesystem includes memoize successful selection for a render.** The key
   includes ordered search directories, template name, registry namespace and
   generation. The existing process-wide parse cache still provides stable AST
   identity. Every new render checks the filesystem again, including changed
   files and new files earlier in the search order. Nested renders isolate and
   restore their caches; errors also unwind the scope. Custom loaders and
   loaders with uncached directories retain their per-call behavior. File edits
   during an already-running render are observed on the next render.
4. **Lazy tag-binding conversion avoids repeated imports and scalar checks.**
   Exact builtin scalar leaves return directly. Lazy promises, safe strings,
   subclasses, errors, named tuples and container-copy behavior retain the
   existing path. Django still implements `regroup`; there is no second grouping
   algorithm.

## Local measurements

Optimized release extension with debug symbols, Python 3.12, macOS ARM64.
Before and after use isolated worktree environments on the same machine.
Measurements exclude compilation and full-suite execution; they are local
microbenchmarks, not production latency guarantees. Median wall-clock times:

| Workload | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| 2,000 repeated naive datetime formats, retained state | 464.73 ms | 14.89 ms | 96.8% |
| 10,000-row table, tracked scalar update, full response | 48.25 ms | 10.26 ms | 78.7% |
| Serialization phase of that tracked update | 45.80 ms | 7.92 ms | 82.7% |
| 2,000 repeated filesystem card includes, retained state | 21.90 ms | 13.60 ms | 37.9% |
| Materialize 2,000 nested grouped bindings | 4.51 ms | 3.04 ms | 32.6% |

Django took approximately 26 ms for the repeated date shape and 18 ms for the
include shape. The date comparison uses a fixed naive datetime and a numeric
format; it does not establish equivalent gains for aware values or localized
formats. The binding microbenchmark includes result equality checking. Full
retained `regroup` moved only from about 30.1 ms to 29.3 ms; this small movement
should not be treated as a robust end-to-end speedup.

Native sampling identified repeated temporal conversion, including eager
`timestamp()` calls, before the date optimization. The tracked update explicitly
uses `set_changed_keys(["tick"])`; it already avoids rerendering the unchanged
table. Most of its remaining cost was full HTML serialization, not diffing.
Fixtures contain plain Python objects and lists, with no database queries.

The repository benchmark suite now covers repeated date formatting, filesystem
includes, and full hydrated HTML on tracked updates. The development torture
harness additionally checks randomized Django parity across direct rendering,
the template backend and retained Rust state.

## Candidates deliberately deferred

- **Skip unused context conversion:** it has substantial potential, but template
  variable extraction alone is not a complete dependency contract. Custom tags,
  dynamic includes, processors and whole-context consumers can observe keys that
  do not appear in the static template. A future change should introduce a
  conservative dependency model or lazy conversion boundary and test all those
  consumers before filtering context.
- **Cache protection callbacks or skip protection for primitive results:** the
  callback enforces the common model/queryset privacy floor at multiple sinks.
  Caching it can hide changes or failures within a render. This batch preserves
  every existing floor invocation and its replacement/failure behavior.
- **Eliminate full HTML after a patch:** the current return value supports
  recovery and hydration. Buffer sharing reduces its cost without changing that
  contract. A patch-only API would require separate caller/recovery design.

## Validation

Validation results are recorded in the PR. Targeted tests cover the boundaries
above, alongside the existing Django compatibility, bridge security, VDOM and
state-roundtrip suites. Timing benchmarks have no brittle speed thresholds.
