- **System check `djust.T027`: a JSX-style `<RustButton … />` template tag.**
  The tags are deprecated since 1.3 and removed in 2.0.0, and rendering one emits
  a `DeprecationWarning`. The check finds them in templates before the page is
  requested, as the deprecation policy asks of template-syntax deprecations.
  New cases in `TestT027RustJsxTags`.
