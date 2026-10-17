- **Nine more dead Rust try-imports in `djust.components.ui`.** The
  breadcrumb, button group, checkbox, list group, navbar, offcanvas,
  pagination, select and table simple components tried to import Rust classes
  the extension never registered, so the import always failed and they always
  rendered in Python or through their template. The try-imports and their
  `_RUST_AVAILABLE` flags are gone; rendering is unchanged.
