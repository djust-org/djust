- **BREAKING: a component template the Rust engine rejects now raises.**
  `Component` rendering used to catch any Rust render error and render the
  template again with Django's `Template`, so a template the Rust engine
  rejected rendered differently and silently. The error now propagates. An
  inline component template that uses `{% include %}` or `{% extends %}` keeps
  working: the Rust render now gets the project's template directories, which
  is what the Django re-render used to supply.
