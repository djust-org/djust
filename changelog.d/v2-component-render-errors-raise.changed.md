- **BREAKING: a component template the Rust engine rejects now raises.**
  `Component` rendering used to catch any Rust render error and render the
  template again with Django's `Template`, so a template the Rust engine
  rejected rendered differently and silently. The error now propagates.
