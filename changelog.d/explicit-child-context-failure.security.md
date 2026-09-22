- **Explicit children no longer fall back to an empty context.** When an
  explicit child's `get_context_data` failed, a non-sticky or lazy
  `{% live_render %}` logged the exception with its traceback and rendered the
  child with an empty context. Like the sticky path, it now raises a value-free
  `ExposureError`. Lazy render failures no longer log exception text for an
  explicit child. Legacy children are unchanged.
