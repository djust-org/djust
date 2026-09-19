# ADRs 034–038: implementation sequence

This is an implementation ledger, not acceptance of the complete proposals.
The ADRs remain Proposed until their transport and security gates pass.

## Dependency order

1. ADR-038: typed per-instance state primitives, then separate rendering,
   server persistence, client snapshots and debug projections. Do not expose
   `exposure_policy="explicit"` until every automatic exporter consumes it.
2. ADR-036: shared strict parameter contracts, client collection metadata and
   equivalent validation across all dispatch paths.
3. ADR-035: authorized single-object form lifecycle, consuming the exposure
   contract. Additive public form-construction hooks can land independently.
4. ADR-034: typed component subscriptions, registry-only routing and optional
   client observations; verify two same-type components and stale identities.
5. ADR-037: shared checks and executable documentation throughout these stages;
   finish with catalogue/browser regression coverage and migration guidance.

## Implemented foundation

- Typed `state()` descriptor with instance-owned deep-copied defaults and lazy
  `default_factory`. A consumer mypy regression asserts both accepted reads and
  rejected assignments; nested changes are checked against content fingerprints.
- Public form construction hooks, empty-mapping binding, Django field initial
  values, prefixes and the `_create_form` compatibility bridge. Mount, reset,
  validation, submission and cache reconstruction use the hooks.
- Internal immutable exposure contracts compiled from state descriptors, with
  separate server/client/snapshot/debug projections, bounded JSON primitives,
  detached schema-checked restoration, and explicit inherited exposure grants.
  Defaults and unrelated properties are not evaluated during compilation.
- Internal server-session adapter with sync/async parity, exact supported Django
  storage implementations, required session/user/tenant/route binding, envelope
  expiry, and a total envelope resource budget. Cookie sessions and unreviewed
  custom backends are rejected. Actual database round trips are tested; this is
  not yet wired into HTTP, WebSocket, or actor LiveView persistence.

These changes **do not enable explicit exposure at runtime**. LiveView rejects
`exposure_policy="explicit"` and nondefault state exposure grants until automatic
exporters enforce the contracts. Legacy exposure and event policies are unchanged.
Managed `self.object`, strict event mode, scoped subscriptions and no-op client
observations remain pending. No generators should use the proposed APIs yet.

### Server persistence adapter boundary

`python/djust/_exposure_sessions.py` reuses Django's opaque session handle and
server storage rather than storing Python state inside the Rust view backend.
Supported concrete stores are Django database, cached database, cache, and file
sessions. The gate checks actual implementation identity, not `SESSION_ENGINE`
text, inheritance, or a self-asserted confidentiality flag. Custom stores require
a reviewed adapter; they currently fail closed. Deployment still determines
whether file/cache storage is shared across workers and durable.

The adapter validates current session identity before and after lazy loading,
requires explicit user/tenant/route identifiers, rejects legacy dictionaries and
changed schemas, and never restores attributes itself. Its digest is not a
signature: storage authenticity comes from the supported server-side session.
Transport integration must derive identifiers from trusted request/routing state,
perform fresh authorization, and remount on rejected restores. Permission checks
and managed-object resolution are **not** implemented by this storage helper.
Envelope expiry is separate from login-session expiry; Django remains responsible
for session cleanup. No client-storage fallback exists on write failure.

The initial codec only accepts bounded, exact JSON primitives. Additional codecs,
framework provider manifests, authenticated client snapshots, migration inventory,
and all runtime exporter integrations remain required before ADR-038 acceptance.

## Readiness audit

The milestone-audit checklist was applied to the two foundation boundaries.
There are no database schema changes, new Celery tasks, or auditlog registrations
in this slice. Existing state decorators, private field storage, context
descriptor discovery, form caches and legacy overrides were identified before
implementation. No new third-party runtime dependencies are introduced.

The remaining exposure work must inventory actual sinks, not just callers:
HTTP sessions, WebSocket/actor persistence, signed snapshots, reconnect/live
navigation, embedded state, time-travel, debug output and component providers.
Server-only data cannot be placed in readable signed cookies. Registration and
restore must fail closed without turning an unavailable provider into a legacy
reflection fallback. These are implementation gates, not completed guarantees.

## Verification boundaries

Tests are in `python/djust/tests/test_state_descriptor_contract.py`,
`test_state_descriptor_typing.py`, `test_form_hooks_adr035.py`,
`test_exposure_contract.py`, `test_exposure_policy_guard.py`, and
`test_exposure_sessions.py`. Run these with
the existing form, decorator, state and WebSocket regression suites. This slice
does not change browser JavaScript and does not establish browser, cross-worker,
or complete explicit-exposure parity.
