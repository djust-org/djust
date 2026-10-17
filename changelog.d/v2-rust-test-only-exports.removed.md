- **Four unused `djust._rust` functions.** `django_value_repr_enabled`,
  `unregister_raw_block_tag_handler`, `clear_raw_block_tag_handlers` and
  `clear_scope_tags` had no caller in djust's Python code or tests. They are no
  longer exported. The two `clear_*` functions remain as Rust-side test helpers.
