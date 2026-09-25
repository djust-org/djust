- **CodeQL code-scanning sweep.** Fixed every open non-cycle alert:
  - A strict handler's parameter-validation error reaches the client from
    `ParameterError.public_message` instead of `str(exc)` (the text was already
    framework-written and value-free).
  - Removed an unreachable explicit-child branch in `{% live_render %}` and a
    dead variable in the gallery catalogue.
  - The service-worker mount-metadata hook moved onto `djust._sw` as
    `applyMountMetadata`, so neither transport needs a `typeof` guard.
  - `ServerStateSession` derives its storage key through an overridable
    `_storage_key()`, which the child-state session overrides instead of
    replacing `key` after construction.
  - `StateProperty` is listed in `djust.decorators.__all__`.
  `py/cyclic-import` is excluded from CodeQL: every cycle it reported was
  guarded by a deferred import. The new
  `tests/test_no_module_level_import_cycles.py` fails on any cycle made of
  module-level imports alone.
