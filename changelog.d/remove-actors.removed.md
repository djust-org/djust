- **Removed the Tokio actor system and `use_actors`.** `use_actors = True` moved
  a view's state and rendering into a per-session actor in the Rust extension.
  It was experimental, WebSocket-only and could not run under explicit
  exposure. The whole path is gone: the `use_actors` view attribute, the actor
  branches in mount and event dispatch, the `uses_actors` /
  `dispatch_actor_event` / `uses_actors_for_mount` / `dispatch_actor_mount`
  transport hooks, `djust.validation.actor_handler_arguments`, the Rust
  `actors` module, and `djust._rust.create_session_actor`, `get_actor_stats`,
  `SessionActorHandle` and `SupervisorStatsPy`. System checks `djust.V017`
  (async strict handler on an actor view) and `djust.V020` (interactive
  component on an actor view) reported only on actor views and are gone too.

  A `LiveView` subclass that sets a truthy `use_actors` now raises
  `ImproperlyConfigured` when the class is created, naming the class. Delete
  the attribute: the view runs on the standard runtime, which already runs one
  turn at a time per connection, and `start_async()` / `@background` cover
  work that should not block a turn. `djust_audit` no longer prints an
  `Actors:` line or a `use_actors` config key.

  The Rust extension no longer depends on `tokio`, `pyo3-async-runtimes`,
  `bincode` or `parking_lot`, so the `RUSTSEC-2025-0141` (bincode) entry in
  `osv-scanner.toml` is removed.
