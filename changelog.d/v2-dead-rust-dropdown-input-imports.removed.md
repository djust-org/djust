- **Dead `RustDropdown` / `RustInput` imports in `djust.components.ui`.** The
  `Dropdown` and `Input` simple components tried to import Rust classes that
  the extension never registered, so the import always failed and both always
  rendered in Python. The try-imports and their `_RUST_AVAILABLE` flags are
  gone; rendering is unchanged.
