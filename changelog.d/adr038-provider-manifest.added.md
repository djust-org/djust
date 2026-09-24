- **ADR-038 E2-0: a context provider manifest for explicit views.** A framework
  provider now declares an immutable `ProviderContract` (the context keys it
  renders, the keys it tracks, its persisted and client keys, and a codec)
  next to `FieldExposure` in `python/djust/_exposure.py`, registered through a
  class-body `_djust_context_providers` tuple. `ExposureContract.from_view_class`
  folds the view's providers into the schema digest, so a provider change
  invalidates stored envelopes and the view remounts. The existing providers
  (component descriptors, `@action` state, streams, and the Rust bridge's
  `csrf_token`/`DATE_FORMAT`/`TIME_FORMAT`) are registered through it, and two
  providers may not declare the same key. A contract with no providers keeps its
  previous digest. Tests in `python/djust/tests/test_exposure_providers.py`.
