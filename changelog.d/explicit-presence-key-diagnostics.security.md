- **Redact `get_presence_key` failures at mount for staged explicit exposure.**
  Joining the presence group at mount logged the exception raised by an
  overridden `get_presence_key` for any policy. A nonlegacy owner now gets the
  value-free line; legacy logging and its WARNING level are unchanged.
