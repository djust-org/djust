- **WebSocket mounts resolve the configured tenant.** The request djust builds
  for a WebSocket mount never carried `request.tenant`, which HTTP requests get
  from `TenantMiddleware`. Explicit request binding refuses a configured
  tenancy that is missing, so every explicit WebSocket mount failed in a
  project with a `TENANT_RESOLVER`. The socket request now resolves its tenant
  the way the middleware does. Header-based resolvers read the handshake's
  headers, and the request's own `META` is unchanged.
