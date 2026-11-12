- System check `djust.V011` (a sticky child opted in to state snapshots and its
  parent did not) is retired with `enable_state_snapshot`. Nothing emits it;
  remove it from `suppress_checks` or `SILENCED_SYSTEM_CHECKS`.
