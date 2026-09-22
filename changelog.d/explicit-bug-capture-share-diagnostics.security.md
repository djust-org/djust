- **Keep `bug_capture_share` failures value-free for staged explicit exposure.**
  When the share's re-render raised a `ValueError` or `RuntimeError`, the
  consumer sent `str(exc)` to the client; other exceptions were logged with
  their traceback. For a nonlegacy owner, only framework `ExposureError` text now
  reaches the client, and everything else gets the generic error and the
  value-free log line. Legacy behaviour is unchanged.
