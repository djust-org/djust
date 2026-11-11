- **Removed the 1.x module shims (#3500).**
  - `djust.template_backend`: use `djust.template` (or
    `djust.template.backend.DjustTemplateBackend` in `TEMPLATES`). A
    `TEMPLATES` entry that still names
    `djust.template_backend.DjustTemplateBackend` now fails startup with an
    `ImproperlyConfigured` that names the replacement. The `djust new` and
    `djust init` scaffolds emit the canonical path.
  - `djust.state_backend`: use `djust.state_backends`.
  - `djust._view_resolution.is_view_import_allowed`: use
    `djust.security.mount.is_view_path_allowed`.
  - `djust.simple_live_view.LiveView`: use `SimpleLiveView`.
