- **The unregistered `djust_components::python` PyO3 module and the empty
  `complex` / `layout` component placeholders.** The deleted `python.rs` in
  the `djust_components` crate defined a `_rust_components` module and `RustInput` / `RustText` /
  `RustDropdown` / `RustTabs` wrappers that no build ever registered, so they
  were never importable. `djust_core` is now built as an `rlib` only; the
  extension has always been built from `djust_live`.
