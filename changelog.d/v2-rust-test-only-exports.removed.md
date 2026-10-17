- **Two unused `djust._rust` functions.** `django_value_repr_enabled` had no
  caller and is deleted. `clear_scope_tags` was only called from Rust unit
  tests, so it is no longer exported to Python and stays as a Rust test helper.
