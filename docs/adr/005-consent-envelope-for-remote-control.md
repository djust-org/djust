# ADR-005: Consent Envelope for Remote Control

**Status**: Proposed
**Date**: 2026-04-11
**Deciders**: Project maintainers
**Target version**: v0.5.x (lands with multi-user `broadcast_commands`)
**Related**: [ADR-002](002-backend-driven-ui-automation.md), [ADR-003](003-llm-provider-abstraction.md), [ADR-004](004-undo-for-llm-driven-actions.md)

---

## Summary

Multi-user LiveView scenarios — support/assist sessions, instructor/student classrooms, accessibility caregivers, pair debugging — all converge on the same primitive: **one user's session wants to drive another user's session**. At the transport layer, this is technically already possible today (whoever owns the server process can send any message to any connected client). At the product layer, that's exactly the wrong default. Users must *explicitly* grant control, for a bounded scope, for a bounded time, with a persistent audit trail, and with an instant off-switch.

This ADR proposes the **consent envelope**: a framework primitive that issues a scoped, time-limited, revocable token granting one session the right to push JS Command chains (and, at higher trust levels, call event handlers) on another session. Every op executed under an envelope is checked against the envelope's scope, logged to an append-only audit trail, visible in real time to the subject user, and revocable with one click. The mechanism is composable with `broadcast_commands` so classroom/workshop scenarios use the same machinery as one-on-one support handoffs.

The design goal is that a hostile controller (or a legitimate controller with bugs) cannot do anything the subject hasn't explicitly authorized, and the subject can always see what's happening and stop it.

## Context

### Why this needs its own ADR

[ADR-002](002-backend-driven-ui-automation.md) enumerates "remote assistance / support handoff" as a use case and sketches a consent envelope in a ~20-line section. That's enough to frame the problem but nowhere near enough to implement it safely. Security surfaces this large need explicit design:

- **The failure modes are asymmetric**. A bug in tutorials makes a tutorial look weird. A bug in remote control leaks data, executes unauthorized destructive actions, or lets a support agent read someone's private messages. The cost of getting this wrong is high enough that it needs to be thought through before code is written.
- **The threat model is adversarial**. Unlike tutorials, where the only actors are the framework and the user, remote control has three actors: framework, controller, and subject. Each has different interests, and the design has to be correct against any two of them being buggy or malicious.
- **Legal and compliance implications.** "One user accepted another user's control" is an auditable event in many regulated industries. Healthcare, finance, government — any sector with compliance requirements needs the audit trail to be tamper-evident and queryable. That's a framework concern, not an app concern.
- **UX carries security**. A consent dialog that's easy to click through is not consent. A consent flow that's too onerous gets ignored. The design has to balance friction and clarity, which is a design problem in itself.

### Current state of the art

Three existing patterns that consent envelopes are intentionally *not*:

1. **Screen sharing** (Zoom, TeamViewer, browser-based cobrowsing): the controller sees the subject's screen and can move a virtual cursor. This is a **display-layer** primitive — the subject's app doesn't know anything is happening, the controller doesn't interact with real state, and the audit trail is "we recorded the screen." Djust's consent envelope is fundamentally different: the controller drives *real state transitions* through djust's event pipeline, and every op is a first-class audited action.
2. **Impersonation / "login as user"**: admin tools that let an operator assume a user's identity. This is a **session takeover** primitive — the operator *becomes* the user, and there's no subject-side consent or real-time visibility. Consent envelopes are the opposite: the subject stays in control, sees what's happening, and can revoke at any moment.
3. **OAuth-style scoped tokens**: the user grants an app permission to access a resource, then the app works offline. This is an **asynchronous delegation** primitive — the grant is long-lived and the user isn't watching. Consent envelopes are **synchronous and live**: the controller is driving the subject's UI *right now*, the subject sees every action as it happens, and the grant is short-lived by default.

The closest prior art is Intercom Inbox's "Cobrowse" feature and Salesforce's "Screen Share," but both are screen-sharing under the hood. What we're building is novel — true semantic remote control for a reactive framework. That novelty is both the opportunity and the responsibility.

## The envelope model

A **consent envelope** is a runtime object with the following structure:

```python
@dataclass(frozen=True)
class ConsentEnvelope:
    """One grant of remote-control authority from subject to controller."""

    envelope_id: str                          # short random id (UUIDv7 recommended)
    subject_session_id: str                   # whose UI is being driven
    subject_user_id: Optional[int]            # Django user pk, nullable for anonymous
    controller_session_id: str                # who's driving
    controller_user_id: Optional[int]         # Django user pk of the driver
    scope: FrozenSet[str]                     # set of allowed scope tokens
    granted_at: datetime                      # when the subject accepted
    expires_at: datetime                      # hard cutoff (absolute, not relative)
    max_ops: Optional[int]                    # optional cap on total operations
    ops_executed: int                         # counter incremented on each op
    reason: str                               # human-readable reason supplied by controller
    view_class: str                           # dotted path of the subject's LiveView class
    view_instance_pk: Optional[str]           # subject view's primary key if any
    revoked: bool                             # True once subject clicks "stop"
    revoked_at: Optional[datetime]
    revoked_by: Optional[str]                 # "subject", "timeout", "ops_cap", "framework"
```

Envelopes are immutable *except* for `ops_executed`, `revoked`, `revoked_at`, and `revoked_by`, which are updated in place. Every mutation is audit-logged.

## Scope vocabulary

The heart of the design is a small, carefully chosen set of scope tokens. Each scope grants a specific subset of operations. A controller requests one or more scopes; the subject accepts a subset; the envelope encodes the accepted set.

### Level 0: read-only

**`read`** — Controller can inspect the subject's view state via `get_state_snapshot()` but cannot modify anything. Used for "let me see what you're looking at" scenarios (support triage before actually helping, accessibility narration).

**Capabilities unlocked**: none on the client side; the controller gets a read-only snapshot delivered via `push_event` and can render it in their own UI. No JS Commands execute on the subject's browser.

**Why it's separate**: privacy. Reading state is a weaker permission than driving UI, and some scenarios need only this level.

### Level 1: visual attention

**`highlight`** — Non-destructive visual operations that draw the subject's attention to parts of the UI.

**JS Commands allowed**: `add_class`, `remove_class`, `transition`, `dispatch` (with a limited detail schema — see below), `focus` (for accessibility).

**JS Commands disallowed**: anything that changes form values, any `push` op, any `set_attr` / `remove_attr`.

**Why it's the default**: the most common support scenario is "look at this button" / "this is where the error message will appear." This scope is maximally useful for that while minimizing risk. An agent can point at things but cannot change anything.

### Level 2: navigation

**`scroll`** — Adds viewport manipulation.

**JS Commands allowed**: `highlight` scope plus `scroll_into_view`, `show`, `hide`, `toggle` (non-form elements only).

**Why it's separate from highlight**: some apps have collapsible sections with sensitive data. Being able to scroll and toggle visibility is more invasive than just highlighting. Apps can grant `highlight` alone to an accessibility assist tool, then upgrade to `highlight + scroll` for support.

### Level 3: input assistance

**`fill`** — Controller can populate form fields on the subject's behalf. Common for support scenarios where the agent helps a user complete a form.

**JS Commands allowed**: all of `scroll` scope plus `set_attr` (limited to the `value` attribute on form inputs — no `data-*`, no `dj-*`, no `id`/`class`), and `dispatch` with event types `input` / `change` (so the djust event pipeline sees the populated value).

**Operations disallowed**: destructive handlers (see `click`), arbitrary `set_attr`, any op that executes outside the DOM.

**Key restriction**: `set_attr` is whitelisted to `value` *only*. A controller cannot `set_attr("href", ...)` or `set_attr("onclick", ...)` — that would open an XSS / navigation-hijack hole.

**What subjects should know before granting this**: "The support agent can type into your form fields on your behalf, but cannot submit the form or click buttons for you." The consent dialog says exactly that.

### Level 4: action

**`click`** — Controller can trigger `@event_handler` methods on the subject's view.

**JS Commands allowed**: all of `fill` scope plus `push` ops that invoke event handlers.

**Operations disallowed**: handlers marked `@destructive` (from [ADR-002](002-backend-driven-ui-automation.md)) require a separate per-call confirmation from the subject, even within a `click`-scoped envelope. Non-destructive handlers execute immediately.

**What subjects should know**: "The support agent can click buttons and take actions on your behalf. You can see each action and stop at any time." Destructive actions prompt individually.

### Level 5: administrative

**`full`** — Controller has the full JS Command vocabulary plus destructive handlers without per-call confirmation.

**Only granted to**: subjects who explicitly enable this level. Most apps should *never* request this, and the consent dialog shows a much more prominent warning when requested. Reserved for scenarios like "admin fully assumes control to debug a bug."

**Why it exists at all**: because some legitimate scenarios need it (dev/debug, elevated support, accessibility for users who truly cannot interact). Making it impossible just pushes apps to implement ad-hoc bypass paths, which is worse.

### Scope vocabulary table

| Scope | Read | Highlight | Scroll/toggle | Form fill | Event handlers | Destructive ops |
|---|---|---|---|---|---|---|
| `read` | ✓ | | | | | |
| `highlight` | ✓ | ✓ | | | | |
| `scroll` | ✓ | ✓ | ✓ | | | |
| `fill` | ✓ | ✓ | ✓ | ✓ | | |
| `click` | ✓ | ✓ | ✓ | ✓ | ✓ (non-destructive) | |
| `full` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Scopes are **hierarchical**: granting `click` implies `fill` + `scroll` + `highlight` + `read`. The controller requests a level; the envelope stores a single scope token (the highest); the enforcement function checks any op against the token.

## Lifecycle

### 1. Request

The controller (usually a support agent, instructor, or admin) initiates a request from their own LiveView:

```python
# In SupportAgentView
class SupportAgentView(LiveView):
    def help_user(self, subject_session_id: str):
        request_id = self.request_control(
            subject_session_id=subject_session_id,
            scope=("highlight", "scroll", "fill"),
            duration=600,                                  # seconds
            max_ops=200,                                   # hard cap
            reason="Help you complete the account setup form",
        )
        self.pending_request_id = request_id
```

`request_control` does several things synchronously on the controller's side:

1. Validates the controller has permission to initiate requests (a new per-view Django permission `djust.can_request_control`).
2. Generates an envelope-pending record in the audit log (not yet an active envelope — just a pending request).
3. Sends a `push_event("djust:control_request", ...)` to the subject session via the standard server-push path. Payload:

```json
{
  "request_id": "env_abc123",
  "controller": {
    "user_id": 42,
    "display_name": "Alex from Support",
    "avatar_url": "https://...",
    "organization": "djust Support"
  },
  "scope": ["highlight", "scroll", "fill"],
  "scope_descriptions": {
    "highlight": "Point out elements on your screen",
    "scroll": "Scroll and show/hide parts of the page",
    "fill": "Type into form fields on your behalf"
  },
  "duration_seconds": 600,
  "max_ops": 200,
  "reason": "Help you complete the account setup form",
  "view_class": "accounts.views.SetupView"
}
```

### 2. Consent dialog (subject side)

The framework ships a default consent dialog that renders on the subject's LiveView when a `djust:control_request` event arrives. The default looks like:

```
┌────────────────────────────────────────────────┐
│ Alex from Support wants to help                │
│                                                │
│ Reason: Help you complete the account setup    │
│ form                                           │
│                                                │
│ Alex can:                                      │
│ ✓ Point out elements on your screen            │
│ ✓ Scroll and show/hide parts of the page       │
│ ✓ Type into form fields on your behalf         │
│                                                │
│ Alex cannot:                                   │
│ ✗ Click buttons or submit forms for you        │
│ ✗ Delete or change your data directly          │
│ ✗ See other tabs or other parts of djust       │
│                                                │
│ This session will last 10 minutes and end      │
│ automatically. You can stop it anytime.        │
│                                                │
│ [ Deny ]                    [ Allow 10 mins ]  │
└────────────────────────────────────────────────┘
```

Three requirements for the dialog:

1. **Cannot be auto-dismissed.** The dialog must persist until the subject explicitly clicks allow or deny (or the controller cancels, or 60 seconds pass with no response → automatic deny).
2. **"Deny" is the default button.** Keyboard Enter triggers deny, not allow. This protects against muscle-memory dismissals.
3. **The allow button has a 2-second delay before it becomes clickable.** Not a click-jacking-style delay (which would annoy legitimate users) but a brief moment to read the dialog before accepting.

Apps can override the dialog via a template tag `{% control_request_dialog %}` with their own markup, but the framework enforces the three requirements at the rendering layer and emits a deprecation warning if an override drops them.

### 3. Acceptance

When the subject clicks Allow:

```python
# The framework-provided event handler
@event_handler
def accept_control_request(self, request_id: str, **kwargs):
    """Subject accepts a control request."""
    envelope = self._accept_pending_request(
        request_id=request_id,
        accepted_by_session=self.request.session.session_key,
    )
    # Envelope is now active; push a visible indicator
    self.push_commands(
        JS.show("#control-indicator")
          .dispatch("djust:control_accepted", detail={
              "envelope_id": envelope.envelope_id,
              "controller_name": envelope.controller_display_name,
              "expires_at": envelope.expires_at.isoformat(),
          })
    )
    # Notify the controller's session
    self.push_event_to_session(
        envelope.controller_session_id,
        "djust:control_granted",
        {"envelope_id": envelope.envelope_id},
    )
```

Several things happen here:

1. The pending request is upgraded to an active envelope in the state backend.
2. The subject's UI gets a **persistent visible indicator** — by default a bar at the top of the page showing "Alex from Support is helping you. Stop session." with a timer. This cannot be hidden, styled away, or overridden to be invisible. The framework enforces its presence.
3. The controller's session is notified so their agent UI can transition from "waiting for consent" to "actively helping."
4. An audit log entry is written with both session IDs, both user IDs, scope, reason, and expiry.

### 4. Execution

During the envelope's lifetime, the controller calls:

```python
# Controller-side helper that only works when an envelope is active
self.push_commands_to(
    subject_session_id,
    chain=JS.add_class("highlight", to="#username-field"),
)
```

`push_commands_to` does the following on every call:

1. Looks up the active envelope for `(controller_session_id, subject_session_id)`. If none exists or is revoked/expired, raises `NoActiveEnvelope`.
2. For each op in the chain, checks the op against the envelope's scope using a whitelist-based validator (see below). Ops that fail the scope check are **dropped silently on the server** — they never reach the subject — and logged to the audit trail as `rejected_op`.
3. For any op that passes, checks `envelope.ops_executed < max_ops`. If exceeded, the envelope is auto-revoked with reason `ops_cap` and no op is sent.
4. Sends the filtered chain to the subject via the standard `push_event("djust:exec", ...)` path.
5. Increments `envelope.ops_executed` and writes an audit entry per op (timestamp, op name, op target, op args).

The subject's client-side auto-executor (from [ADR-002](002-backend-driven-ui-automation.md) Phase 1) runs the received ops exactly the same way it runs any other pushed chain. The subject can't tell from the client side whether the chain came from their own view or from a remote controller — but the persistent indicator (step 3 above) makes the controller's presence unmistakable.

### 5. Scope enforcement

The enforcement function is the heart of the design. It must be:

- **Fail-safe**: unknown ops are rejected by default, not allowed.
- **Stateless per call**: no side channels, no state-dependent decisions.
- **Small enough to audit visually**: a reviewer should be able to understand the whole function in one sitting.

Here's the proposed implementation:

```python
# python/djust/consent/scope.py

SCOPE_HIERARCHY = {
    "read": 0,
    "highlight": 1,
    "scroll": 2,
    "fill": 3,
    "click": 4,
    "full": 5,
}

# Ops allowed at each level. Each entry lists the ops NEWLY allowed at that
# level, not the cumulative set — that's computed by walking the hierarchy.
OPS_BY_LEVEL = {
    1: {  # highlight
        "add_class", "remove_class", "transition", "focus",
        "dispatch",  # subject to a sub-check on event types
    },
    2: {  # scroll
        "scroll_into_view", "show", "hide", "toggle",
    },
    3: {  # fill
        "set_attr",    # restricted to value-only — see below
    },
    4: {  # click
        "push",
    },
    5: {  # full
        "remove_attr",
        # "set_attr" is upgraded at this level to allow all attrs
    },
}


# Dispatch events allowed at highlight level. Anything not listed is rejected
# even at higher levels unless the app opts in explicitly.
ALLOWED_DISPATCH_EVENTS_HIGHLIGHT = {
    "assistant:narrate",
    "tutorial:step",
    "highlight:shown",
    "highlight:dismissed",
}


def check_op(op: tuple, envelope: ConsentEnvelope) -> Tuple[bool, str]:
    """Validate a single JS Command op against an envelope's scope.

    Returns (allowed, reason). reason is a short string for audit logging.
    """
    op_name, args = op
    level = SCOPE_HIERARCHY.get(_effective_scope(envelope.scope), 0)

    # Compute the cumulative allowed set for this level
    allowed_ops = set()
    for lvl in range(1, level + 1):
        allowed_ops |= OPS_BY_LEVEL.get(lvl, set())

    if op_name not in allowed_ops:
        return False, f"op {op_name} not in scope {level}"

    # Per-op restrictions that go beyond the level whitelist
    if op_name == "set_attr":
        if level < 5:  # fill and click levels
            attr = args.get("attr")
            if not isinstance(attr, list) or len(attr) != 2:
                return False, "set_attr args must be [name, value]"
            attr_name = attr[0]
            if attr_name != "value":
                return False, f"set_attr restricted to 'value' at level {level}, got {attr_name!r}"

    if op_name == "dispatch":
        event_name = args.get("event", "")
        if level < 3:  # highlight / scroll levels
            if event_name not in ALLOWED_DISPATCH_EVENTS_HIGHLIGHT:
                return False, f"dispatch event {event_name!r} not in highlight whitelist"
        elif level == 3:  # fill level
            if event_name not in (ALLOWED_DISPATCH_EVENTS_HIGHLIGHT | {"input", "change"}):
                return False, f"dispatch event {event_name!r} not in fill whitelist"
        # click and full allow any event name

    if op_name == "push":
        handler_name = args.get("event", "")
        # Block destructive handlers unless we're at full scope
        if level < 5 and _is_destructive(envelope.view_class, handler_name):
            return False, f"push to destructive handler {handler_name!r} requires 'full' scope"

    return True, "ok"


def _effective_scope(scope_set: FrozenSet[str]) -> str:
    """Return the highest scope token in the set."""
    max_level = max(SCOPE_HIERARCHY.get(s, -1) for s in scope_set)
    for token, level in SCOPE_HIERARCHY.items():
        if level == max_level:
            return token
    return "read"  # fallback


def _is_destructive(view_class: str, handler_name: str) -> bool:
    """Look up whether a handler is marked @destructive on the named view class."""
    try:
        cls = import_string(view_class)
    except ImportError:
        return True  # fail safe: unknown class → treat as destructive
    handler = getattr(cls, handler_name, None)
    if handler is None:
        return True  # fail safe: unknown handler → treat as destructive
    return getattr(handler, "_djust_destructive", False)
```

Key properties:

- The function is **~60 lines**, reviewable at a glance.
- Every rejection has a logged reason.
- Unknown ops and unknown handlers fail safe (rejected, treated as destructive).
- Per-op sub-checks (the `set_attr` value-only restriction, the `dispatch` event whitelist) are inlined in the same function where they apply. No remote config, no tables to keep in sync.
- The allowed dispatch event list is intentionally small at the highlight level. Apps can expand it for their own semantic events by passing a per-envelope `extra_dispatch_events=...` parameter at envelope creation time.

### 6. Revocation

Revocation happens from five paths, all converging on the same code:

1. **Subject clicks "Stop"** on the persistent indicator. Default binding: `dj-click="revoke_control_envelope"`. Framework-provided handler.
2. **Envelope expires.** Background task checks every second and revokes any envelope past `expires_at`.
3. **`max_ops` reached.** Handled inline in `push_commands_to`.
4. **Controller cancels.** Controller's own UI has a "End session" button.
5. **Framework-initiated revocation.** System checks can revoke envelopes (e.g., if the subject logs out, their view unmounts, or a security event fires).

All five paths call:

```python
def revoke_envelope(envelope_id: str, revoked_by: str) -> None:
    """Revoke an active envelope. Idempotent."""
    envelope = _get_envelope(envelope_id)
    if envelope is None or envelope.revoked:
        return
    envelope.revoked = True
    envelope.revoked_at = now()
    envelope.revoked_by = revoked_by
    _persist_envelope(envelope)
    _audit_log.append(envelope.envelope_id, "revoked", {"by": revoked_by})

    # Tell both sides
    _push_event_to_session(envelope.subject_session_id, "djust:control_revoked", {
        "envelope_id": envelope_id,
        "revoked_by": revoked_by,
    })
    _push_event_to_session(envelope.controller_session_id, "djust:control_revoked", {
        "envelope_id": envelope_id,
        "revoked_by": revoked_by,
    })
```

After revocation:

- The subject's indicator bar transitions to "Session ended" for 3 seconds, then hides.
- Any in-flight `push_commands_to` calls fail with `RevokedEnvelope`.
- The controller's UI shows a "Session ended" banner and returns to the agent's normal state.
- Subsequent `push_commands_to` calls raise `RevokedEnvelope` immediately.

## Audit trail

Every envelope-related event writes an append-only entry to an audit log. The log is:

- **Append-only**: no update or delete API. Retention is managed by the app via a scheduled job that archives or deletes entries older than N days, per the app's retention policy.
- **Queryable** by subject user, controller user, envelope ID, and time range.
- **Visible to the subject** via a user-facing page: "Who has helped me?" shows a reverse-chronological list of envelopes, their scopes, their durations, and links to the full op-by-op detail.
- **Visible to admins** via a Django admin registration, with filters by controller, subject, scope, and outcome (accepted / denied / revoked).
- **Machine-queryable** via a stable `djust_consent_audit` table for compliance reporting.

### What gets logged

Per envelope lifecycle:

- `requested` — controller initiates a request, with scope + reason
- `granted` — subject accepts, with full envelope details
- `denied` — subject rejects
- `request_timeout` — subject didn't respond in 60 seconds
- `op_executed` — one op passed scope check and was sent to subject
- `op_rejected` — one op failed scope check (with reason)
- `revoked` — envelope ended, with reason (subject/timeout/ops_cap/framework/controller)

Every entry includes:
- timestamp (UTC, microsecond precision)
- envelope ID
- subject session ID
- controller session ID
- subject user ID (if any)
- controller user ID (if any)
- event type
- event-specific payload (op name + args + target, or reason string, or scope set)

The audit log is a critical compliance artifact. The framework ships a minimal Django model for it, plus extension points for apps that want to stream to an external SIEM.

## Security considerations

### Threat 1: Hostile controller bypassing scope

**Attack**: A controller with `highlight` scope tries to execute a `push` op to trigger a destructive handler.

**Mitigation**: Scope enforcement runs on *every* op *on the server* before the op is relayed to the subject. The controller cannot construct a chain that bypasses the check — their `push_commands_to` call is the enforcement point, not some client-side guard.

**Defense in depth**: the subject's client-side auto-executor also has a mode that tags incoming chains as "from envelope" and re-validates the ops against the currently-active envelope's scope (cached from the `djust:control_granted` event). Double-checking at the client means a buggy server-side check (future regression) doesn't silently widen the attack surface.

### Threat 2: Session hijack of the controller

**Attack**: An attacker compromises the controller's authenticated session, and uses it to issue `request_control` calls against arbitrary subjects.

**Mitigation**: `request_control` requires the subject to explicitly accept. An attacker can issue requests but not execute them. The attack narrows to "can the attacker social-engineer a subject into accepting?" — which is a standard phishing problem, outside the framework's remit but mitigated by:

1. The consent dialog showing the controller's verified identity (not a free-form display name — the name comes from `controller.user.get_full_name()`).
2. An "organization" field in the consent dialog that's set per-app-config and cannot be spoofed by the controller.
3. A rate limit on `request_control`: max 3 pending requests per controller session at once, max 10 requests per controller per hour by default. Exceeding triggers a warning in the security log.

### Threat 3: Envelope token theft

**Attack**: An attacker intercepts an envelope ID and tries to use it to execute ops against the subject.

**Mitigation**: The envelope ID is **not a capability**. You can't execute ops just by knowing the envelope ID; you have to be authenticated as the controller session that owns the envelope. The server verifies `request.session.session_key == envelope.controller_session_id` on every `push_commands_to` call.

### Threat 4: Subject UI spoofing (fake indicator)

**Attack**: An attacker constructs a page that *looks like* an active consent envelope indicator to confuse users about what's happening.

**Mitigation**: The persistent indicator is rendered by the framework, not by app templates. It uses a reserved DOM id `#djust-consent-indicator` and is injected via a `{% djust_consent_indicator %}` template tag that apps **must** include in their base layout (a system check fails without it). Apps can't override the indicator's markup to hide it or make it look different.

More importantly, the indicator's presence *correlates with the subject actually being under control*. If there's no active envelope, there's no indicator. An attacker rendering a fake indicator achieves nothing — they're not actually driving the subject's UI.

### Threat 5: Cross-session leak via dispatch

**Attack**: A controller in an envelope with `highlight` scope dispatches a CustomEvent carrying subject state into the detail field, then has their own listener in the subject's page read the detail.

**Mitigation**: `dispatch` ops under an envelope can only carry detail fields declared in the envelope's `extra_dispatch_events` config. The framework doesn't let the controller set arbitrary detail fields. Combined with the `dispatch` event name whitelist (step 5 above), this closes the side channel.

**Defense in depth**: the framework never executes a `dispatch` op whose target is the subject's view root — dispatches can only target named elements. Any listener the controller might have placed in the subject's page has to be there legitimately (via the subject's own app code), and the framework's JS runtime isolates controller-pushed ops from app-page event handlers by namespacing all envelope-sourced CustomEvents with a `djust-envelope-` prefix.

### Threat 6: Controller denial-of-service

**Attack**: A controller holds an envelope open indefinitely by sending one op every N seconds, consuming server resources and pinning a connection.

**Mitigation**: `max_ops` (default 200) and `duration` (default 600 seconds, hard max 3600) both cap envelope lifetime at creation. A malicious controller can hold one envelope, but cannot exceed the cap. Multiple envelopes per controller are limited by the rate limit on `request_control` (threat 2). Together, a controller can only consume a bounded share of server resources.

### Threat 7: Subject revocation bypass

**Attack**: The revoke button is buried, styled away, or intercepted so the subject can't click it.

**Mitigation**: The `#djust-consent-indicator` is rendered by the framework, cannot be styled away, and the stop button is a fixed-position element that survives CSS overrides. Additionally, a keyboard shortcut — `Cmd-Shift-Esc` (or `Ctrl-Shift-Esc` on non-Mac) — revokes any active envelope regardless of whether the user can reach the indicator. The shortcut is bound at `window` level and cannot be preventDefault'd.

### Threat 8: Tampered audit log

**Attack**: An admin or attacker with DB write access modifies or deletes audit entries after the fact.

**Mitigation**: This is outside the framework's control — at the storage layer, any app with DB access can tamper with any DB table. We document that apps requiring tamper-evident logs should:

1. Ship audit entries to an append-only external store (Kafka, Kinesis, BigQuery, a SIEM) in addition to the local table.
2. Use row-level triggers to prevent direct updates to the audit table (Postgres supports this).
3. Hash-chain the entries so a post-hoc modification is detectable.

The framework provides hooks for all three patterns but does not enforce any of them by default.

### Threat 9: Session state leakage via screenshot

**Attack**: A controller takes screenshots of the subject's UI using `get_state_snapshot` (available at `read` scope).

**Mitigation**: This is a legitimate use of the `read` scope. The subject consented to read access. The mitigation is the subject *seeing* the screenshots happen — which requires reliable real-time visibility. The audit log and the persistent indicator are the remediation.

For apps that need to prevent specific fields from being read even under `read` scope, there's a new decorator:

```python
@private_from_control
class SensitiveView(LiveView):
    internal_notes = ""           # readable normally
    api_key = ""                  # excluded from get_state_snapshot when an envelope is active
```

Fields marked `@private_from_control` are stripped from any state snapshot sent to a controller, regardless of scope.

## User-facing subject experience

One of the hardest problems in designing this is making the subject experience feel *safe* without being *annoying*. Four design principles:

### Principle 1: Consent cannot be accidental

- The consent dialog is modal and blocks interaction with the underlying page.
- "Deny" is the default button, reachable by Escape.
- "Allow" has a 2-second unlock delay from when the dialog first renders.
- The dialog persists until the subject makes a choice or 60 seconds pass.

### Principle 2: Active envelopes are always visible

- Persistent indicator bar at the top of the viewport, outside app layout.
- Shows controller name, scope summary, time remaining, and a prominent Stop button.
- Cannot be styled away, hidden, or removed by app code.
- Hash-verified by the framework on every re-render to detect client-side tampering.

### Principle 3: Every op is visible as it happens

- The framework optionally renders a small floating panel at the bottom-right of the viewport that lists the last 5 ops executed under the envelope.
- Default is ON for `fill`/`click`/`full` scopes, OFF for `highlight`/`scroll` (too noisy).
- Each op entry has the op name, target, and timestamp.
- Subjects can hover to see the full op details.

### Principle 4: Stop is instant

- Clicking Stop revokes immediately (no confirmation dialog).
- `Cmd-Shift-Esc` / `Ctrl-Shift-Esc` revokes even when the indicator is inaccessible.
- After stop, the subject's UI reverts to its pre-envelope state where possible (no new invariant is imposed — the subject keeps whatever state changes the controller made before stop, but no new changes can happen).

## Examples

### Example 1: Support helping a user complete a form

```python
# Controller side
class SupportAgentView(LiveView):
    login_required = True
    permission_required = "support.provide_assistance"

    @event_handler
    def start_assist(self, user_session_id: str, **kwargs):
        self.request_control(
            subject_session_id=user_session_id,
            scope=("highlight", "scroll", "fill"),
            duration=600,
            reason="Help you complete the account setup form",
        )

    @event_handler
    def point_to_username(self, **kwargs):
        if not self.active_envelope:
            return
        self.push_commands_to(
            self.active_envelope.subject_session_id,
            JS.add_class("help-highlight", to="#username")
              .scroll_into_view("#username")
              .dispatch("assistant:narrate", detail={
                  "text": "Enter your username here.",
              }),
        )

    @event_handler
    def fill_username(self, value: str, **kwargs):
        if not self.active_envelope:
            return
        self.push_commands_to(
            self.active_envelope.subject_session_id,
            JS.set_attr("value", value, to="#username")
              .dispatch("input", to="#username"),
        )
```

```python
# Subject side
class SetupView(LiveView):
    template_name = "accounts/setup.html"

    # Subject gets the default consent dialog and indicator via the mixin:
    class Meta:
        consent_envelope = True
```

That's it. The framework handles dialog rendering, indicator, op visibility, audit log, revocation, and lifecycle. App authors write the controller's intent (`point_to_username`, `fill_username`) and the subject configuration (`consent_envelope = True`).

### Example 2: Classroom workshop (instructor → many students)

```python
class WorkshopInstructorView(LiveView, PresenceMixin):
    presence_key = "workshop:{session_id}"
    login_required = True
    permission_required = "workshops.lead"

    @event_handler
    def demo_step(self, step: int, **kwargs):
        # Broadcast to every student in the presence group via their active envelopes
        chain = (
            JS.scroll_into_view(f"#step-{step}")
              .add_class("active", to=f"#step-{step}")
              .transition("pulse", to=f"#step-{step}", time=500)
        )
        self.broadcast_commands_to_envelopes(chain, presence_group=self.presence_key)


class WorkshopStudentView(LiveView, PresenceMixin):
    presence_key = "workshop:{session_id}"

    class Meta:
        consent_envelope = True
        # Auto-accept envelopes from users with the workshops.lead permission,
        # scoped to highlight/scroll only. This is the "I trust my instructor"
        # convenience, off by default, opt-in per view.
        auto_accept_envelopes = {
            "from_permission": "workshops.lead",
            "max_scope": ("highlight", "scroll"),
            "max_duration": 3600,
        }
```

With `auto_accept_envelopes`, students don't see a dialog for every request — they implicitly trust the instructor for the duration of the workshop. They still see the persistent indicator and can stop at any time. The opt-in is explicit per view class.

### Example 3: Accessibility caregiver

```python
class PatientView(LiveView):
    class Meta:
        consent_envelope = True
        # Grant a specific designated caregiver persistent control authority,
        # so they don't have to re-request every session. The patient still
        # accepts once, then the grant persists.
        persistent_envelopes = {
            "from_user_id": 42,  # caregiver's user ID
            "scope": ("highlight", "scroll", "fill", "click"),
            "require_confirmation_each_session": True,
        }
```

The patient accepts a control request once; a persistent grant is issued, and subsequent sessions just need one-click reconfirmation. This is the pattern for accessibility scenarios where the caregiver is a known, trusted party.

## System checks

Five new static checks run via `djust_audit` / `manage.py check`:

- **A050**: View sets `consent_envelope = True` but its base template is missing `{% djust_consent_indicator %}`. Error.
- **A051**: View uses `request_control` / `push_commands_to` but has no `permission_required` set. Warning.
- **A052**: `auto_accept_envelopes` is set without `from_permission` or `from_user_id`. Error.
- **A053**: `@private_from_control` decorator used but no envelope-aware views reference this class. Info.
- **A054**: `persistent_envelopes` configured with `require_confirmation_each_session=False` and scope level ≥ `click`. Warning — persistent click-level grants without per-session confirmation are unusually high trust.

## Testing strategy

Five classes of test:

1. **Scope enforcement unit tests**: exhaustive coverage of `check_op` for every (scope, op) pair. ~100 lines covering the truth table.
2. **Lifecycle integration tests**: spawn two test views (controller + subject), issue a request, exercise each accept/deny/revoke path, verify the resulting envelope state and audit log. ~200 lines.
3. **Concurrent envelope tests**: two controllers trying to grab the same subject, subject with max pending requests, envelope expiry under load. ~150 lines.
4. **Security regression tests**: one test per threat in the Security section, showing the mitigation holds. ~300 lines. These must not be deletable without a corresponding security review.
5. **UI tests**: the consent dialog renders correctly, the indicator persists, the stop button works, the keyboard shortcut works. Exercised in JSDOM against a mocked WebSocket. ~200 lines.

## Open questions

1. **Envelope persistence across page navigation.** If the subject navigates from `/a/` to `/b/`, does their active envelope persist into the new view? My lean: yes, if the controller also transitions to the corresponding view, envelopes are portable between views in the same app. A view that wants to decline incoming control has `consent_envelope = False`.
2. **Cross-device envelopes.** Can the controller be on a desktop and the subject on a mobile? Technically yes — the framework doesn't care about device type. The display of the consent dialog and indicator on mobile needs thought, though.
3. **Envelope chaining.** Can a subject who is under control from A themselves act as a controller for subject B? My lean: yes, with a warning in the audit log ("nested control session"). No technical restriction. It's a legitimate accessibility pattern (caregiver helps user A, who is helping user B).
4. **What about anonymous subjects?** The design assumes authenticated subjects. Anonymous users (no Django user, session-only) can still be controlled, but the audit log has to cope with nulls. Probably fine; worth a test.
5. **Persistent grant revocation.** If a subject wants to revoke a persistent grant for a specific caregiver, they need a UI for it. Ship a default account settings page for this? My lean: yes, as part of the v0.5.x launch. Otherwise apps roll their own inconsistently.
6. **Recording sessions.** Some apps will want to *record* envelope sessions for training or compliance (op-by-op replay). The audit log already captures everything; rendering it back as a visual replay is a separate feature. Worth filing as a follow-up.
7. **Encrypting envelope contents.** For compliance apps, the audit log may need encryption at rest. Django's storage layer doesn't enforce it. Ship a hook for apps to provide an encryptor function on audit writes.
8. **Timezones on expiry display.** The consent dialog says "10 minutes remaining." That's locale-sensitive formatting. Framework localizes via Django's `i18n`.

## Decision

**Recommendation**: accept as Proposed. Implement in Phase 4 of [ADR-002](002-backend-driven-ui-automation.md), before the LLM-driven `AssistantMixin` lands in Phase 5 — because an LLM-driven remote-assist scenario is a natural early adopter of the consent envelope, and we want the envelope design settled before AI features start depending on it.

Implementation order:

1. `ConsentEnvelope` dataclass + state backend (memory + Redis) — 3 days.
2. `check_op` scope enforcement + exhaustive unit tests — 2 days.
3. `request_control` / `accept_control_request` / `revoke_envelope` lifecycle — 3 days.
4. Consent dialog template + client-side behavior — 2 days.
5. Persistent indicator + stop button + keyboard shortcut — 1 day.
6. `push_commands_to` + `broadcast_commands_to_envelopes` — 2 days.
7. Audit log model + Django admin + user-facing "who has helped me" page — 3 days.
8. `@private_from_control` + `auto_accept_envelopes` + `persistent_envelopes` — 2 days.
9. A050-A054 system checks — 1 day.
10. Security regression tests (one per documented threat) — 3 days.
11. Integration tests + UI tests — 3 days.
12. Docs: subject-facing explainer, controller-facing guide, compliance story — 3 days.

Total: ~4 weeks of focused work. Fits in Phase 4 with buffer.

## Changelog

- **2026-04-11**: Initial draft. Proposed.
