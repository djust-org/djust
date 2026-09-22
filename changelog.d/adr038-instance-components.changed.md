- **An explicit view with a component assigned on the instance now fails at its
  first render (ADR-038 D-h).** `self.nav = Tabs()` in `mount()` or a handler
  was silently absent from an explicit view's context. The first explicit render
  now raises `ExposureConfigurationError` naming the attribute and saying to
  declare the component at class level; the message carries no values. There is
  no automatic discovery. Context-processor attributes the HTTP POST path
  injects are not flagged. Legacy views are unchanged.
