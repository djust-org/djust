- **BREAKING: the Rust kill-switch setters.** `djust._rust` no longer exports
  `set_virtual_keyed_ops`, `virtual_keyed_ops_enabled` or
  `set_django_value_repr`, and `RustLiveView` no longer has
  `set_template_auto_call` / `template_auto_call_enabled`. What each one
  switched on is now unconditional: `[dj-virtual]` parents always get
  key-addressed VDOM ops, `{{ value }}` always renders as Django's `str()`
  does (the pre-1.2 `legacy_display` rendering is deleted), and LiveView
  templates always auto-call nullary callables. `DjustConfig.ready()` no longer
  sets the two process-global flags.
