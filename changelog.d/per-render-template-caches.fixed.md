- **Two per-render costs of an unchanged template are paid once per template.**
  The JIT built its extraction source again on every render that serialized a
  model (the `{% extends %}` chain in Rust, every `{% include %}` read from
  disk, a SHA-256 of the result), and the dj-model allowlist walked the
  template and its includes, with the `{% load %}` hook for each, after every
  render (measured downstream at 7 ms and a third of a chat page's server CPU).
  With `DEBUG` off both are now kept per template source and template
  directories (`djust._render_caches`); the allowlist is cached in Rust by the
  exact source, never a short hash, and only after a successful walk. With
  `DEBUG` on every render builds them as before; a change of `DEBUG`,
  `TEMPLATES` or `INSTALLED_APPS` and a hot-reload template refresh clear them.
  Also: the JIT's template-hash cache was keyed by `id()` of the source (a
  reused id gave a new template an old hash, and it grew by one entry per
  render), and a name serialized as a QuerySet and later as a model read the
  QuerySet's cache slot as a serializer and fell back to every field. 18
  regression cases in `python/tests/test_per_render_template_caches.py` cover
  this and `djust.T026`.
