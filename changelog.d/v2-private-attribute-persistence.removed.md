- **BREAKING: private-attribute persistence.** A legacy-exposure view's
  `_private` attributes are no longer saved in the Django session
  (`liveview_<path>__private`, `__private_unsaved`) by the page GET or the
  page-POST fallback, nor restored from it. The fallback runs `mount()` on
  every POST instead, which rebuilds them. Declare what must survive between
  requests with `state(..., persist="server")`. `UploadMixin` no longer records
  `_upload_configs_saved`: its replay went with `enable_state_snapshot`. See
  [Migrating to 2.0](docs/website/guides/migrating-to-2.0.md#5-what-does-not-survive).
