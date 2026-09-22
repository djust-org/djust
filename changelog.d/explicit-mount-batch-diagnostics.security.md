- **Keep `mount_batch` failures value-free for staged explicit exposure.**
  When a view in a `mount_batch` raised past `handle_mount`, the consumer logged
  the exception with its traceback and, under `DEBUG`, returned `str(exc)` to
  the client in the batch's `failed[]` entry, for any policy. The owner is now
  the class the entry names, resolved by the shared allowlist-first resolver; an
  unresolvable class or a nonlegacy owner gets the value-free log line and the
  generic `"mount failed"`. Legacy behaviour, including the `DEBUG` detail, is
  unchanged.
