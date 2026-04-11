# Server-Driven UI

djust gives the server full declarative control over the browser. Instead of writing JavaScript hooks that listen for clicks and mutate the DOM, you build a [JS Command chain](js-commands.md) in Python and push it directly to the connected client. The framework runs it. No custom JS.

This guide covers `self.push_commands(chain)` — the foundation primitive for every backend-driven UI feature in djust. It's intentionally small (one method) but unlocks a large class of applications: guided tours, wizards, instructor-led workshops, remote support handoffs, AI-driven voice interfaces, automated UI testing.

## The simplest possible example

```python
from djust import LiveView
from djust.decorators import event_handler
from djust.js import JS


class DashboardView(LiveView):
    template_name = "dashboard.html"

    @event_handler
    def highlight_new_button(self, **kwargs):
        """Fired from a 'Show me around' button on the dashboard."""
        self.push_commands(
            JS.add_class("tour-highlight", to="#btn-new-project")
              .focus("#btn-new-project")
              .transition("pulse", to="#btn-new-project", time=600)
        )
```

```html
<!-- dashboard.html -->
<button dj-click="highlight_new_button">Show me around</button>
<button id="btn-new-project">Create a project</button>
```

Click "Show me around" and the server pushes three DOM operations down the WebSocket: add a `tour-highlight` class to the create-project button, move keyboard focus to it, and run a CSS pulse animation. Zero custom JavaScript. Zero DOM code in the template.

## How it works

### Server side — `push_commands(chain)`

`push_commands()` is a one-line helper on `LiveView` (via `PushEventMixin`). It takes a [`JSChain`](js-commands.md) and queues a `djust:exec` push event carrying the chain's `ops` list:

```python
# What push_commands does under the hood
def push_commands(self, chain):
    self.push_event("djust:exec", {"ops": chain.ops})
```

The chain is serialized as a JSON-safe list of `[op_name, args]` pairs. It piggybacks on the existing `push_event` transport, so there's no new wire protocol, no new WebSocket message type, and no new infrastructure to deploy.

### Client side — the `djust:exec` auto-executor

Every djust page automatically runs a small listener (`src/27-exec-listener.js`) that watches for `djust:exec` push events and interprets them via `window.djust.js._executeOps(ops, null)` — the same function that runs inline `dj-click="[[...]]"` JSON chains and fluent-API `.exec()` calls from hook code.

You don't write a `dj-hook`, you don't import anything in your templates, you don't configure the auto-executor. It ships with `client.js` and is bound once at load time.

### End-to-end flow

```
1. User clicks dj-click="highlight_new_button"
2. Server: highlight_new_button() runs, calls self.push_commands(chain)
3. Server: chain queued in self._pending_push_events
4. Server: normal event response is sent (VDOM patches, etc.)
5. Server: flushes push-event queue → WebSocket type:'push_event'
6. Client: 03-websocket.js dispatches djust:push_event CustomEvent on window
7. Client: 27-exec-listener.js catches it, filters for event === 'djust:exec'
8. Client: calls window.djust.js._executeOps(payload.ops, document.body)
9. Client: each op runs against the DOM (add_class, focus, transition, etc.)
```

Every step is inspectable in the djust debug panel (`Ctrl+Shift+D`), same as any other push event.

## The eleven commands you can push

Every [JS Command](js-commands.md) from v0.4.1 works in a pushed chain. Quick reference:

| Command | Effect |
|---|---|
| `JS.show(selector)` | Unhide an element (clear `display:none`) |
| `JS.hide(selector)` | Set `display:none` |
| `JS.toggle(selector)` | Flip between shown/hidden |
| `JS.add_class(names, to=selector)` | Add CSS classes |
| `JS.remove_class(names, to=selector)` | Remove CSS classes |
| `JS.transition(names, to=selector, time=ms)` | Apply classes, wait N ms, remove them (animations) |
| `JS.set_attr(name, value, to=selector)` | Set an HTML attribute |
| `JS.remove_attr(name, to=selector)` | Remove an HTML attribute |
| `JS.focus(selector)` | Move keyboard focus |
| `JS.dispatch(event, to=selector, detail=...)` | Fire a CustomEvent |
| `JS.push(event, value=...)` | Send a server event (round-trip) |

All scoped-target options (`to`, `inner`, `closest`) work the same way they do in `dj-click` chains. See the [JS Commands guide](js-commands.md) for the full reference.

## Patterns

### Sequencing multiple visible steps

Every call to `push_commands()` queues a separate `djust:exec` event. The client runs each one as it arrives, so a handler that calls `push_commands` multiple times gives you a sequence of distinct steps the user can see unfold:

```python
@event_handler
def run_tour(self, **kwargs):
    self.push_commands(JS.add_class("highlight", to="#step-1"))
    self.push_commands(JS.add_class("highlight", to="#step-2"))
    self.push_commands(JS.add_class("highlight", to="#step-3"))
```

Three separate events, three distinct animation frames on the client. Each ships with its own WebSocket frame, which means the steps are strictly ordered and interruptible. (For timing-sensitive sequences that need to pause between steps — "highlight for 2 seconds, then advance" — use the [`wait_for_event`](server-driven-ui.md#waiting-for-the-user) primitive from Phase 1b or the [`TutorialMixin`](tutorials.md) from Phase 1c, which handle timing declaratively.)

### Mixing commands with state changes

Nothing special — `push_commands` composes with regular state mutation. Update your view attrs, optionally push commands, and the framework sends both the VDOM patch and the exec chain:

```python
@event_handler
def open_modal(self, **kwargs):
    self.modal_open = True                             # triggers VDOM patch
    self.push_commands(
        JS.focus("#modal-title")                       # moves focus after render
          .transition("fade-in", to="#modal", time=200)
    )
```

Both side effects happen on the same event round-trip. The VDOM patch lands first, then the exec chain, so the modal is already in the DOM when `focus` runs.

### Composing with `push_event`

`push_commands` and `push_event` share the same queue and preserve ordering. Use them together when a chain needs to coexist with a regular event fired to a `dj-hook`:

```python
@event_handler
def save(self, **kwargs):
    self._persist()
    self.push_event("flash", {"message": "Saved!", "type": "success"})
    self.push_commands(
        JS.add_class("just-saved", to=".save-button")
          .transition("pulse", to=".save-button", time=400)
    )
    self.push_event("analytics", {"action": "document_saved"})
```

Four events queued in order — two plain, two exec chains. All delivered to the client after the handler returns.

### Type safety

`push_commands()` rejects anything that isn't a `JSChain` with a clear `TypeError`:

```python
self.push_commands("show('#modal')")            # ❌ TypeError: expected JSChain
self.push_commands([["show", {"to": "#modal"}]])  # ❌ TypeError: expected JSChain
self.push_commands({"ops": [...]})              # ❌ TypeError: expected JSChain
self.push_commands(JS.show("#modal"))           # ✓ works
```

The check is intentional: the framework validates the chain structure by requiring a real `JSChain` instance, which can only be built through the `JS.*` factory methods. You can't smuggle an arbitrary ops list through `push_commands` and bypass the chain's immutability guarantees.

## When to reach for `push_commands` vs other primitives

| You want to... | Use |
|---|---|
| Run DOM ops on a direct user click, no server round-trip | Inline `dj-click="{{ JS.show('#modal') }}"` |
| Run DOM ops from inside a server handler after state changes | `self.push_commands(JS.show('#modal'))` |
| Run DOM ops from a client-side `dj-hook` lifecycle callback | `this.js().show('#modal').exec()` |
| Run DOM ops in response to any server event in arbitrary code | `window.djust.js.show('#modal').exec()` |
| Build a guided tour with highlight + narrate + wait-for-user | [`TutorialMixin`](tutorials.md) *(Phase 1c, coming in v0.4.2)* |
| Pause a background handler until the user acts | [`wait_for_event`](server-driven-ui.md#waiting-for-the-user) *(Phase 1b)* |
| Drive another user's UI (support, instructor, assist) | Consent envelope *(coming in v0.5.x)* |
| Have an LLM generate UI commands from user speech | `AssistantMixin` *(coming in v0.5.x)* |

Everything in the "coming in..." rows is built on top of `push_commands`. It's intentionally the smallest possible primitive so every higher-level feature composes cleanly.

## Background work and pushed commands

`push_commands` works inside `@background` handlers too, which is the pattern for any flow longer than a single click:

```python
from djust.decorators import event_handler, background

class Onboarding(LiveView):
    @event_handler
    @background
    def start_tour(self, **kwargs):
        self.tour_running = True

        # Step 1: highlight dashboard nav
        self.push_commands(
            JS.add_class("tour-highlight", to="#nav-dashboard")
              .dispatch("tour:narrate", detail={"text": "This is your dashboard."})
        )
        time.sleep(3)
        self.push_commands(JS.remove_class("tour-highlight", to="#nav-dashboard"))

        # Step 2: highlight create button
        self.push_commands(
            JS.add_class("tour-highlight", to="#btn-new-project")
              .dispatch("tour:narrate", detail={"text": "Click here to start a project."})
        )
        # ... and so on

        self.tour_running = False
```

Each step runs, the user sees the highlight appear, waits, disappears, and the next one lands. The `time.sleep(3)` is the simplest possible "wait" — it's synchronous and blocks the background task. For proper "wait for the user to actually click the highlighted button" behavior, use [`wait_for_event`](server-driven-ui.md#waiting-for-the-user) when it lands in Phase 1b.

Once `TutorialMixin` (Phase 1c) ships, all of this becomes declarative — a list of `TutorialStep` entries — with no manual state machine to write.

## Debugging

Open the djust debug panel (`Ctrl+Shift+D`) and switch to the **Network** tab. Every `djust:exec` push event shows up alongside regular push events and VDOM patches, with the full `ops` payload visible when you click on the entry. If a chain isn't doing what you expect:

1. Is the event in the Network tab? If not, `push_commands` wasn't called — check the server-side handler path.
2. Is the `ops` payload shaped correctly? Each entry should be a `[op_name, args_dict]` pair.
3. Is the target selector matching any elements? Try it in the browser console: `document.querySelectorAll('#your-selector')`.
4. Is `window.djust.js` loaded? Run `typeof window.djust.js._executeOps` in the console — should return `'function'`.
5. Set `window.djustDebug = true` in the console and re-run the handler — the auto-executor will log any op failures.

Most "chain didn't do anything" issues are selector mismatches or `push_commands` not being called. The auto-executor itself is small enough (~40 lines of source) that it rarely causes problems.

## What's next

`push_commands` is **Phase 1a** of the backend-driven UI story in [ADR-002](../adr/002-backend-driven-ui-automation.md). Two more primitives land in the same v0.4.2 release on top of this one:

- **Phase 1b: `wait_for_event`** — see [Waiting for the user](#waiting-for-the-user) below.
- **Phase 1c: [`TutorialMixin`](tutorials.md)** — a declarative state machine for guided tours. Describe the tour as a list of `TutorialStep` entries (target, message, wait-for event, optional on-enter/on-exit chains) and call `start_tutorial()`. The mixin handles step ordering, highlight cleanup, timeout handling, and skip/cancel. Zero boilerplate.

After v0.4.2, Phase 4 (multi-user broadcast, consent envelope) and Phase 5 (LLM-driven `AssistantMixin`) extend the primitive into multi-user and AI-driven scenarios. See [ADR-002](../adr/002-backend-driven-ui-automation.md) for the full roadmap.

## Waiting for the user

`push_commands` sends chains to the client, but by itself it doesn't know how to pause a background task until the user actually does something. That's what `await self.wait_for_event(...)` is for — it's the async primitive that makes "highlight this button, wait for the user to click it, then move on" work declaratively.

```python
from djust.decorators import event_handler, background
from djust.js import JS

class Onboarding(LiveView):
    tour_running: bool = False

    @event_handler
    @background
    async def start_tour(self, **kwargs):
        self.tour_running = True

        # Step 1: highlight the create button
        self.push_commands(
            JS.add_class("tour-highlight", to="#btn-new-project")
              .focus("#btn-new-project")
        )

        # Suspend until the user clicks it (which fires create_project)
        try:
            result = await self.wait_for_event("create_project", timeout=60)
        except TimeoutError:
            self.tour_running = False
            self.push_commands(JS.remove_class("tour-highlight", to="#btn-new-project"))
            return

        # User clicked — clean up the highlight and advance
        self.push_commands(JS.remove_class("tour-highlight", to="#btn-new-project"))
        self.project_name = result.get("name", "")

        # Step 2: continue the tour...

    @event_handler
    def create_project(self, name: str, **kwargs):
        """Called when the user clicks the highlighted button."""
        Project.objects.create(name=name, owner=self.request.user)
```

### Signature

```python
async def wait_for_event(
    self,
    name: str,
    *,
    timeout: Optional[float] = None,
    predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
```

- **`name`** — the name of the event handler to wait for. Must match a method decorated with `@event_handler`. Any call to that handler resolves the waiter (unless a predicate filters it out).
- **`timeout`** — optional seconds to wait. Raises `asyncio.TimeoutError` when exceeded. `None` (the default) waits indefinitely.
- **`predicate`** — optional callable that takes the handler's kwargs dict and returns `True` to resolve or `False` to keep waiting. Useful for "wait for the user to click *this specific* button" when multiple events might fire the same handler with different arguments.
- **Returns** — the kwargs dict that was passed to the matching handler.

### Predicate examples

```python
# Wait for the user to submit a form with a specific project id
result = await self.wait_for_event(
    "submit_form",
    predicate=lambda kw: kw.get("project_id") == 42,
    timeout=30,
)

# Wait for any "save" event from a draft whose status is "ready"
result = await self.wait_for_event(
    "save",
    predicate=lambda kw: kw.get("status") == "ready",
)

# Wait for a click with no filter — first click wins
result = await self.wait_for_event("next_step")
```

A predicate that raises is treated as "no match" and logged — a buggy predicate can't crash the event pipeline or deadlock your background task.

### Concurrency

Multiple background tasks can wait on the same event name simultaneously. When that event fires, **every** waiter whose predicate matches resolves with the same kwargs dict. This lets you build fan-out patterns like "three tutorial branches all waiting on the user's next action" without manual coordination.

Waiters for different event names are fully independent — notifying `event_a` never resolves a waiter for `event_b`.

### Timeouts and cleanup

When a waiter times out, the framework removes it from the registry automatically — no stale waiters accumulate over the life of a view. When the view disconnects (WebSocket close, tab navigation, browser crash), the framework cancels **all** pending waiters on that view. Any `@background` task currently awaiting a waiter will unblock with `asyncio.CancelledError`, giving it a chance to clean up (remove highlights, persist partial state, emit analytics).

### Integration with `push_commands`

The two primitives compose naturally. The pattern for any guided flow is:

1. Push a chain that sets up the UI state (highlight, narrate, focus)
2. Await a waiter for the event you want the user to trigger
3. On resolution: push a chain that cleans up the UI state and sets up the next step
4. Repeat

```python
@event_handler
@background
async def run_multi_step_tour(self, **kwargs):
    for step in self.tour_steps:
        # Setup
        self.push_commands(
            JS.add_class("highlight", to=step["target"])
              .dispatch("tour:narrate", detail={"text": step["message"]})
        )
        # Wait for user action or timeout
        try:
            await self.wait_for_event(step["expect"], timeout=step.get("timeout", 60))
        except TimeoutError:
            self.push_commands(JS.remove_class("highlight", to=step["target"]))
            return  # User abandoned the tour
        # Cleanup + advance
        self.push_commands(JS.remove_class("highlight", to=step["target"]))
```

This is exactly the state machine `TutorialMixin` will formalize in Phase 1c — a list of steps, setup/wait/cleanup per step, skip/cancel handling — without the boilerplate.

### Limitations

- **Component events are not currently notified.** If a `LiveComponent` fires a handler, the parent `LiveView`'s waiters don't resolve. This is intentional for v0.4.2 scope — component-event waiting is uncommon and adds complexity. File a follow-up if you hit a case where it matters.
- **Actor-mode views bypass the dispatch hook.** Views running under the experimental Rust actor system (`use_actors = True`) don't notify waiters yet. The non-actor path is the default and is fully supported.
- **`wait_for_event` requires the handler to actually run server-side.** If the client fires an event that fails validation (missing params, auth error, etc.), the handler never executes and the waiter never resolves — only the timeout will unblock it.

## See also

- [JS Commands](js-commands.md) — the full command vocabulary (11 ops, scoped targets, immutable chains)
- [ADR-002](../adr/002-backend-driven-ui-automation.md) — full design doc with motivation, alternatives, and the multi-user / AI follow-through
- [Hooks](hooks.md) — when to reach for client-side `dj-hook` lifecycle callbacks instead
- [Debug Panel](../advanced/debug-panel.md) — inspecting push events and exec chains at runtime
