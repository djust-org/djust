- **Explicit views work with tenancy configured but no `TenantMiddleware`.**
  A project that sets `TENANT_RESOLVER` and resolves tenants per view through
  `TenantMixin`, instead of installing the middleware, got a refusal on every
  explicit request, because binding found no `request.tenant`. Binding now
  resolves the tenant on demand with the configured resolver. A resolver
  failure is still a refusal.
