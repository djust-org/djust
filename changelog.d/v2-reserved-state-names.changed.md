- **One list of reserved `state()` names (#4070).** The names a `state()`
  field may not take are `djust._exposure.reserved_state_names()`: the exposure contract and the
  rendered-state names both read it (#4070), in place of the explicit path's
  reads of `live_view._FRAMEWORK_INTERNAL_ATTRS`. Its public names
  (`RESERVED_STATE_NAMES`: `request`, `args`, `kwargs`, `view_is_async`, the
  view settings) are the old set's public subset, plus the `LiveView` class
  namespace, `view` and the client-unsafe keys; the old set's underscore names
  are not repeated because no state field name can start with `_`. The
  change-detection skip list is `djust.change_detection.CHANGE_DETECTION_IGNORED`,
  with the same membership as before. No accepted or refused name changes.
  Refusing a reserved name when the class is created (#4070) is prepared
  behind a switch that is off pending a decision.
