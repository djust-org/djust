- **The README, `docs/TEMPLATE_BACKEND.md`, `docs/TESTING.md` and the
  deployment guide now use the canonical module paths**
  (`djust.template.backend.DjustTemplateBackend`, `djust.state_backends`)
  instead of the `djust.template_backend` and `djust.state_backend` shims.
  djust 2.0 removes the shims; the canonical paths work on 1.x too.
