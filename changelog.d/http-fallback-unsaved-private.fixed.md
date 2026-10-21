- **The HTTP event fallback rebuilds private attributes the session could not
  hold.** Private attributes are saved only when they are JSON (a service
  client, a dataclass or a stored callable is not), so the fallback's fresh
  instance lacked them and a handler that read one answered 500 with
  `AttributeError`. The session now records which private attributes were not
  saved; when there are any, the fallback runs `mount()` to rebuild them and
  then applies the saved public and private state on top. Background work that
  `mount()` queues is not run (the page already has its results). A view whose
  private state is all JSON is not re-mounted.
