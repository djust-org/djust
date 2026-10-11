- **`{% masonry_grid %}`, `{% dashboard_grid %}` and `{% audit_log %}` escape
  caller values in the Rust template engine.** The component classes and the
  Django template tags escape each masonry item's `content`, each dashboard
  panel's `content` and each audit-log column name (written into the cell's
  `class` attribute); the Rust-engine handlers wrote them unchanged. They now
  escape them the same way. The Rust engine passes list arguments to component
  handlers as data, so markup marked safe inside items is escaped there too;
  render such items with the component class or the Django tag. The
  `MasonryGrid` and `DashboardGrid` docstrings and the gallery examples now
  show text content. Tests in
  `python/djust/tests/test_rust_handler_item_escaping.py`.
