- **Redact object-permission and action failures for staged explicit
  exposure.** A non-`PermissionDenied` error from a view's `get_object` or
  `has_object_permission` is still treated as denial, but its text is no longer
  logged for a nonlegacy view; the same applies to `@action` handlers, tutorial
  steps and `SimpleLiveView` template rendering. Legacy logging is unchanged.
