# ADR-041: Pluggable state protection and a compliance profile

**Status**: Proposed
**Date**: 2026-10-02
**Deciders**: Project maintainers
**Related**:
- ADR-018: sticky-child state persistence (the first user of `enable_state_snapshot`)
- ADR-038: explicit context and state exposure (`persist="server"` / `persist="client"`)
- ADR-039: pluggable account backends (the pattern this ADR follows)
- ADR-040: vendored assets and SBOMs (no third-party code from CDNs)
- `python/djust/security/state_snapshot.py`: the signed client snapshot envelope
- `docs/website/guides/scaling.md`, `docs/website/guides/navigation.md`

---

## Release plan this ADR assumes

- **1.3 takes opt-in changes only.** New settings and APIs whose defaults
  preserve current behaviour.
- **2.0 takes the breaking changes.** After 1.3 the legacy exposure policy
  (views that rely on `get_context_data()` and `enable_state_snapshot`) is
  removed, together with the other legacy code, and the defaults below flip.
  This ADR says which decisions are 1.3 and which are 2.0.

## Context

`LiveView.enable_state_snapshot = True` (default `False`) turns on two
separate mechanisms for a legacy-exposure view. They are easy to mistake
for one.

1. **Session copy (server-side).** After each event the runtime saves the
   view's public state, its `_private` state and component state into the
   Django session (`liveview_<path>`, `liveview_<path>__private`,
   `..._components`). A plain reconnect, and Back, restore from it. It works
   across processes when the session store is shared, and the scaling guide
   relies on it. It is best effort and bounded (150 ms per save).
2. **Signed client token (browser-side).** The server sends the same public
   state to the browser as a blob signed with Django's `TimestampSigner`
   (`SECRET_KEY`, bound to the view slug and a keyed digest of the session
   key, with a TTL). The service worker stores it per URL (capped at 64 KB).
   On Back or `popstate`, the client echoes it and the server restores from
   it when the session copy did not restore. The envelope is **signed, not
   encrypted**: it cannot be forged, but the client can read it.

The session copy wins whenever it exists (`navigation.md`, "Back restores the
state the user left"). The client token therefore matters only when the server
copy is gone: the session expired or was evicted, the store was flushed, or
the save missed its 150 ms deadline (#3098, #3246).

Explicit-exposure views (ADR-038) already separate the two: a field is
`persist="server"` or `persist="client"`, and `persist="client"` requires
`client=True`, which declares the value browser-visible. Legacy views have no
such split. Every public attribute that survives the JSON round trip goes to
both places.

### What a regulated deployment found

A downstream application built to the FBI CJIS Security Policy on djust
reached the following without being asked, and works around each:

- **Legacy persistence is outside access control.** Under the legacy policy
  every GET and HTTP-POST event copied the whole `get_context_data()` (record
  lists, titles, search text) into the session, "outside access control and
  audit, surviving revocation and disposal". The application moved every
  view to the explicit policy, declares no `persist=` fields on staff pages,
  and runs a migration that purges the old session keys.
- **Sockets outlive sessions.** The application wrote its own registry so
  that, when a user's sessions end elsewhere (logout, disablement, MFA
  reset, idle expiry), every open view of that user re-checks immediately
  instead of waiting for its next event. The policy requires idle lock and
  session termination to apply to live connections as well.
- **Audit needs a hook.** Refusals (4403) must reach the application's audit
  trail; nothing signals them.
- **Cryptography requirements.** At-rest protection must use validated
  modules (FIPS 140-3) or AES-256. The deployer needs to supply the
  cryptographic module (a KMS or HSM), not accept a fixed library choice.

### What is wrong with the current design

- **Browser-readable state.** A legacy view's whole public state is readable
  in the browser (service-worker storage, plus the blob sent over the socket)
  whenever the feature is on. The only guard is `djust.C304`, which warns on
  attribute *names* such as `password` or `token`; it cannot see PII under
  other names.
- **Redundant exposure.** The session copy wins, so the client copy is a
  fallback exercised rarely but always present.
- **No way to say "never".** There is no project-wide switch that forbids
  state at the client, or that forbids legacy persistence.
- **Hard-wired cryptography.** The envelope is fixed to Django's signer.
  A deployment that must use a particular validated module cannot.
- **Live connections are not lifecycle-managed.** The framework has no
  `user_logged_out` receiver, no idle timeout for sockets, and no way to ask
  "re-check every socket of this user now".

### Requirement (maintainer, 2026-10-02)

djust should be usable for applications that must meet CJIS and HIPAA
obligations. djust cannot be compliant by itself (hosting, retention, audit
storage, key management, access reviews and personnel controls are the
deployer's). The framework's job is to make the compliant configuration the
easy one, refuse configurations known to be incompatible, supply the
mechanisms an application cannot build safely from outside, and say plainly
what it does not cover.

## Decision

### 1. A pluggable protection backend (1.3, opt-in)

Anything djust hands to the browser, and optionally what it writes to the
session, passes through a **protection backend**, configured the way Django
configures caches and storages:

```python
LIVEVIEW_CONFIG = {
    "snapshot_backend": {
        "BACKEND": "djust.security.snapshots.SignedBackend",   # default
        "OPTIONS": {},
    },
}
```

A backend implements two methods and a name:

```python
class SnapshotBackend:
    def seal(self, payload: bytes, *, context: SealContext) -> str: ...
    def open(self, token: str, *, context: SealContext) -> bytes: ...  # raises SnapshotRejected
```

`SealContext` carries the format version, the view slug, the keyed session
digest and the TTL. A backend must bind them (as signed data or as AEAD
associated data) so a token for another view or session fails. Anything that
cannot be opened is rejected and the server falls back to a normal
`mount()`, exactly as it does today for an expired or mismatched token.

djust ships two backends:

- `SignedBackend`: today's behaviour (`TimestampSigner`, readable by the
  client). The default in 1.3, so nothing changes for existing projects.
- `AesGcmBackend`: authenticated encryption with a key derived by HKDF-SHA256
  from `SECRET_KEY` (and each `SECRET_KEY_FALLBACKS` entry, for opening
  only). Requires the `cryptography` package, provided by a `djust[crypto]`
  extra. A system check is an **error** when it is configured and the package
  is missing, so a project cannot silently fall back to a readable token.

Third parties and deployers can supply their own, for example one that calls
a KMS or an HSM, or that uses a particular validated cryptographic module.
That is the reason for the interface: djust does not have to choose the
module, and does not claim one is validated.

The same backend can protect the server-side copy:
`"protect_session_state": True` seals the persisted payload before it is
written to the session, so state is encrypted inside the store regardless of
which store holds it. Default `False`.

The envelope used by explicit `persist="client"` fields goes through the same
module and backend. Explicit `persist="server"` values are unaffected by
`snapshot_backend` unless `protect_session_state` is set.

### 2. Transport choice for legacy views (1.3, opt-in)

```python
class Dashboard(LiveView):
    enable_state_snapshot = True
    snapshot_transport = "both"   # "both" (default in 1.3) | "session" | "client"
```

`"session"` means no token is sent to the browser and the service worker
stores no view state. `LIVEVIEW_CONFIG["snapshot_transport"]` sets the
project default. This is additive in 1.3: the default stays `"both"`.
It is validated by a system check, and is irrelevant to explicit-exposure
views, which declare `persist=` per field.

### 3. Live-connection lifecycle primitives (1.3, new APIs)

Each is opt-in and none changes existing behaviour:

- `djust.auth.revoke_user(user_id, *, reason)`: asks every open view of that
  user (across processes, over the channel layer) to re-run its fresh-principal
  check now; failure navigates to login and closes 4403.
- An optional `user_logged_out` receiver, enabled by
  `LIVEVIEW_CONFIG["close_sockets_on_logout"]`, that calls `revoke_user`.
- `LIVEVIEW_CONFIG["idle_timeout"]` (seconds, default `None`): a socket with no
  client event for that long is re-checked and closed. The limit is the
  deployer's policy; djust enforces it, it does not choose it.
- A `djust.auth_refused` signal, sent for every 4403 refusal (event, mount or
  server-originated turn) with the view class, user id if any, and a reason
  code, never the request body or state. Deployers connect it to their audit
  trail.

### 4. `compliance_profile` (1.3, opt-in)

```python
LIVEVIEW_CONFIG = {"compliance_profile": "cjis"}   # "hipaa", ["cjis", "hipaa"], or None
```

A profile does two things: it sets defaults that are safe to set, and it turns
configurations known to conflict into **system-check errors**, so
`manage.py check`, `manage.py test` and startup refuse them. It never claims
conformance. Both profiles share a base:

| Control family | Effect |
|---|---|
| Re-authorization | `reauth_on_event` is forced on; `close_sockets_on_logout` is forced on. |
| Session lock and lifetime | `idle_timeout` and `SESSION_COOKIE_AGE` must be set explicitly (the profile checks they are set, not what the values are). |
| State at the client | `snapshot_transport` must be `"session"`; explicit `persist="client"` fields are errors unless `snapshot_backend` is not `SignedBackend`. |
| State at rest | `SESSION_ENGINE` must not be `signed_cookies` or `locmem`. The deployer must set `LIVEVIEW_CONFIG["protection_attestation"]` to a free-text description of the control that protects the store (database or Redis encryption, or `protect_session_state` with a named backend); `djust_audit` prints it. djust cannot verify it. |
| Legacy exposure | Views using the legacy policy are errors. In 1.3 the profile therefore requires `exposure_policy = "explicit"` everywhere; in 2.0 this check is moot. |
| PII in view state | `C304` becomes an error. |
| Transport | `SECURE_SSL_REDIRECT`, secure and HttpOnly session and CSRF cookies, `SameSite`, HSTS; the WebSocket Origin check must be strict (host **and** port, see Open questions). |
| Debug | `DEBUG` on, the debug panel script and `client-dev.js` are errors. |
| Third-party code | No external asset origins (ADR-040's `DJUST_ALLOW_EXTERNAL_ASSETS` is an error). |
| Audit | At least one receiver is connected to `djust.auth_refused`. |
| Logging | The log-exposure pin already forbids values in logs; the profile errors if the `djust` logger is configured to emit request or state data. |

Out of scope, stated in the profile documentation: audit-trail storage,
integrity and retention, encryption at rest for the database and Redis and
key management, backups, hosting, personnel and access reviews, incident
response. The profile is a floor, not a certification. The documentation does
not use the words "compliant" or "certified" for djust itself.

### 5. 2.0 changes (breaking, decided in principle here)

When the legacy exposure policy is removed:

- `enable_state_snapshot`, `snapshot_transport`, `PersistentLiveView`, the
  `liveview_<path>` session keys and the legacy restore path go. State is
  persisted only through declared `state(..., persist=...)` fields.
- State never reaches the browser unless a field declares `persist="client"`
  with `client=True`. That path always goes through `snapshot_backend`, and
  the default backend becomes `AesGcmBackend` (with `cryptography` a core
  dependency, or the default falls back to a clear error: decided in 2.0).
- The profile's "legacy exposure" row disappears.

There is no project-wide "persist everything" mode. Because legacy views are
leaving, a global switch would be a short-lived feature that persists every
public attribute, including credentials and PII, with only a name heuristic
to guard it. Projects that want to persist a group of legacy views in 1.3
use `PersistentLiveView`.

## Consequences

**Positive**
- A deployment that must use a particular validated module, KMS or HSM can
  plug it in without a fork.
- Nothing changes for existing 1.3 projects until they opt in.
- Regulated projects get checkable errors, a place to record the controls
  djust cannot verify, and live-connection primitives they would otherwise
  build themselves (and sometimes get wrong).
- 2.0 gets a smaller surface: one persistence model, one envelope path.

**Negative / risks**
- A new public interface (`SnapshotBackend`) to keep stable, and a new
  optional dependency.
- `revoke_user` needs a cross-process mechanism (channel layer groups per
  user). Its delivery is best effort: a process that is down misses it and
  catches up at its next event or tick.
- The profile makes a project's `manage.py check` stricter; a project that
  enables it will fix configuration before it can start.
- `protect_session_state` adds seal/open cost to every save and restore.
- Until 2.0, legacy views keep the ability to put state in the browser.

**Neutral**
- Existing `SignedBackend` tokens keep working in 1.3.

## Alternatives rejected

1. **Hard-code AES-GCM and drop the signer.** Cannot satisfy a deployment that
   must use a specific validated module, and breaks existing tokens in 1.3.
2. **Keep the signed token and only warn.** Leaves regulated data readable in
   the browser and relies on a name-based heuristic.
3. **Remove the client token now.** Drops Back-restore durability when the
   server copy is gone for deployments that have no durable store. It goes in
   2.0 for legacy views; explicit `persist="client"` stays as a deliberate,
   per-field choice.
4. **A global `state_persistence = "all"` setting.** Rejected above.
5. **A hosted "HIPAA mode" that disables features at runtime.** Opaque and
   unverifiable. System-check errors are visible, testable and fail at
   startup.

## Rollout

1. **1.3:** `snapshot_backend` and the two shipped backends, `snapshot_transport`,
   `djust[crypto]`, `PersistentLiveView` (already in review) with the
   readable-by-the-client caveat.
2. **1.3:** `revoke_user`, `close_sockets_on_logout`, `idle_timeout`,
   `djust.auth_refused`.
3. **1.3:** `compliance_profile` checks and documentation, `djust_audit`
   reporting of the profile and attestation.
4. **2.0:** the removals and default flips in Decision 5.

Each step carries tests that fail without the change: tampering and
cross-view/cross-session replay for each backend, a token never emitted with
the session transport, each profile error case and the clean case, and
`revoke_user` closing a socket on another process.

## Open questions

1. **`cryptography` as an extra or a hard dependency in 2.0?** Extra in 1.3
   (maintainer: acceptable).
2. **Origin check strictness.** The WebSocket Origin validation compares host
   only (the port is ignored). The profile wants host and port; that is a
   behaviour change for deployments behind port-mapping proxies and needs its
   own setting.
3. **Service-worker store on logout.** `33-sw-registration.js` (~line 277)
   appears to clear stored entries when the identity changes or disappears.
   This needs a test before the profile can rely on it.
4. **Idle semantics.** What counts as activity for `idle_timeout` (a client
   event only, or also a heartbeat)? A server-originated re-check must never
   extend the idle clock.
5. **Concurrent session writes (#3347).** The post-event state save writes the
   whole session dict, so a concurrent change to another key can be lost and a
   deleted key can come back (reproduced for legacy views; for explicit views
   only when a writer other than the turn's own request object changes the
   session). `idle_timeout` and `protect_session_state` must not depend on the
   session carrying their clock or marker until that is fixed; a fix that
   saves only djust's own keys on a fresh load is the likely route.
6. **Standards mapping.** The profile table names control families
   generically. Whether to publish a mapping to specific CJIS Security Policy
   and HIPAA Security Rule sections needs review by someone qualified to make
   that claim.
