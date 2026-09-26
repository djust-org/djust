- **`djust.V004` no longer reports component-subscription callbacks (ADR-034).**
  A `@<menu>.on.<output>` callback, such as the documented
  `on_project_menu_selected`, was reported as "looks like an event handler but is
  missing @event_handler". The advice could not be followed: `subscribe()`
  refuses a callback that is also an event handler. V004 now skips any method
  that `is_component_subscription()` recognises.
