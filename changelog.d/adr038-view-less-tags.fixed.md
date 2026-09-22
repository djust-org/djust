- **ADR-038 E2-2: `{% dj_activity %}`, `{% colocated_hook %}` and the form tags
  work in explicit views.** An explicit context never carries the raw `view`,
  so `{% dj_activity %}` silently registered nothing (the server never knew the
  activity was hidden), strict hook namespacing silently fell back to the bare
  name, and `live_form`/`live_field`/`live_errors`/`field_value`/`has_errors`
  rendered their "no FormMixin" error. They now resolve an explicit view from
  the render's active-view thread-local, without putting the view in context;
  legacy resolution is unchanged. The Rust engine has no handler for these tags,
  so they run where djust renders with Django's engine: embedded and sticky
  children. Tests in `python/djust/tests/test_exposure_provider_tags.py`.
