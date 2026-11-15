- **`Component.update()` no longer raises on a non-string `__dict__` key (#4143).** It rebuilt
  a Rust-backed component's instance by walking its own `__dict__` and called `startswith` on
  every key; a key that is not a `str` (not an attribute name) raised `AttributeError`. It now
  skips such a key, as every walk over a view's `__dict__` does since #4143. New cases in
  `python/djust/tests/test_component_update_non_string_key_4143.py`.
