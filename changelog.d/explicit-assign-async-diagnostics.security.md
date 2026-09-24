- **Redact `assign_async` loader failures for staged explicit exposure.** A
  failing loader's exception text was logged for any policy. The runner now
  logs the value-free line for a nonlegacy view; legacy logging is unchanged.
