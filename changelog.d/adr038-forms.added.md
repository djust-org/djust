- **ADR-038 E2-3: `FormMixin` under the explicit policy.** A registered,
  render-only `djust.forms` provider (`FORM_PROVIDER` in `python/djust/forms.py`)
  gives explicit templates `form_data`, `form_choices`, `form_errors`,
  `field_errors`, `is_valid`, `success_message` and `error_message`, so explicit
  `validate_field`/`submit_form` render their errors over WebSocket and HTTP POST
  instead of rendering empty. Per decision D-e nothing of a form is persisted by
  default; `form_input = persisted_form_input("name", ...)` opts named
  non-sensitive fields into server persistence, restoring their input across
  reconnect and HTTP POST while every other field, all errors and `is_valid`
  reset. `model_pk`/`model_label` are neither rendered nor persisted for explicit
  views. Tests in `python/djust/tests/test_exposure_forms.py`.
