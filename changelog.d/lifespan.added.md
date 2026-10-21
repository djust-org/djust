- **`djust.lifespan.with_lifespan(app, startup=..., shutdown=...)` answers the
  ASGI `lifespan` protocol.** Django's ASGI handler and channels'
  `ProtocolTypeRouter` do not, so a project had no supported place to start
  services bound to the server's event loop and stop them on shutdown. The
  wrapper runs the hooks on the server's loop (sync hooks through
  `sync_to_async`), reports a failed startup as `lifespan.startup.failed`, runs
  every shutdown hook in reverse even when one raises, and passes every other
  scope to the wrapped application. Hooks can also be registered with
  `@on_startup` / `@on_shutdown`. Documented in the deployment guide. 7 cases
  in `python/djust/tests/test_lifespan.py`.
