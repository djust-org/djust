# Actor System - Current Limitations (Phases 1-4)

This document outlines the current limitations and incomplete features of the actor-based state management system as implemented in Phases 1-4.

## ✅ What Works (Fully Implemented)

1. **Actor Infrastructure**
   - `SessionActor` - Per-user session management
   - `ViewActor` - Per-view state management
   - Message passing with bounded channels (100/50/20 capacity)
   - Async actor lifecycle (create, ping, shutdown)

2. **PyO3 Integration**
   - Async Python → Rust calls via `pyo3-async-runtimes`
   - `SessionActorHandle` Python wrapper
   - `create_session_actor()` factory function
   - Type conversion helpers (Python dict → Rust HashMap)

3. **WebSocket Infrastructure**
   - Actor creation on session mount
   - Graceful shutdown on disconnect
   - Opt-in via `use_actors = True` flag

4. **Testing**
   - 5 Python integration tests (all passing)
   - Concurrent actor operations verified
   - Stress testing (20 rapid create/shutdown cycles)

## ⚠️ Known Limitations

### 1. Event Handling Not Implemented

**Location**: `crates/djust_live/src/actors/session.rs:162-169`

```rust
// Route to the first (and typically only) view
// In a full implementation, would need view identification in params
let view_handle = self.views.values().next()
    .ok_or_else(|| ActorError::not_found("No mounted views"))?
    .clone();

// TODO: In Phase 5, call Python event handler via PyO3
// For now, just re-render
```

**Impact**:
- Events trigger re-renders but do NOT call Python event handlers
- Cannot actually handle button clicks, form submissions, etc.
- Actor system is infrastructure-only at this stage

**Planned Fix**: Phase 5 - Full Python Integration

### 2. No View Identification System

**Location**: `crates/djust_live/src/actors/session.rs:148`

```rust
// Uses view_path as HashMap key
self.views.insert(view_path, view_handle);
```

**Impact**:
- Only one view per view_path allowed
- Mounting "app.Counter" twice will silently replace the first instance
- Cannot route events to specific views when multiple exist

**Planned Fix**: Phase 5 - Use UUID-based view IDs

### 3. No Supervisor/Cleanup System

**Status**: Not implemented (planned for Phase 6)

**Impact**:
- Sessions accumulate in memory indefinitely
- No TTL-based session expiration
- Dead actors cannot be detected or restarted
- No graceful application shutdown mechanism

**Workaround**: Manual cleanup on disconnect (implemented in `LiveViewConsumer.disconnect()`)

**Planned Fix**: Phase 6 - ActorSupervisor

### 4. Empty Template Initialization

**Location**: `crates/djust_live/src/actors/view.rs:54`

```rust
let backend = RustLiveViewBackend::new_rust(String::new());
```

**Impact**:
- ViewActor created with empty template
- First render will fail until template is set
- Templates should be loaded in mount phase

**Planned Fix**: Add `set_template()` method or pass template in constructor

### 5. Rust Unit Tests Don't Link

**Issue**: PyO3 linking errors in test mode

```
Undefined symbols for architecture arm64: "_PyBytes_AsString", ...
```

**Root Cause**: PyO3 `extension-module` feature conflicts with `cargo test` linking

**Workaround**: Python integration tests verify functionality (5/5 passing)

**Impact**: Cannot run `cargo test -p djust_live`

**Planned Fix**: Configure PyO3 test features properly or use `#[cfg(not(test))]` guards

## 📊 Current vs Target Architecture

### Current (Phases 1-4):
```
WebSocket → LiveViewConsumer → LiveView (Python) → RustLiveViewBackend
                                     ↓
                            SessionActor (infrastructure only)
```

### Target (Phases 5-8):
```
WebSocket → LiveViewConsumer → SessionActor → ViewActor → RustLiveViewBackend
                                                   ↓
                                          Python Event Handlers
```

## 🔄 Migration Path

### Phase 5 (Next): Full Python Integration
- Implement Python event handler callbacks via PyO3
- Add view identification system (UUIDs)
- Pass Python LiveView state to ViewActor
- Route events through actor system

### Phase 6: Supervision & Lifecycle
- Implement `ActorSupervisor`
- TTL-based session cleanup
- Health monitoring and metrics
- Crash recovery

### Phase 7: Testing & Optimization
- Benchmarks vs non-actor mode
- Load testing
- Memory profiling
- Fix Rust unit test linking

### Phase 8: Documentation & Migration
- Complete API documentation
- Migration guide for existing views
- Performance comparison report
- Best practices guide

## 🐛 Known Issues

### Silent View Replacement
When mounting the same view_path twice, the first instance is silently replaced:

```python
# First mount
await handle.mount("app.Counter", {})  # Creates view

# Second mount - REPLACES first!
await handle.mount("app.Counter", {})  # Silently replaces
```

**Workaround**: Use unique view paths or wait for UUID-based identification

### No Backpressure Visibility
Bounded channels have capacity limits (100/50/20), but no mechanism to detect when channels are full.

**Impact**: Messages may be dropped silently under high load

**Planned Fix**: Add metrics/monitoring in Phase 6

## 💡 Recommendations

### For Current Use (Phases 1-4)

1. **DO**: Use actors for testing infrastructure
2. **DO**: Experiment with actor lifecycle (create/shutdown)
3. **DO**: Benchmark concurrent session creation
4. **DON'T**: Enable `use_actors=True` in production yet
5. **DON'T**: Expect event handling to work through actors
6. **DON'T**: Mount same view_path multiple times

### For Production Use (Wait for Phase 5+)

- Event handling implementation complete
- View identification system working
- Supervisor for cleanup
- Performance benchmarks completed

## 📝 Testing Notes

**Python Tests**: ✅ All passing (5/5 in 0.15s)
- Basic lifecycle
- Concurrent operations
- Stress testing
- Stats retrieval

**Rust Tests**: ❌ Linking errors
- Unit tests cannot compile due to PyO3 extension-module conflicts
- Actor logic is sound (proven by Python tests)
- Needs test configuration fixes

## 📚 Related Documentation

- `ACTOR_STATE_MANAGEMENT.md` - Full 8-phase implementation guide
- `crates/djust_live/src/actors/README.md` - Actor system overview (if exists)
- `python/tests/test_actor_integration.py` - Test examples

## ⏭️ Next Steps

See `ACTOR_STATE_MANAGEMENT.md` Phase 5 for detailed implementation plan of:
- Python event handler integration
- View identification system
- State synchronization
- Complete event handling flow
