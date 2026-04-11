# ADR-004: Undo for LLM-Driven Actions

**Status**: Proposed
**Date**: 2026-04-11
**Deciders**: Project maintainers
**Target version**: v0.5.x (lands with `AssistantMixin`)
**Related**: [ADR-002](002-backend-driven-ui-automation.md), [ADR-003](003-llm-provider-abstraction.md)

---

## Summary

When an LLM drives a UI, plans occasionally go wrong — the model misinterprets intent, hallucinates a tool call, or chains handlers into an unintended final state. Users need to be able to undo the *entire plan* (not just the last step) as one atomic action, the same way Cmd-Z works in a desktop app. This ADR explores four possible undo strategies, picks one as the primary path, and defines the fallback behavior when the primary path isn't possible. The recommendation is **per-plan checkpoint snapshots for in-memory state plus optional paired inverse handlers for external side effects**, with a user-visible "Undo" button that appears for 60 seconds after every completed plan and reverts everything the plan touched.

The mechanism is specifically *for plans executed by AssistantMixin*. Regular user-initiated handler calls don't get this undo — they already have optimistic updates with rollback from v0.3+, and users have the implicit undo of "click a different button." LLM plans are different because the user often doesn't know what the plan will do until it's already running, so post-hoc undo has to be both trustworthy and prominent.

## Context

### Why undo matters more for LLM plans than for normal handlers

Three things make LLM plan undo qualitatively different from normal handler undo:

1. **Multi-step atomicity.** A plan is often 3-10 handler calls. If step 5 does something wrong, the user wants to revert steps 1-5 as a single action, not click through five individual undo buttons.
2. **Low user awareness of intermediate state.** When you click a button yourself, you know what you just did. When you tell an assistant to "split this project into phases," you don't know *in advance* whether the assistant will create, rename, delete, or move things. If the result isn't what you expected, "go back to before I asked" is the only mental model that works.
3. **Trust calibration.** LLM-driven UIs are new for most users. A visible, reliable undo button lowers the stakes enough for users to actually try the assistant. Without it, users stay in "preview mode forever" — they ask what the assistant *would* do rather than letting it do anything.

Conversely, the problem is harder because:

1. **Arbitrary side effects.** Handlers can touch a database, call an external API, write a file, send an email, or mutate in-memory state. Each of these has different reversibility characteristics.
2. **Cross-handler dependencies.** Plan step 2 might reference a resource created by step 1. Undo has to happen in reverse order or reference resolution fails.
3. **Concurrent users.** Between plan execution and undo, another user might have modified the same resources. Pure snapshot-based undo would trample their changes.
4. **Partial failures.** A plan that crashes halfway through leaves the system in a mixed state. Undo has to know how to roll back the completed steps without trying to roll back the uncompleted ones.

### What already exists

djust v0.3+ has **optimistic updates with rollback**. When a client applies an optimistic DOM change, the server can reject it and the client snaps back to the previous state. This is a *client-side* mechanism for a specific UI pattern (button clicks that look instant but might fail server-side validation). It doesn't touch server state, doesn't persist across events, and doesn't know anything about multi-step plans.

What's missing:
- A **server-side mechanism** for snapshotting view state before a plan.
- A **handler-level mechanism** for declaring how to inverse-execute an operation.
- A **user-facing UI** for invoking undo after a plan completes.
- An **audit story** so admins can see what was undone and when.

## Design space: four approaches

Before picking a recommendation, here are the four approaches I considered, with honest evaluation of each. Understanding what was rejected and why is important because the first-choice approach has caveats, and the rejected ones each have narrow cases where they win.

### Approach A: Snapshot-and-restore

**How it works**: Before executing a plan, the framework deep-copies the view's `public` attributes (the same ones that drive `get_context_data()`) into a checkpoint. If the user clicks undo, the framework replaces the view's live state with the checkpoint and re-renders.

**Pros**:
- Zero app-author work. Any LiveView using `AssistantMixin` gets undo for free.
- Handles arbitrary combinations of handler calls in a single plan.
- Composable with `wait_for_event` — the checkpoint is taken when the plan starts, the undo restores to that point regardless of what the plan did in between.
- Fast — snapshot is a dict copy, ~µs.

**Cons**:
- **Only works for in-memory state.** A handler that inserted a `Project` row into the database is not undone by restoring `self.projects` — the DB row still exists.
- Apps that hold large state (long lists, model querysets) pay memory and CPU for the snapshot.
- Cannot reason about external side effects (emails sent, API calls made, files written).
- Restore can clobber state that was modified *between* the plan completing and the user clicking undo. Edge case for single-user views, serious problem for multi-user views.

**Where it wins**: Views whose state lives entirely in `self.attrs` and is derived from source-of-truth data elsewhere. Dashboards, search/filter/sort UIs, wizard state, draft editors. In practice this is a *lot* of djust views.

**Where it loses**: Views that mutate persisted models or call external services. It lies to the user — the undo button "works" (UI snaps back) but the actual side effects are still there.

### Approach B: Inverse operations (paired handlers)

**How it works**: Each `@event_handler` can declare an inverse with a new decorator:

```python
@event_handler
@undoable(inverse="delete_phase")
def create_phase(self, name: str, **kwargs):
    self._last_phase_id = Phase.objects.create(project=self.project, name=name).id
    ...

@event_handler
def delete_phase(self, phase_id: int, **kwargs):
    Phase.objects.filter(pk=phase_id).delete()
    ...
```

When the framework needs to undo `create_phase`, it calls `delete_phase(phase_id=<captured>)`. The framework passes the original call's return value as the inverse's argument via a convention (e.g. the decorated handler returns a dict, the framework stores it, and the inverse receives it).

**Pros**:
- Handles persistent state correctly — the inverse actually deletes the row.
- Correctness is app-author's responsibility, so domain knowledge stays where it belongs.
- Composable with audit logging — the inverse run is a regular handler call that goes through the normal pipeline.
- Multi-user safe, because the inverse runs with current concurrent state, not a stale snapshot.

**Cons**:
- Requires app-author work: every destructive or state-mutating handler needs an explicit inverse.
- Some operations are fundamentally not inversible (sending an email, charging a credit card, calling a one-shot external API). Apps need a way to say "this handler can't be undone."
- Parameter passing between `create` and the inverse `delete` requires a convention that's easy to get wrong.
- Plans that call inverse-less handlers become non-undoable as a whole. The framework has to decide whether to offer partial undo or disable undo for the entire plan.

**Where it wins**: Apps with disciplined CRUD handlers and clear domain-level inverses. Project management, CMS, task trackers, form builders.

**Where it loses**: Apps with lots of one-shot or external-effect handlers, where writing inverses is impractical.

### Approach C: Event log replay

**How it works**: The framework records the entire event stream for each session (every `@event_handler` call with its args). To undo the last plan, the framework replays the entire event history *minus* the plan's events, starting from either `mount` or a recent known-good checkpoint.

**Pros**:
- Works for any handler, no app-author annotation required.
- Captures ordering and dependencies correctly — if event B depended on the result of event A, removing A and replaying B naturally surfaces the dependency break.
- Provides a natural audit trail: the event log *is* the history.
- The replay mechanism is the same mechanism used for "record and playback for testing" and "session resume after disconnect," so one primitive solves multiple problems.

**Cons**:
- **Replay is expensive.** On a view that's been alive for an hour, replaying every past event to undo the last plan is O(events) and can be slow.
- **External side effects are replayed.** If a prior event sent an email, replay would try to send it again. Events have to be marked as "replay-safe" or not, which is the same annotation problem as Approach B with extra steps.
- **Non-deterministic handlers break replay.** Handlers that depend on `datetime.now()`, `random`, or external API state won't produce the same result on replay.
- Storage cost: keeping the full event log in the state backend (Redis, usually) is more data than keeping just the latest state.
- Security surface: the event log contains every handler call, including sensitive ones. Encryption at rest is non-trivial.

**Where it wins**: Apps that already do event sourcing (CQRS, audit-first systems, regulated industries). For them, this is nearly free.

**Where it loses**: Almost every other case. The cost-to-benefit ratio is bad for undo as a sole use case.

### Approach D: Branch isolation (shadow views)

**How it works**: Before executing a plan, the framework creates a "shadow" copy of the view (new instance, deep-copied state, isolated DB transaction). The plan runs on the shadow view. The shadow's state changes are diffed against the original's and presented as a "preview" — the user either accepts (commits the shadow's changes) or rejects (discards the shadow).

**Pros**:
- **Clean semantics.** The plan can't affect real state until the user approves. No undo needed; it's pre-commit instead of post-commit.
- **Transactional integrity.** The DB isolation means the plan runs inside one Django atomic block that either commits or rolls back.
- **External side effects are captured.** The framework can intercept outbound API calls during the shadow run and either queue them for commit or drop them.

**Cons**:
- **UX changes radically.** The user has to approve every plan before anything visible happens. That breaks the "AI does it, then I approve or undo" flow that `AssistantMixin` was designed around.
- **Shadow views are expensive.** Creating a full view copy plus a DB transaction per plan multiplies memory and connection-pool usage.
- **Not all handlers are transactional.** External API calls, file writes, and in-memory caches are hard to isolate.
- **Confusing to debug.** Users and developers see two views where they expect one.

**Where it wins**: High-stakes environments where the cost of a wrong plan is significant (financial apps, healthcare, admin dashboards). The "review before applying" semantics are arguably the right default there.

**Where it loses**: Consumer-facing apps where the value of AI assistance is immediacy. Forcing approval on every plan adds friction that defeats the point.

## Recommendation

**Primary approach: A (snapshot) with B (inverse handlers) as an opt-in extension for side-effect handlers.**

The reasoning:

1. Most LiveView state is in-memory or derived from queried data. Snapshot-and-restore handles that case with zero app-author work.
2. For handlers that mutate persistent data or call external services, apps opt in by adding `@undoable(inverse="...")` to the handler. The framework uses the inverse when undoing the plan, falling back to the snapshot for the rest.
3. Plans that contain one or more handlers marked `@not_undoable` (explicit opt-out) disable undo for the whole plan. The assistant narrates "this action can't be undone — are you sure?" before executing and omits the undo button afterward.
4. Event log replay (C) is rejected as the default because the cost is too high for too narrow a benefit, but nothing stops an app from layering it on top (the framework already captures handler calls for audit purposes).
5. Branch isolation (D) is rejected as the default because it breaks the "AI does it and shows you" flow, but is *absolutely* the right approach for specific high-stakes handlers. The framework supports it as an opt-in per-handler mode (`@review_required` decorator, future work in v0.6.0+).

**Scope**: undo reverts the entire plan as a single atomic action. There is no "undo just step 3" — it would be a confusing UX and the implementation gets hairy because steps can depend on each other. If users need finer control, they can cancel the plan mid-execution via a prominent stop button during execution.

**Time window**: the undo button is visible for 60 seconds (configurable) after plan completion. After 60s, the button auto-dismisses and the snapshot is garbage-collected. This prevents unbounded memory use and matches the intuition of "undo is a momentary escape hatch, not a persistent history."

**Single undo only**: after you undo a plan, you cannot undo the undo. The snapshot is restored and then discarded. If the user wants a redo story, they can re-ask the assistant.

## Detailed design

### The `UndoSnapshot` dataclass

```python
# python/djust/assistant/undo.py
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
import copy
import time


@dataclass
class CapturedCall:
    """One handler call made during a plan, with enough info to invert it."""
    name: str                                  # handler name
    args: Dict[str, Any]                       # kwargs passed in
    result: Any = None                         # what the handler returned
    timestamp: float = field(default_factory=time.time)
    inverse_name: Optional[str] = None         # set if handler was @undoable
    inverse_args: Optional[Dict[str, Any]] = None  # computed inverse kwargs
    undoable: bool = True                      # False means this call blocks plan-level undo


@dataclass
class UndoSnapshot:
    """A checkpoint + call log for one completed plan."""
    snapshot_id: str                           # short random id
    created_at: float
    expires_at: float
    public_state: Dict[str, Any]               # deep copy of view's public attrs
    captured_calls: List[CapturedCall]         # in execution order
    plan_description: str                      # human-readable summary
    conversation_turn_id: str                  # for audit cross-reference

    def is_undoable(self) -> bool:
        return all(c.undoable for c in self.captured_calls)

    def is_expired(self) -> bool:
        return time.time() > self.expires_at
```

### The decorators

```python
# python/djust/assistant/undo.py (continued)

def undoable(inverse: str = None, *, capture_args: Callable = None):
    """Mark a handler as explicitly undoable.

    Args:
        inverse: Name of the handler to call to reverse this one. If the
                 handler name matches 'delete_X' for 'create_X', can be
                 inferred; otherwise specify explicitly.
        capture_args: Optional callable that takes (handler_result, call_kwargs)
                     and returns the kwargs dict to pass to the inverse.
                     Defaults to 'pass the handler's return dict'.
    """
    def decorator(handler):
        handler._djust_undoable = True
        handler._djust_inverse = inverse
        handler._djust_capture_args = capture_args or _default_capture_args
        return handler
    return decorator


def not_undoable(reason: str = ""):
    """Mark a handler as non-undoable. Plans containing this handler disable undo.

    The `reason` is narrated to the user before executing the plan:
    'Heads up: this plan includes an email send, which can't be undone.'
    """
    def decorator(handler):
        handler._djust_not_undoable = True
        handler._djust_not_undoable_reason = reason
        return handler
    return decorator
```

### Snapshot creation and restoration

```python
# Inside AssistantMixin
class AssistantMixin:
    assistant_undo_window: float = 60.0         # seconds
    assistant_undo_enabled: bool = True

    def _capture_snapshot(self, plan_description: str) -> UndoSnapshot:
        """Called before executing a plan."""
        if not self.assistant_undo_enabled:
            return None

        # Deep-copy the view's public attributes.
        from djust.serialization import get_public_attrs
        public_state = copy.deepcopy(get_public_attrs(self))

        snap = UndoSnapshot(
            snapshot_id=generate_short_id(),
            created_at=time.time(),
            expires_at=time.time() + self.assistant_undo_window,
            public_state=public_state,
            captured_calls=[],
            plan_description=plan_description,
            conversation_turn_id=self._current_turn_id,
        )
        # Store in the view instance; garbage-collected on expiry
        self._undo_snapshots = getattr(self, "_undo_snapshots", {})
        self._undo_snapshots[snap.snapshot_id] = snap
        self._current_snapshot_id = snap.snapshot_id
        return snap

    def _capture_call(self, snap: UndoSnapshot, handler, call_kwargs: Dict, result: Any):
        """Called after each tool call during plan execution."""
        if snap is None:
            return

        captured = CapturedCall(
            name=handler.__name__,
            args=call_kwargs,
            result=result,
            undoable=not getattr(handler, "_djust_not_undoable", False),
        )
        if getattr(handler, "_djust_undoable", False):
            inv_name = getattr(handler, "_djust_inverse", None)
            if inv_name:
                captured.inverse_name = inv_name
                captured.inverse_args = handler._djust_capture_args(result, call_kwargs)
        snap.captured_calls.append(captured)

    @event_handler
    async def assistant_undo(self, snapshot_id: str, **kwargs):
        """User-facing undo handler. Wired to the undo button."""
        snap = self._undo_snapshots.get(snapshot_id)
        if snap is None:
            self.push_commands(
                JS.dispatch("assistant:undo-failed", detail={
                    "reason": "snapshot expired or not found"
                })
            )
            return
        if snap.is_expired():
            self.push_commands(
                JS.dispatch("assistant:undo-failed", detail={
                    "reason": "snapshot expired"
                })
            )
            return

        # Run the inverse for every call that had one (reverse order).
        for call in reversed(snap.captured_calls):
            if call.inverse_name:
                handler = getattr(self, call.inverse_name, None)
                if handler is None:
                    logger.error(
                        "Undo: inverse handler %s not found for %s",
                        call.inverse_name, call.name,
                    )
                    continue
                try:
                    await self._call_handler(call.inverse_name, call.inverse_args or {})
                except Exception as e:
                    logger.exception("Undo inverse failed for %s: %s", call.name, e)
                    # Fall through to snapshot restore below

        # Restore the snapshot for any state not covered by inverses.
        from djust.serialization import restore_public_attrs
        restore_public_attrs(self, snap.public_state)

        # Clean up and notify
        del self._undo_snapshots[snapshot_id]
        self._narrate(f"Undone: {snap.plan_description}")
        self.push_commands(JS.dispatch("assistant:undone", detail={"snapshot_id": snapshot_id}))
```

### The UI side

The narrator bubble's "plan complete" state includes an inline undo button that fires for the 60-second window. After 60 seconds, the bubble transitions to "done" state and the button disappears:

```html
<!-- tutorial_bubble.html (shipped as default) -->
<div id="assistant-bubble" style="display:none">
    <p class="assistant-message"></p>
    <div class="assistant-actions">
        <button
            class="assistant-undo"
            dj-click="assistant_undo"
            data-snapshot-id=""
            style="display:none"
        >
            Undo
        </button>
        <button class="assistant-dismiss" dj-click="assistant_dismiss">
            Dismiss
        </button>
    </div>
</div>
```

A small client-side hook (~25 lines) listens for `assistant:plan-complete` events, shows the undo button with a countdown indicator, and auto-hides it at the window expiry.

### The execution flow with undo

Integrating with `AssistantMixin.execute_plan` from [ADR-002](002-backend-driven-ui-automation.md):

```python
async def execute_plan(self, plan: ChatResponse):
    # Check for non-undoable handlers before starting
    non_undoable = [
        call for call in plan.tool_calls
        if getattr(getattr(self, call.name, None), "_djust_not_undoable", False)
    ]
    if non_undoable and self.assistant_destructive_confirm:
        reasons = [
            getattr(getattr(self, c.name), "_djust_not_undoable_reason", "")
            for c in non_undoable
        ]
        confirmed = await self._confirm_plan_without_undo(plan, reasons)
        if not confirmed:
            self._narrate("Cancelled.")
            return

    # Capture snapshot unless the plan is non-undoable
    snap = None
    if self.assistant_undo_enabled and not non_undoable:
        snap = self._capture_snapshot(self._summarize_plan(plan))

    # Execute as before
    for call in plan.tool_calls:
        handler = getattr(self, call.name, None)
        if handler is None:
            self._narrate(f"I don't know how to do '{call.name}'. Skipping.")
            continue
        self._narrate(call.narration or f"Calling {call.name}…")
        await asyncio.sleep(self.assistant_step_delay)
        if self._plan_cancelled:
            # User hit stop — abort without snapshot
            self._narrate("Stopped.")
            if snap:
                self._rollback_snapshot(snap)
            return
        result = await self._call_handler(call.name, call.args)
        if snap:
            self._capture_call(snap, handler, call.args, result)

    # Plan complete — show undo button
    if snap:
        self.push_commands(
            JS.dispatch("assistant:plan-complete", detail={
                "snapshot_id": snap.snapshot_id,
                "expires_at": snap.expires_at,
                "description": snap.plan_description,
            })
        )
    else:
        self.push_commands(
            JS.dispatch("assistant:plan-complete", detail={
                "description": "done (no undo available)"
            })
        )
```

## Limitations and caveats

### Things this design does *not* undo

Be explicit about these in the user-facing documentation so apps set correct expectations:

1. **External API calls** (charge a card, post to Slack, enqueue an email) — unless the handler is marked `@undoable` with a paired inverse that makes the compensating call.
2. **Filesystem writes** — unless the handler is marked `@undoable` and the inverse deletes/restores the file.
3. **Database mutations to tables not part of the view's `public_state`** — unless the handler is `@undoable`. The snapshot only captures view attrs, not the whole database.
4. **Concurrent changes from other users or sessions.** If Alice asks the assistant to rename project 42, and Bob renames project 42 himself five seconds later, Alice's undo restores her snapshot — which may or may not be what Bob expects. Multi-user apps should consider this carefully.
5. **Changes that happened after the plan completed but before the user clicked undo.** If the user makes a manual edit after the plan, then clicks undo, the snapshot restore clobbers the manual edit. The narrator bubble can mitigate this by showing "Your recent changes will be lost. Continue?" when the public state has diverged from the snapshot.
6. **Time-sensitive state** (auth tokens, rate-limit counters, session timers). Snapshotted state can be stale by the time undo runs.

### Things this design *should* undo but won't always

1. **Handler side effects on `self._private` attrs.** By convention, private attrs are not part of the view's reactive state, but some handlers do mutate them (caches, memos). Those mutations aren't snapshotted. Document that `_private` attrs are outside the undo guarantee.
2. **DOM state applied via `push_commands`.** If the plan called `push_commands(JS.add_class("active", to="#btn"))`, that class is on the DOM and isn't tracked in server state. The next re-render may or may not clear it depending on how the template is structured. Apps that care about this use declarative class assignment in templates instead of imperative JS Commands.
3. **Events emitted via `push_event` or `dispatch`.** Once fired, they can't be un-fired. Downstream listeners that did something can't be rolled back.

### When to disable undo entirely

A few scenarios where the framework disables undo and the app should expect it:

1. **Plans containing `@not_undoable` handlers** — entire plan is non-undoable, the user gets a "this can't be undone" warning before execution.
2. **Very large public state** (>10 MB snapshot) — the framework warns and falls back to no-undo, so we don't blow memory on a view holding massive in-memory data.
3. **`assistant_undo_enabled = False`** explicitly set on the view class — for privacy-sensitive views that don't want state snapshots held in memory.
4. **User is in a consent-envelope session** (ADR-005) — undo is disabled for plans executed by a remote controller; the subject user can still invoke their own undo on their own plans.

## Security considerations

### The snapshot as a data leak

`UndoSnapshot.public_state` contains a deep copy of the view's state, which may include private user data. It lives in memory for up to 60 seconds. Implications:

- **Don't serialize snapshots to the state backend.** They live in the view instance only, garbage-collected when the view exits or the window expires. Never written to Redis / disk / logs.
- **Clear on logout / session expiry.** Hook into the existing session cleanup path.
- **Memory bounds.** Cap `assistant_undo_window * plan_frequency * avg_plan_state_size` at a configurable max per session. When exceeded, drop the oldest snapshot.
- **Audit log entries reference snapshots by ID only.** The ID is not a capability — it's just a correlation key. The audit log contains plan description + resulting state deltas, not the full snapshot.

### Authorization for undo

The `assistant_undo` event handler runs through the normal djust auth pipeline. Only the session that triggered the original plan can undo it. The snapshot ID alone isn't enough — the handler verifies the current session matches the snapshot's owning session.

For consent-envelope sessions (ADR-005), the subject user (not the controller) owns every snapshot. The controller can't undo their own remote-triggered plans; the subject has to click undo themselves. This is intentional: it preserves the subject's authority over their own data.

### Malicious inverse handlers

Apps can write inverse handlers that *don't* actually reverse the original. An `@undoable(inverse="delete_phase")` call on `create_phase` doesn't guarantee `delete_phase` is functionally the inverse — only that the framework will call it during undo. Apps are responsible for correctness.

Mitigations:

1. **Audit log shows both the original plan and the undo sequence.** Users can inspect what was called during undo, same as what was called during plan execution.
2. **A static check** (new `A045` under [ADR-002](002-backend-driven-ui-automation.md)'s proposed security checks) warns when an `@undoable` handler's inverse doesn't exist or isn't itself an `@event_handler`.
3. **The `assistant_undo` handler runs each inverse through the same `@destructive` confirmation flow as regular execution.** If the inverse is destructive, the user is prompted. Default is off — most inverses shouldn't need confirmation — but the decorator respects the handler's own `@destructive` marker.

### Race condition on concurrent user edits

Between plan completion and undo click, the user (or another user in multi-user views) may modify the public state. Naive restore would clobber those changes.

**Mitigation**: compare the snapshot's `public_state` against the current `public_state` before restoring. If they differ, show a confirmation dialog: "Your recent changes will be lost. Continue with undo?" The dialog is opt-out via a per-view config for apps that don't want the extra friction.

## Examples

### Example 1: Pure in-memory undo (common case)

```python
class DashboardView(LiveView, AssistantMixin):
    """A filter/search dashboard. All state is in-memory."""

    def mount(self, request, **kwargs):
        self.filters = {"status": "all", "sort": "recent"}
        self.selected_items = []

    @event_handler
    def apply_filter(self, field: str, value: str, **kwargs):
        """Apply a filter to the dashboard."""
        self.filters[field] = value

    @event_handler
    def select(self, item_id: int, **kwargs):
        """Select an item."""
        if item_id not in self.selected_items:
            self.selected_items.append(item_id)
```

User says: "Filter to status=active, sort by priority, select items 1-5."

The assistant generates a 7-call plan. The framework snapshots `{"filters": {"status": "all", "sort": "recent"}, "selected_items": []}` before execution. Plan runs. User looks at the result, says "no that's not what I wanted," clicks undo. The framework restores the dict and re-renders. Zero app-author work, zero database changes, zero side effects. Clean.

### Example 2: DB handlers with paired inverses

```python
class ProjectView(LiveView, AssistantMixin):

    @event_handler
    @undoable(inverse="delete_phase")
    def create_phase(self, name: str, **kwargs) -> Dict:
        """Create a new phase. Returns {phase_id}."""
        phase = Phase.objects.create(project=self.project, name=name)
        self.phases = list(self.project.phases.all())
        return {"phase_id": phase.id}

    @event_handler
    def delete_phase(self, phase_id: int, **kwargs):
        """Delete a phase."""
        Phase.objects.filter(pk=phase_id).delete()
        self.phases = list(self.project.phases.all())
```

Note the `@undoable(inverse="delete_phase")` and the return-value dict. The framework's `_default_capture_args` picks up the returned dict and uses it as the inverse's kwargs. So `create_phase(name="Discovery")` returns `{"phase_id": 42}`, framework stores it, undo calls `delete_phase(phase_id=42)`. Clean inversion.

### Example 3: External side effect, non-undoable

```python
class SupportView(LiveView, AssistantMixin):

    @event_handler
    @not_undoable(reason="Sending an email can't be undone.")
    def send_notification(self, subject: str, body: str, **kwargs):
        """Send a notification email to the user."""
        send_mail(subject, body, "noreply@app.com", [self.user.email])
```

User says: "Send a notification saying 'your account is ready'." The assistant generates a plan that calls `send_notification`. Before executing, the framework notices the plan contains a `@not_undoable` handler and narrates:

> "Heads up: this plan includes an email send, which can't be undone. Proceed anyway?"

User confirms. Plan executes. No undo button appears afterward — the plan is final.

### Example 4: Mixed plan (some undoable, some not)

```python
class ProjectView(LiveView, AssistantMixin):

    @event_handler
    @undoable(inverse="delete_phase")
    def create_phase(self, name: str, **kwargs): ...

    @event_handler
    @not_undoable(reason="The email notification can't be un-sent.")
    def notify_team(self, message: str, **kwargs): ...
```

User says: "Create Discovery, Build, and Launch phases, then notify the team."

The framework detects a `@not_undoable` handler in the plan and asks for confirmation: "This plan includes a team notification, which can't be undone. Proceed anyway?" If the user confirms, the plan runs without a snapshot — no undo offered afterward.

If the user wants undo, they can rephrase: "Create Discovery, Build, and Launch phases (don't notify yet)." The assistant generates a shorter plan that excludes the non-undoable step, and the undo button appears when it completes.

## Open questions

1. **Should undo be redo-able (i.e., "redo" after an undo)?** Probably not. Redo adds significant complexity for marginal benefit — users who undo then want the original result can just re-ask the assistant. If data shows this is a common friction, revisit.
2. **Snapshot size limits.** What happens when a view has 50MB of state? Default is "fall back to no-undo and narrate that." But some apps might want "fall back to event log replay instead," which would require lifting Approach C as an opt-in. Defer unless requested.
3. **Undo for plans that mutated other users' data via broadcast.** If Alice's plan broadcast `JS.add_class("highlight", to=...)` to every student in a workshop, does undo broadcast a `remove_class` to everyone? My lean: yes, broadcasts replay on undo. Needs a test case in the implementation.
4. **Persistent undo across sessions.** Can a user close the tab, reopen tomorrow, and undo yesterday's plan? No — snapshots are in-memory-only for privacy reasons (see Security above). This is a hard no and needs to be documented prominently.
5. **Time-of-check-to-time-of-use race on concurrent edits.** Described in the security section. The design as proposed detects divergence and confirms. Edge cases: what if the divergence is semantically equivalent (e.g., whitespace changes)? Probably just ignore them; don't try to be clever about equivalence.
6. **How does undo interact with streaming mid-execution?** If a plan is mid-execution and the user clicks "stop," we already roll back via `_rollback_snapshot`. But if a handler was streaming tokens into `self.assistant_response`, the partial output is in the snapshot. Restore clears it. Desired behavior? Probably yes, but worth calling out.
7. **Should `@undoable` work for non-assistant handlers too?** I.e., could a regular user click, a `create_phase` call, and a "Undo last action" button all work together? Technically yes — the mechanism is general. But it would blur the "undo for AI plans specifically" framing. My lean: keep undo scoped to `AssistantMixin` plans for v0.5.x, generalize later if app authors ask for it.

## Alternatives considered

- **Approach B alone** (paired inverses, no snapshot). Rejected because it puts too much burden on app authors and fails silently for in-memory state.
- **Approach A alone** (snapshot, no inverses). Rejected because it misleads users about persistent-state handlers.
- **Per-step undo instead of per-plan.** Rejected — confusing UX, complex implementation.
- **Ship undo as opt-in only.** Rejected — most apps should get it for free, and the default should be the safe one.
- **Use Django's `@transaction.atomic` for persistent state.** Complementary, not an alternative. If the handler wraps its own DB calls in a transaction, that's good discipline regardless. The undo mechanism works alongside it — snapshot for in-memory, transaction rollback within the handler, inverse handler for post-commit compensating actions.
- **Use `contextvars` to scope changes to a plan-local context.** Clever but doesn't generalize — most of the state we care about isn't contextvar-addressable.

## Decision

**Recommendation**: accept as Proposed. Implement alongside `AssistantMixin` in Phase 5 of [ADR-002](002-backend-driven-ui-automation.md). Implementation order:

1. `UndoSnapshot`, `CapturedCall` dataclasses + `_capture_snapshot` / `_capture_call` / `_rollback_snapshot` — 2 days.
2. `@undoable` / `@not_undoable` decorators + introspection glue — 1 day.
3. `assistant_undo` event handler + client-side bubble UI with countdown — 2 days.
4. Concurrent-edit divergence detection + confirmation dialog — 1 day.
5. A045 system check for `@undoable` without valid inverse — 1 day.
6. Memory bounds + garbage collection — 1 day.
7. Tests (happy path, partial failure, non-undoable plan, concurrent edits, inverse failure, snapshot expiry) — 3 days.
8. Documentation: user-facing page on how undo works, worked examples, limitations — 2 days.

Total: ~2 weeks, fits in Phase 5's window.

## Changelog

- **2026-04-11**: Initial draft. Proposed.
