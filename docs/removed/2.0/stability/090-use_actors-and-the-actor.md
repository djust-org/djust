### `use_actors` and the actor system → the standard runtime

`use_actors = True` moved a view's state and rendering into a per-session Tokio
actor in the Rust extension. It was experimental, WebSocket-only, and could not
run under explicit exposure. djust `2.0.0` removed it, together with
`djust._rust.create_session_actor`, `get_actor_stats`, `SessionActorHandle` and
`SupervisorStatsPy`. The system checks `djust.V017` and `djust.V020`, which
only reported on actor views, are retired; their IDs are not reused.

A `LiveView` subclass that sets a truthy `use_actors` raises
`ImproperlyConfigured` when the class is created, naming the class. A value the
class-creation check cannot see (set on the instance, or assigned to the class
later) raises the same error right after `mount()`, on every transport. Delete
the attribute:

```python
# Before
class Dashboard(LiveView):
    use_actors = True

# After
class Dashboard(LiveView):
    ...
```

The view then runs on the standard runtime, which already runs one turn at a
time per connection. For work that should not block a turn, use
`start_async()` or `@background`. `use_actors = False` is accepted and has no
effect; delete it too. Importing one of the removed `djust._rust` names raises
`ImportError`.
