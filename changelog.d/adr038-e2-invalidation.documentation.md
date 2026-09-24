- **`set_changed_keys()` is documented as the invalidation API for explicit-exposure views (ADR-038 E2-8).**
  Explicit context derived from declared `state()` fields, from plain attributes
  read in `get_context_data`, and from a provider's tracked keys already
  re-renders under the explicit policy exactly as under legacy; no-op turns stay
  a single `noop` frame. The per-turn change detector keeps walking the whole
  instance for explicit views on purpose, so no opaque dependency is dropped.
  New pins in `python/djust/tests/test_exposure_invalidation.py`.
