- **Redact `full_html_update` receiver failures for staged explicit exposure.**
  The signal is sent with `send`, so an application receiver's exception reached
  the runtime's catch, which logged it with its traceback for any policy. A
  nonlegacy owner now gets the value-free line; legacy logging is unchanged.
