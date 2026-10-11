- **`{% masonry_grid %}` and `{% dashboard_grid %}` escape item content in the
  Rust template engine.** The component classes and the Django template tags
  escape each masonry item's `content` and each dashboard panel's `content`;
  the Rust-engine handlers wrote them unchanged. They now escape them the same
  way. The Rust engine passes list arguments to component handlers as data, so
  markup marked safe inside items is escaped there too; render such items with
  the component class or the Django tag. Tests in
  `python/djust/tests/test_rust_handler_item_escaping.py`.
