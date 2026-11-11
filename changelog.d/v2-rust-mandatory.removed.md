- **BREAKING: the pure-Python fallbacks for a missing Rust extension.** djust
  2.0 requires its compiled extension. `import djust` now fails if it is
  missing, instead of warning "Performance will be degraded" and leaving
  `render_template`, `RustLiveView` and friends as `None`. About 45 guarded
  imports that silently switched features off without the extension are now
  plain imports: template-tag, filter and library bridging, scope and i18n
  hooks, the render environment, `{% markdown %}`, JIT serialization, dj-model
  allowlists and the Rust-backed simple components. `djust_doctor` and the CLI
  health check still report a broken install. The `jit_serialization` setting
  still selects the non-JIT serializer.
