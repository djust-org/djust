- **ADR-038 D-e: password-type form input never leaves the server under the
  explicit policy.** A field with a `PasswordInput` widget, or a name in the
  serialization floor or `DJUST_SENSITIVE_FIELDS`, renders as empty in explicit
  templates, and an error message that echoes its value is replaced by a generic
  one, so the value is absent from frames, HTTP POST responses, the server
  session, the signed snapshot and debug output even when an invalid form
  re-renders. Opting such a field into `persisted_form_input()` raises
  `ExposureConfigurationError` when the class is defined (or, for a dynamic
  `get_form_class()`, at mount). An explicit view also no longer re-resolves a
  raw `model_pk` with an unscoped `objects.get`; `mount()`, which runs on every
  restore, must establish the model instance through the view's own lookup.
  Tests in `python/djust/tests/test_exposure_forms.py`.
