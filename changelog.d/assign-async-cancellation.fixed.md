- **Cancelling an `assign_async` loader now cancels it.** The async runner
  caught `BaseException`, so `cancel_async()` or view teardown was swallowed:
  the task finished normally and the attribute became an errored `AsyncResult`
  holding the `CancelledError`. The runners now catch `Exception`, so a loader's
  own failure is still surfaced, while cancellation (and `KeyboardInterrupt` /
  `SystemExit` in the sync runner) propagates and the attribute stays pending.
  Found by CodeQL `py/catch-base-exception`.
