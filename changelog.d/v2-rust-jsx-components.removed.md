- **BREAKING: JSX-style `<RustButton …/>` component tags and
  `djust.rust_components`.** The template engine no longer renders
  `<RustButton>`, `<RustInput>`, `<RustText>`, `<RustCard>`, `<RustAlert>`,
  `<RustModal>`, `<RustDropdown>` or `<RustTabs>` tags; any `<Rust…>` tag is now
  a template error that says it was removed. `djust.rust_components` is gone
  too: its `Button` constructor did not match the compiled class and raised
  `TypeError`. Use `djust.components.ui.Button` and the other
  `djust.components.ui` components, which keep their Rust renderers. The
  `djust_components::ui` Rust module that backed the tags is deleted, and
  `djust_templates` no longer depends on `djust_components`.
