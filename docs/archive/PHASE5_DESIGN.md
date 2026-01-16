# Phase 5 Design: Python Event Handler Integration

## Problem Statement

Currently, the actor system is infrastructure-only. When events arrive:
1. SessionActor routes to ViewActor
2. ViewActor re-renders without calling Python event handlers
3. Result: No actual event handling, just re-rendering

**Goal**: Enable ViewActor to call Python event handlers before re-rendering.

## Architecture Overview

### Current Flow (Non-Actor)
```
WebSocket Event → LiveViewConsumer → view.event_name(**params) → view.render_with_diff()
```

### Target Flow (Actor-Based)
```
WebSocket Event → LiveViewConsumer → SessionActor → ViewActor → Python handler → render
                                                          ↓
                                                    view.event_name(**params)
```

## Design Options Considered

### Option 1: Pass Callback Closure (❌ Rejected)
Store a Python callback function in ViewActor:
```rust
struct ViewActor {
    event_callback: Py<PyAny>,  // Python function
}
```

**Issues**:
- Requires storing Python references in Rust
- Complex lifetime management
- Need to pass new callback for each event

### Option 2: Async Callback via Channel (❌ Rejected)
Send event back to Python for handling:
```rust
// In ViewActor::handle_event()
let (tx, rx) = oneshot::channel();
python_caller.send(CallPythonEvent { event_name, params, reply: tx });
let result = rx.await?;
```

**Issues**:
- Extra round-trip defeats purpose of actors
- Adds latency
- Complicates error handling

### Option 3: Store Python View Instance (✅ Selected)
Store reference to Python LiveView instance in ViewActor:
```rust
struct ViewActor {
    python_view: Option<Py<PyAny>>,  // The LiveView instance
}
```

**Advantages**:
- Clean design - ViewActor owns the view
- Direct Python calls via PyO3
- Minimal latency
- Proper error propagation

## Detailed Design

### 1. ViewActor Changes

**Add Python view reference**:
```rust
pub struct ViewActor {
    view_path: String,
    receiver: mpsc::Receiver<ViewMsg>,
    backend: RustLiveViewBackend,
    python_view: Option<Py<PyAny>>,  // NEW: Python LiveView instance
}
```

**Add message to set Python view**:
```rust
pub enum ViewMsg {
    // ... existing messages
    SetPythonView {
        view: Py<PyAny>,
        reply: oneshot::Sender<Result<(), ActorError>>,
    },
}
```

**Implement event handler calling**:
```rust
async fn handle_event(
    &mut self,
    event_name: String,
    params: HashMap<String, Value>,
) -> Result<RenderResult, ActorError> {
    // Call Python event handler
    if let Some(python_view) = &self.python_view {
        Python::with_gil(|py| {
            let view = python_view.bind(py);

            // Get the handler method
            let handler = view.getattr(&event_name)?;

            // Convert params to Python dict
            let params_dict = PyDict::new_bound(py);
            for (key, value) in params {
                params_dict.set_item(key, value_to_python(py, &value)?)?;
            }

            // Call handler(**params)
            handler.call((), Some(&params_dict))?;

            Ok::<_, PyErr>(())
        })
        .map_err(|e: PyErr| ActorError::Python(e.to_string()))?;
    }

    // Sync state back from Python to RustLiveViewBackend
    self.sync_state_from_python()?;

    // Render with diff
    self.backend.render_with_diff_rust()
        .map(|(html, patches, version)| RenderResult {
            html,
            patches,
            version,
        })
        .map_err(|e| ActorError::Template(e.to_string()))
}
```

### 2. SessionActor Changes

**Update mount to set Python view**:
```rust
async fn handle_mount(
    &mut self,
    view_path: String,
    params: HashMap<String, Value>,
    python_view: Py<PyAny>,  // NEW: Python view instance
) -> Result<MountResponse, ActorError> {
    // Create ViewActor
    let (view_actor, view_handle) = ViewActor::new(view_path.clone());
    tokio::spawn(view_actor.run());

    // Set Python view
    view_handle.set_python_view(python_view).await?;

    // Update state and render
    view_handle.update_state(params).await?;
    let result = view_handle.render_with_diff().await?;

    self.views.insert(view_path, view_handle);
    Ok(MountResponse {
        html: result.html,
        session_id: self.session_id.clone()
    })
}
```

### 3. Python Bindings Changes

**Update SessionActorHandlePy.mount()**:
```python
async def mount(self, view_path: str, params: dict, python_view: Any):
    """
    Mount a view with Python instance.

    Args:
        view_path: Python path to LiveView class
        params: Initial parameters
        python_view: Python LiveView instance (for event handlers)
    """
    pass  # Implementation in Rust
```

### 4. LiveViewConsumer Changes

**Pass Python view to actor on mount**:
```python
async def handle_mount(self, data: Dict[str, Any]):
    # ... create view_instance ...

    if self.use_actors:
        # Create SessionActor
        self.actor_handle = await create_session_actor(self.session_id)

        # Mount with Python view instance
        result = await self.actor_handle.mount(
            view_path,
            params,
            self.view_instance  # Pass Python view!
        )
    else:
        # Non-actor mode (existing code)
        await sync_to_async(self.view_instance.mount)(request, **params)
```

## State Synchronization

### Problem
- Python LiveView stores state as instance attributes
- RustLiveViewBackend stores state as HashMap<String, Value>
- Need to sync: Python attributes ↔ Rust HashMap

### Solution

**After Python handler, sync state**:
```rust
fn sync_state_from_python(&mut self) -> Result<(), ActorError> {
    if let Some(python_view) = &self.python_view {
        Python::with_gil(|py| {
            let view = python_view.bind(py);

            // Get context_data (calls view.get_context_data())
            let context_method = view.getattr("get_context_data")?;
            let context_dict = context_method.call0()?.downcast::<PyDict>()?;

            // Convert to HashMap and update backend
            let mut state = HashMap::new();
            for (key, value) in context_dict.iter() {
                let key_str: String = key.extract()?;
                let rust_value = python_to_value(&value)?;
                state.insert(key_str, rust_value);
            }

            self.backend.update_state_rust(state);
            Ok::<_, PyErr>(())
        })
        .map_err(|e: PyErr| ActorError::Python(e.to_string()))
    } else {
        Ok(())
    }
}
```

## Error Handling

### Python Exception Propagation
```rust
Python::with_gil(|py| {
    let result = handler.call((), Some(&params_dict));

    if let Err(e) = result {
        // Convert PyErr to ActorError
        return Err(ActorError::Python(format!(
            "Error in {}.{}(): {}",
            view_path,
            event_name,
            e.to_string()
        )));
    }

    Ok(())
})
```

### Missing Handler
```rust
let handler = view.getattr(&event_name).map_err(|_| {
    ActorError::EventHandlerNotFound(format!(
        "Handler '{}' not found on {}",
        event_name,
        view_path
    ))
})?;
```

## View Identification (Separate Task)

For now, use view_path as identifier (one view per path limit).
Phase 5.2 will add UUID-based identification.

## Testing Strategy

### Unit Tests (Rust)
- Test ViewActor.set_python_view()
- Test event handler calling with mock Python object
- Test state synchronization

### Integration Tests (Python)
```python
async def test_actor_event_handling():
    """Test that events call Python handlers through actors"""
    class CounterView(LiveView):
        template_string = "<div>{{ count }}</div>"
        use_actors = True

        def mount(self, request):
            self.count = 0

        def increment(self):
            self.count += 1

    # Create actor and mount
    handle = await create_session_actor("test-session")
    view = CounterView()
    view.mount(None)

    # Mount with Python view
    result = await handle.mount(
        "test.CounterView",
        {},
        view  # Pass Python instance
    )

    # Trigger event
    result = await handle.event("increment", {})

    # Verify handler was called
    assert view.count == 1  # Python state updated!
    assert "<div>1</div>" in result["html"]
```

## Migration Path

### Phase 5.1 (This Task)
- Add Python view storage to ViewActor
- Add SetPythonView message
- Basic infrastructure

### Phase 5.2
- Implement view identification with UUIDs

### Phase 5.3
- Complete event handler calling
- Implement state synchronization

### Phase 5.4
- Update LiveViewConsumer integration

### Phase 5.5
- Full testing and documentation

## Security Considerations

1. **Python Code Execution**: Only execute methods on known LiveView instances (not arbitrary code)
2. **State Isolation**: Each ViewActor has its own Python view instance
3. **Error Boundary**: Python exceptions caught and converted to ActorError
4. **No Pickle**: Don't serialize Python objects - keep in memory only

## Performance Considerations

- **GIL Acquisition**: Use `Python::with_gil()` briefly during event handling
- **State Sync Overhead**: Only sync after handler, not on every render
- **PyO3 Call Cost**: ~1-2μs per call (acceptable)
- **Memory**: One Python view instance per ViewActor (acceptable)

## Open Questions

1. **Q**: What if Python handler modifies view instance but we need to re-mount?
   **A**: Re-mounting should create new ViewActor with new Python instance.

2. **Q**: How to handle async Python handlers (async def increment)?
   **A**: Phase 5 focuses on sync handlers. Async handlers in Phase 6+.

3. **Q**: Should we cache getattr lookups?
   **A**: Not yet - premature optimization. Measure first.

## Summary

This design enables ViewActor to call Python event handlers by:
1. Storing Python view instance as `Py<PyAny>`
2. Using `Python::with_gil()` to call handlers
3. Syncing state after handler execution
4. Proper error propagation

**Next Step**: Implement ViewActor changes (add python_view field and message).
