- **A view with a public `@property` that raised lost its client
  `@debounce` / `@throttle` / `@cache` config.** The mount frame's scan read
  every public attribute on the instance, on the event loop, inside one `try`;
  a property that raised (an ORM read refuses to run there) dropped the whole
  `handler_config` and `cache_config` without a log line, so the browser sent
  every keystroke. The scan now skips class attributes that are descriptors
  other than functions, `staticmethod` and `classmethod`, so no property,
  `cached_property` or class-level component runs during it. Covered in
  `python/djust/tests/test_concurrent_liveview_cpu.py`.
