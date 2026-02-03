# djust Performance Analysis Report

Generated: 2026-02-02 23:33:38
Iterations per benchmark: 100

## Performance Targets

From ROADMAP.md:
- **<2ms per patch**: Target for simple updates
- **<5ms for list updates**: Target for list operations

## Summary

- ✅ **Passing** (<2ms p95): 21/21 benchmarks
- ⚠️ **Marginal** (2-5ms p95): 0/21 benchmarks
- ❌ **Failing** (>5ms p95): 0/21 benchmarks

## Template Rendering

| Benchmark | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Status |
|-----------|----------|----------|----------|----------|--------|
| render_simple_counter | 0.001 | 0.001 | 0.001 | 0.001 | ✅ Pass |
| render_list_10_items | 0.028 | 0.028 | 0.032 | 0.035 | ✅ Pass |
| render_list_100_items | 0.241 | 0.238 | 0.265 | 0.292 | ✅ Pass |
| render_list_500_items | 1.165 | 1.156 | 1.221 | 1.282 | ✅ Pass |
| render_nested_depth_5 | 0.002 | 0.002 | 0.003 | 0.003 | ✅ Pass |
| render_form_validation | 0.004 | 0.003 | 0.004 | 0.014 | ✅ Pass |

## VDOM Diffing

| Benchmark | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Status |
|-----------|----------|----------|----------|----------|--------|
| diff_no_changes_100_items | 1.076 | 1.068 | 1.152 | 1.178 | ✅ Pass |
| diff_single_attr_change | 0.004 | 0.004 | 0.005 | 0.005 | ✅ Pass |
| diff_list_append_100th | 1.077 | 1.072 | 1.154 | 1.176 | ✅ Pass |
| diff_list_prepend_to_99 | 1.087 | 1.079 | 1.142 | 1.166 | ✅ Pass |
| diff_list_toggle_all_100 | 1.206 | 1.204 | 1.268 | 1.318 | ✅ Pass |
| diff_list_reverse_50 | 0.552 | 0.540 | 0.612 | 0.884 | ✅ Pass |
| diff_form_validation_errors | 0.042 | 0.039 | 0.054 | 0.096 | ✅ Pass |

## Patch Serialization

| Benchmark | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Status |
|-----------|----------|----------|----------|----------|--------|
| serialize_patches_2 | 0.004 | 0.004 | 0.004 | 0.004 | ✅ Pass |
| serialize_patches_20 | 0.040 | 0.039 | 0.042 | 0.076 | ✅ Pass |
| serialize_patches_100 | 0.194 | 0.191 | 0.224 | 0.257 | ✅ Pass |

## State Backend

| Benchmark | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Status |
|-----------|----------|----------|----------|----------|--------|
| state_get | 0.001 | 0.001 | 0.001 | 0.001 | ✅ Pass |
| state_set | 0.001 | 0.001 | 0.001 | 0.004 | ✅ Pass |
| state_set_large_1000_items | 0.226 | 0.224 | 0.234 | 0.241 | ✅ Pass |

## Full Update Cycle

| Benchmark | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Status |
|-----------|----------|----------|----------|----------|--------|
| full_cycle_simple_counter | 0.007 | 0.007 | 0.009 | 0.009 | ✅ Pass |
| full_cycle_list_append | 1.300 | 1.283 | 1.373 | 1.497 | ✅ Pass |

## Bottleneck Analysis

### Slowest Operations (by P95)

1. **full_cycle_list_append**: 1.373ms (avg: 1.300ms)
2. **diff_list_toggle_all_100**: 1.268ms (avg: 1.206ms)
3. **render_list_500_items**: 1.221ms (avg: 1.165ms)
4. **diff_list_append_100th**: 1.154ms (avg: 1.077ms)
5. **diff_no_changes_100_items**: 1.152ms (avg: 1.076ms)

### Key Findings

- **VDOM diffing** is the primary bottleneck (Rust)
  - Consider optimizing the diff algorithm
  - Keyed lists can improve reorder performance

## Optimization Recommendations


## Raw Data

```json
{
  "render_simple_counter": {
    "avg_ms": 0.0006653685704804957,
    "p50_ms": 0.0006669724825769663,
    "p95_ms": 0.0007089984137564898,
    "p99_ms": 0.0008750066626816988,
    "min_ms": 0.0006249756552278996,
    "max_ms": 0.0008750066626816988
  },
  "render_list_10_items": {
    "avg_ms": 0.028484489303082228,
    "p50_ms": 0.028165988624095917,
    "p95_ms": 0.03229099093005061,
    "p99_ms": 0.035124976420775056,
    "min_ms": 0.02779200440272689,
    "max_ms": 0.035124976420775056
  },
  "render_list_100_items": {
    "avg_ms": 0.24141496134689078,
    "p50_ms": 0.2382500097155571,
    "p95_ms": 0.2646660141181201,
    "p99_ms": 0.2924999862443656,
    "min_ms": 0.22841600002720952,
    "max_ms": 0.2924999862443656
  },
  "render_list_500_items": {
    "avg_ms": 1.1651432904182002,
    "p50_ms": 1.1563329899217933,
    "p95_ms": 1.2207080144435167,
    "p99_ms": 1.282332988921553,
    "min_ms": 1.1232079996261746,
    "max_ms": 1.282332988921553
  },
  "render_nested_depth_5": {
    "avg_ms": 0.0023767209495417774,
    "p50_ms": 0.002333021257072687,
    "p95_ms": 0.00291600008495152,
    "p99_ms": 0.003249995643272996,
    "min_ms": 0.002207991201430559,
    "max_ms": 0.003249995643272996
  },
  "render_form_validation": {
    "avg_ms": 0.0035987593582831323,
    "p50_ms": 0.0034999975468963385,
    "p95_ms": 0.003583991201594472,
    "p99_ms": 0.013625016435980797,
    "min_ms": 0.003374996595084667,
    "max_ms": 0.013625016435980797
  },
  "diff_no_changes_100_items": {
    "avg_ms": 1.076382861356251,
    "p50_ms": 1.0680409905035049,
    "p95_ms": 1.1524169822223485,
    "p99_ms": 1.1778330081142485,
    "min_ms": 1.0388329974375665,
    "max_ms": 1.1778330081142485
  },
  "diff_single_attr_change": {
    "avg_ms": 0.004432470304891467,
    "p50_ms": 0.004417001036927104,
    "p95_ms": 0.004583009285852313,
    "p99_ms": 0.00462500611320138,
    "min_ms": 0.004249974153935909,
    "max_ms": 0.00462500611320138
  },
  "diff_list_append_100th": {
    "avg_ms": 1.0768829303560778,
    "p50_ms": 1.072082988684997,
    "p95_ms": 1.1537919926922768,
    "p99_ms": 1.1764590162783861,
    "min_ms": 1.0376250138506293,
    "max_ms": 1.1764590162783861
  },
  "diff_list_prepend_to_99": {
    "avg_ms": 1.087155020213686,
    "p50_ms": 1.078583998605609,
    "p95_ms": 1.1421250237617642,
    "p99_ms": 1.1664170015137643,
    "min_ms": 1.042416988639161,
    "max_ms": 1.1664170015137643
  },
  "diff_list_toggle_all_100": {
    "avg_ms": 1.2055749585852027,
    "p50_ms": 1.203916996018961,
    "p95_ms": 1.2675000180024654,
    "p99_ms": 1.3176249922253191,
    "min_ms": 1.1420410010032356,
    "max_ms": 1.3176249922253191
  },
  "diff_list_reverse_50": {
    "avg_ms": 0.5515991608262993,
    "p50_ms": 0.5404590046964586,
    "p95_ms": 0.6120839971117675,
    "p99_ms": 0.8841250091791153,
    "min_ms": 0.5193329998292029,
    "max_ms": 0.8841250091791153
  },
  "diff_form_validation_errors": {
    "avg_ms": 0.04162328928941861,
    "p50_ms": 0.03920801100321114,
    "p95_ms": 0.054249976528808475,
    "p99_ms": 0.09616700117476285,
    "min_ms": 0.038291997043415904,
    "max_ms": 0.09616700117476285
  },
  "serialize_patches_2": {
    "avg_ms": 0.003950779209844768,
    "p50_ms": 0.0039579754229635,
    "p95_ms": 0.004083995008841157,
    "p99_ms": 0.004125002305954695,
    "min_ms": 0.0038330035749822855,
    "max_ms": 0.004125002305954695
  },
  "serialize_patches_20": {
    "avg_ms": 0.039502379950135946,
    "p50_ms": 0.03879101132042706,
    "p95_ms": 0.042208004742860794,
    "p99_ms": 0.07550002192147076,
    "min_ms": 0.03783300053328276,
    "max_ms": 0.07550002192147076
  },
  "serialize_patches_100": {
    "avg_ms": 0.19395796000026166,
    "p50_ms": 0.19050002447329462,
    "p95_ms": 0.22420799359679222,
    "p99_ms": 0.25675000506453216,
    "min_ms": 0.18404200091026723,
    "max_ms": 0.25675000506453216
  },
  "state_get": {
    "avg_ms": 0.000766669400036335,
    "p50_ms": 0.0007500057108700275,
    "p95_ms": 0.000833999365568161,
    "p99_ms": 0.0009170034900307655,
    "min_ms": 0.0007079797796905041,
    "max_ms": 0.0009170034900307655
  },
  "state_set": {
    "avg_ms": 0.0012837289250455797,
    "p50_ms": 0.0012500095181167126,
    "p95_ms": 0.001334003172814846,
    "p99_ms": 0.004417001036927104,
    "min_ms": 0.001167005393654108,
    "max_ms": 0.004417001036927104
  },
  "state_set_large_1000_items": {
    "avg_ms": 0.22596878930926323,
    "p50_ms": 0.22387501667253673,
    "p95_ms": 0.2339589991606772,
    "p99_ms": 0.24083402240648866,
    "min_ms": 0.2220839960500598,
    "max_ms": 0.24083402240648866
  },
  "full_cycle_simple_counter": {
    "avg_ms": 0.007292151567526162,
    "p50_ms": 0.007166003342717886,
    "p95_ms": 0.009041017619892955,
    "p99_ms": 0.00937501317821443,
    "min_ms": 0.006667018169537187,
    "max_ms": 0.00937501317821443
  },
  "full_cycle_list_append": {
    "avg_ms": 1.299969179672189,
    "p50_ms": 1.2829999905079603,
    "p95_ms": 1.3732080115005374,
    "p99_ms": 1.4974999940022826,
    "min_ms": 1.2486659979913384,
    "max_ms": 1.4974999940022826
  }
}
```