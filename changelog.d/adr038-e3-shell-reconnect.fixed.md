- **Explicit page-shell children are reconstructed on WebSocket mount and
  reconnect (ADR-038 E3-6).** Children rendered outside `dj-root` by a
  `template_name` page were never registered on a live connection, so their
  events returned "Embedded view not found". An explicit root now renders its
  full page once at mount, as the HTTP GET does, restoring those children's
  stored state and routing their events. Legacy views are unchanged.
