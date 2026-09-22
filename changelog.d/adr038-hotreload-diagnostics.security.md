- **Explicit views: hot-reload render failures are value-free.** In
  development, hot reload re-renders the mounted view, which runs its
  `get_context_data`. Its catch-all logged the exception, with the traceback,
  for any policy. An explicit view's failure now logs the value-free line.
  Legacy logging is unchanged.
