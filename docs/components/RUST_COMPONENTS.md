# Rust Components

Some of the stateless components in `djust.components.ui` render through a
native Rust implementation. You use them as ordinary Python components; the
Rust class is an implementation detail.

```python
from djust.components.ui import Button

btn = Button("Save", variant="primary", size="lg")
html = btn.render()
```

## What is Rust-backed

The extension module `djust._rust` registers 15 classes from
`crates/djust_components/src/simple/`: `RustAlert`, `RustAvatar`, `RustBadge`,
`RustButton`, `RustCard`, `RustDivider`, `RustIcon`, `RustModal`,
`RustProgress`, `RustRange`, `RustSpinner`, `RustSwitch`, `RustTextArea`,
`RustToast` and `RustTooltip`.

Each one backs the matching `*_simple.py` component in
`python/djust/components/ui/` through the component's `_rust_impl_class`
attribute. When that attribute is set, `Component.render()` builds the Rust
instance and returns its HTML.

The other `*_simple.py` components try to import a Rust class that the
extension does not register (for example `RustDropdown` or `RustInput`), so
they always render with their Python or template path.

## Removed in 2.0

djust 2.0 removed three Rust component APIs that were broken or never
reachable:

- **JSX-style template tags** such as `<RustButton id="b" label="Go" />`. The
  template engine now refuses any `<Rust…>` tag with a template error. Render a
  `djust.components.ui` component, or a component template tag, instead.
- **`djust.rust_components`**, the Python wrapper module. Its `Button`
  constructor did not match the compiled class and raised `TypeError`. Use
  `djust.components.ui.Button`.
- **The `djust_components::ui` and `djust_components::python` Rust modules**,
  which backed the JSX tags and an unregistered `_rust_components` module.

## Adding a Rust-backed component

1. Add a `#[pyclass]` struct under `crates/djust_components/src/simple/` and
   export it from `simple/mod.rs` and `lib.rs`.
2. Register it in `crates/djust_live/src/lib.rs` with
   `m.add_class::<djust_components::RustMyComponent>()?;`.
3. Add its stub to `python/djust/_rust.pyi`.
4. Point the Python component's `_rust_impl_class` at it, and keep the
   template fallback so the component still renders if the class is missing.

## See also

- [Component overview](COMPONENTS.md)
