- **`@action` errors no longer render exception text for explicit views
  (ADR-038 D-f).** `@action` recorded `str(exc)` as `{{ <name>.error }}`, so
  exception text reached the rendered HTML and patch frames. For nonlegacy views
  the recorded error is now the generic `"Action failed"`, unless the handler
  raised the new `djust.decorators.ActionError`, whose message is meant for the
  user and is recorded as written under every policy. Legacy views are
  unchanged. 1 regression test (4 cases) in
  `python/djust/tests/test_exposure_action_errors.py`.
