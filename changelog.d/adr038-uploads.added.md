- **ADR-038 E2-6: explicit views render upload progress through a registered
  `uploads` provider.** Under `exposure_policy="explicit"`, `UploadMixin`
  registers a render-only `djust.uploads` provider (in either MRO order), a
  projection of `get_upload_state()` that drops each entry's `writer_result`
  and raw client-supplied `client_name`; templates get the sanitized
  `safe_client_name` instead. An application `uploads` kwarg is a provider
  collision. The mount frame's `upload_configs` stays configuration only.
  Following decision D-g, entries in flight are not persisted and do not
  survive a reconnect: the remount starts with an empty upload manager, and
  `upload_resume` answers `not_found` for an explicit view without reading the
  resumable state store, so the client re-registers. Legacy views keep no
  `uploads` context and store-backed resume. 8 regression tests (13 parametrized cases) in
  `python/djust/tests/test_exposure_uploads.py`.
