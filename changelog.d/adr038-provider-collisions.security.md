- **ADR-038 E2-1: framework mixins no longer write explicit context silently.**
  `TenantMixin`, `WizardMixin`, `DraftModeMixin`, `AudioMixin`, `PWAMixin`,
  `OfflineMixin` and `_sync_state_to_rust` register their keys as providers.
  Under `exposure_policy="explicit"` an application kwarg, a `context[...]`
  write, `update`, `setdefault`, `pop` or `del` after `super().get_context_data()`,
  or a context processor that collides with a provider key raises the existing
  "Explicit context provider collision" error instead of silently replacing the
  provider value or being replaced by it; an explicit view that supplies
  `csrf_token`, `DATE_FORMAT` or `TIME_FORMAT` itself is refused rather than
  used. An `_action_state` entry for a name no `@action` method declares is
  refused. Explicit wizards render `form_choices` only, without the flat
  `<field>_choices` aliases, which depend on runtime form fields and cannot be
  declared. Provider values stay render-only: they reach no server storage,
  frame, client snapshot or debug output. `_explicit_context_provider_keys` is
  now in `_FRAMEWORK_INTERNAL_ATTRS`. Legacy views are unchanged.
