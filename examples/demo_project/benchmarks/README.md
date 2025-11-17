# djust Benchmarks

This directory contains performance benchmarks for djust features.

## JIT Auto-Serialization Benchmark

### Overview

The `benchmark_jit.py` script measures the performance impact of JIT auto-serialization on the rental dashboard.

### Current Status (Phase 6 Complete)

**Implementation Status:**
- ✅ Phase 1: Template variable extraction (Rust)
- ✅ Phase 2: Query optimizer
- ✅ Phase 3: Serializer code generation
- ✅ Phase 4: LiveView integration (foundation)
- ✅ Phase 5: Caching infrastructure
- ✅ Phase 6: Testing & documentation

**Integration Status:**
- The JIT infrastructure is complete and functional
- The rental dashboard currently uses manual serialization for stability
- Future work: Convert dashboard to use JIT auto-serialization

### Running the Benchmark

```bash
# From project root
cd examples/demo_project
DJANGO_SETTINGS_MODULE=demo_project.settings python benchmarks/benchmark_jit.py
```

**Note**: Requires Django environment setup with all dependencies installed.

### Expected Results

When JIT auto-serialization is fully integrated into the dashboard:

| Metric | Target | Status |
|--------|--------|--------|
| Cold cache (first request) | < 500ms | Pending integration |
| Warm cache (cached) | < 100ms | Pending integration |
| Template extraction | < 5ms | ✅ Implemented |
| Query reduction | > 80% | Pending integration |

### Current Baseline

**Dashboard with Manual Serialization:**
- Queries: ~500 (includes N+1 patterns)
- Response time: Varies based on data size
- Serialization: Manual (lines 170-201 in dashboard.py)

**Expected with JIT:**
- Queries: < 15 (with select_related/prefetch_related)
- Response time: 77% faster (based on architecture projections)
- Serialization: Automatic

### Future Work

To complete JIT integration:

1. **Remove Manual Serialization**:
   - Remove lines 170-201 from `djust_rentals/views/dashboard.py`
   - Let `get_context_data()` return QuerySets directly
   - JIT will automatically serialize based on template usage

2. **Verify Performance**:
   - Run `benchmark_jit.py` to measure actual improvements
   - Verify query count reduction (target: < 15 queries)
   - Verify response time improvement (target: < 100ms warm cache)

3. **Integration Tests**:
   - Update `test_jit_integration.py` to verify query optimization
   - Remove "Note: Dashboard uses manual serialization" comments
   - Enforce strict query count limits

## Contributing

When adding new benchmarks:

1. Create a new `benchmark_*.py` file
2. Follow the structure of `benchmark_jit.py`
3. Document expected results in this README
4. Ensure benchmarks can run standalone
5. Use `CaptureQueriesContext` to measure database queries
6. Use `time.time()` to measure execution time
7. Print clear, human-readable results
