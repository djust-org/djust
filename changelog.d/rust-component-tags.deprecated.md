- **JSX-style `<Rust…/>` component template tags.** Rendering a tag such as
  `<RustButton id="b" label="Go" />` now emits a `DeprecationWarning`: the tags
  are deprecated since 1.4 and removed in 2.0.0, where they become a template
  error. Use the matching `djust.components.ui` component. 3 regression cases
  in `python/tests/test_rust_component_tag_deprecation.py`.
