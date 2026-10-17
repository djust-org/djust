- **Removed three kill switches and the duplicate flat settings (#3500).**
  Startup now fails with an `ImproperlyConfigured` that names the replacement
  when a project still sets one (`python/djust/_removed_settings.py`). It runs in
  `DjustConfig.ready()` rather than as a system check because ASGI servers do
  not run checks, and a silently ignored `DJUST_STATE_BACKEND` would drop to
  the in-memory backend in production.
  - `LIVEVIEW_CONFIG` / `DJUST_CONFIG` keys `template_auto_call`,
    `virtual_keyed_ops` and `django_value_repr`: the behaviour each one could
    switch off is now always on. Delete the key.
  - `DJUST_WS_COMPRESSION`: use `LIVEVIEW_CONFIG['websocket_compression']`.
  - `DJUST_VDOM_CACHE_ENABLED`, `DJUST_VDOM_CACHE_TTL_SECONDS`,
    `DJUST_VDOM_CACHE_MAX_ENTRIES`: use the `vdom_cache_*` keys of
    `LIVEVIEW_CONFIG['service_worker']`. Checks C301–C303 read only those keys
    now.
  - `DJUST_STATE_BACKEND` / `DJUST_REDIS_URL`: use
    `DJUST_CONFIG['STATE_BACKEND']` (`'memory'` or `'redis'`) and
    `DJUST_CONFIG['REDIS_URL']`. The `redis://` URL shorthand for the backend
    setting went with the flat alias.
  - System check `djust.C018` (LIVEVIEW_CONFIG keys removed in 1.3) is gone;
    those keys are still ignored.
  - The flag-default consistency script and its pre-commit hook are gone;
    `virtual_keyed_ops` was the only flag it tracked.
