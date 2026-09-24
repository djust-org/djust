- **ADR-038: an explicit `TenantMixin` view accepts a context processor that
  repeats its own tenant object.** A context processor that supplies a
  provider key is no longer a collision when its value is the identical object
  (`is`) the provider already supplied, e.g. a processor returning
  `request.tenant`, which an explicit tenant view binds to its own tenant. Any
  other value, including an equal but distinct `TenantInfo`, still raises.
  `djust.tenants.context_processor` re-resolves the tenant and so still
  collides with a resolved `TenantMixin` tenant under the explicit policy.
  Legacy views are unchanged.
