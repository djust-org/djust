- **`exposure_policy = "legacy"` is deprecated.** A class that states it raises a
  `DeprecationWarning` when it is defined (or, when the policy is assigned to the class
  afterwards, the first time a view of that class is constructed), once for each class that
  states it. The message names the class and the replacement: remove the line, declare
  `state()` fields and supply the template's names in `get_context_data()`
  ([explicit exposure](docs/website/state/explicit-exposure.md)). The value still works in the
  2.0 pre-releases and is removed in 2.0.0. The views djust ships that state it
  (`PersistentLiveView`, the admin change list and change form, `TtydTerminalView`, the
  interactive dropdown example, `@live_view`) do not warn, and neither does a subclass that
  only inherits it from one of them.
