- **System check `djust.C027`.** It reported a `view_state_in_session` value
  that was not a bool, and is retired with the setting. Nothing emits it;
  remove it from `suppress_checks` or `SILENCED_SYSTEM_CHECKS`.
