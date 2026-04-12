# Guided Tours with `TutorialMixin`

`TutorialMixin` is djust's declarative state machine for guided tours, onboarding flows, and wizards where the server drives the UI through a sequence of highlighted steps. Apps describe the tour as a list of `TutorialStep` dataclasses and mix in `TutorialMixin` — the framework handles step progression, highlight/narrate push commands, cleanup on advance, skip/cancel handling, and per-step timeouts.

It's built on top of [`push_commands`](server-driven-ui.md) (Phase 1a) and [`wait_for_event`](server-driven-ui.md#waiting-for-the-user) (Phase 1b), but you don't need to use those primitives directly unless you want to customize beyond what the mixin supports.

## The simplest possible example

```python
from djust import LiveView
from djust.tutorials import TutorialMixin, TutorialStep


class OnboardingView(TutorialMixin, LiveView):
    template_name = "onboarding.html"

    tutorial_steps = [
        TutorialStep(
            target="#nav-dashboard",
            message="This is your dashboard — your home base.",
            timeout=4.0,
        ),
        TutorialStep(
            target="#btn-new-project",
            message="Click here to create your first project.",
            wait_for="create_project",
        ),
        TutorialStep(
            target="#project-form [name=title]",
            message="Give it a title — anything works.",
            wait_for="form_input_title",
        ),
        TutorialStep(
            target="#btn-save",
            message="Save your project to continue.",
            wait_for="form_saved",
        ),
    ]
```

```html
<!-- onboarding.html -->
{% load djust_tutorials %}

<button dj-click="start_tutorial">Take the tour</button>

<nav id="nav-dashboard">...</nav>
<button id="btn-new-project">New project</button>
<form id="project-form">...</form>

{% tutorial_bubble %}
```

That's the entire tour. Click "Take the tour" and the framework:

1. Highlights `#nav-dashboard`, shows "This is your dashboard — your home base." in the bubble for 4 seconds
2. Highlights `#btn-new-project`, shows "Click here to create your first project.", waits until the user's click fires a `create_project` event handler
3. Highlights the title input, shows the next message, waits for `form_input_title`
4. Highlights the save button, shows the final message, waits for `form_saved`
5. Cleans up all highlights and exits

Zero custom JavaScript. Zero client-side state.

## MRO ordering: `TutorialMixin` must come first

Always list `TutorialMixin` **before** `LiveView` in your class bases:

```python
# Correct
class MyView(TutorialMixin, LiveView):
    ...

# Wrong — TutorialMixin.__init__ is never called
class MyView(LiveView, TutorialMixin):
    ...
```

Django's `View.__init__` does not call `super().__init__()`, so any mixin listed after a View-derived class never gets initialised. If you get the order wrong, the `djust.V010` system check will catch it at startup with a clear error message.

## How it works

### The state machine

`TutorialMixin` runs as a `@background` task, one step at a time:

```
for step in tutorial_steps:
    1. Push "setup" chain (add highlight class, dispatch tour:narrate, focus target)
    2. If step.on_enter is set, push that chain too
    3. Wait for the step's completion condition:
       - If step.wait_for is set: await self.wait_for_event(wait_for, timeout=step.timeout)
       - If only step.timeout is set: asyncio.sleep(timeout) (auto-advance)
       - If neither: no wait, advance immediately
    4. If step.on_exit is set, push that chain
    5. Push "cleanup" chain (remove highlight class)

while waiting:
    - skip_tutorial() unblocks the current step and advances to the next
    - cancel_tutorial() unblocks the current step and exits the loop
    - view disconnect cancels the background task entirely
```

Every push happens via `self.push_commands(JSChain)`, which ships the chain through the `djust:exec` auto-executor (Phase 1a). The narration event is dispatched at the step's target element with `bubbles: true`, so the framework-provided `{% tutorial_bubble %}` template tag catches it at `document` level and renders the message.

### State and events exposed by the mixin

```python
class YourView(TutorialMixin, LiveView):
    # Three instance attributes the mixin manages for you:
    tutorial_running: bool          # True while a tour is active
    tutorial_current_step: int      # 0-based index, or -1 if not running
    tutorial_total_steps: int       # len(tutorial_steps)
```

And four event handlers you can wire to buttons, keyboard shortcuts, or call from other handlers:

```html
<button dj-click="start_tutorial">Take the tour</button>
<button dj-click="skip_tutorial">Next</button>
<button dj-click="cancel_tutorial">Close</button>
<button dj-click="restart_tutorial">Start over</button>
```

The default `{% tutorial_bubble %}` template tag already binds `skip_tutorial` and `cancel_tutorial` to its own buttons — you only need to wire `start_tutorial` explicitly.

## `TutorialStep` reference

```python
@dataclass
class TutorialStep:
    target: str                               # CSS selector (required)
    message: str                              # Narration text (required, can be empty)
    position: Literal["top", "bottom", "left", "right"] = "bottom"
    wait_for: Optional[str] = None            # Event handler name to wait on
    timeout: Optional[float] = None           # Seconds
    on_enter: Optional[JSChain] = None        # Extra setup commands
    on_exit: Optional[JSChain] = None         # Extra cleanup commands
    highlight_class: str = "tour-highlight"   # CSS class applied during the step
    narrate_event: str = "tour:narrate"       # CustomEvent name
```

### `target` and `position`

`target` is a CSS selector. The framework uses it for both the highlight class (added to the first matching element via `JS.add_class(..., to=target)`) and the bubble positioning (the client-side bubble script reads the element's bounding rect and places itself above/below/left/right per `position`).

`position` hints where the bubble renders relative to the target. One of `"top"`, `"bottom"` (default), `"left"`, `"right"`.

### `wait_for` and `timeout`

Four scenarios depending on how you set these:

| `wait_for` | `timeout` | Behavior |
|---|---|---|
| `None` | `None` | Advance immediately — step just flashes the narration |
| `None` | `T` | Auto-advance after T seconds |
| `"event_name"` | `None` | Wait indefinitely for `event_name` (user must fire it to advance) |
| `"event_name"` | `T` | Wait up to T seconds for `event_name`, then advance silently |

Skipping or cancelling the tour always unblocks the current step immediately, regardless of `wait_for`/`timeout`.

### `on_enter` and `on_exit`

Optional `JSChain` instances pushed **in addition to** the default setup/cleanup chains. Use them for per-step custom behavior:

```python
from djust.js import JS

TutorialStep(
    target="#search-input",
    message="Try searching for 'hello'.",
    wait_for="search",
    on_enter=(
        JS.scroll_into_view("#search-input")
          .set_attr("placeholder", "try: hello", to="#search-input")
    ),
    on_exit=JS.remove_attr("placeholder", to="#search-input"),
)
```

`on_enter` runs after the default highlight/narrate/focus chain and before the wait. `on_exit` runs after the wait and before the default cleanup chain.

### `highlight_class` and `narrate_event`

Override these per-step when you need different visual treatment or a different CustomEvent name. Most tours use the defaults.

```python
TutorialStep(
    target="#danger-zone",
    message="This is where destructive actions live.",
    highlight_class="tour-highlight-danger",  # app-defined CSS class
    timeout=5.0,
)
```

## The `{% tutorial_bubble %}` template tag

Renders a floating bubble container that listens for `tour:narrate` events and displays the current step's message. The bubble is absolutely positioned next to the target element per the step's `position` hint, shows `step N / total` progress, and includes "Skip" and "Close" buttons bound to `skip_tutorial` and `cancel_tutorial`.

```html
{% load djust_tutorials %}

<!-- Default: class="dj-tutorial-bubble", bottom position -->
{% tutorial_bubble %}

<!-- Custom CSS class for app-level theming -->
{% tutorial_bubble css_class="my-app-tour-bubble" %}

<!-- Different default position when the step doesn't specify -->
{% tutorial_bubble position="top" %}

<!-- Listen for a different event name (if you changed narrate_event on steps) -->
{% tutorial_bubble event="my:narrate" %}
```

The bubble is marked `dj-update="ignore"` so morphdom won't clobber its live content during VDOM patches.

### Styling the bubble

The framework doesn't ship CSS — styling is the app's responsibility. Here's a minimal starter:

```css
.dj-tutorial-bubble {
    position: absolute;
    padding: 12px 16px;
    background: #1e293b;
    color: white;
    border-radius: 8px;
    max-width: 320px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
    z-index: 10000;
    display: none;
    font-size: 14px;
    line-height: 1.5;
}

.dj-tutorial-bubble[data-visible="true"] {
    display: block;
}

.dj-tutorial-bubble__text {
    margin: 0 0 8px 0;
}

.dj-tutorial-bubble__progress {
    font-size: 11px;
    opacity: 0.7;
    margin-bottom: 8px;
}

.dj-tutorial-bubble__actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
}

.dj-tutorial-bubble__actions button {
    background: transparent;
    color: white;
    border: 1px solid rgba(255, 255, 255, 0.3);
    padding: 4px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
}

.dj-tutorial-bubble__actions button:hover {
    background: rgba(255, 255, 255, 0.1);
}

/* Highlight class applied to the tour target */
.tour-highlight {
    outline: 3px solid #6366f1;
    outline-offset: 4px;
    border-radius: 4px;
    transition: outline 0.2s ease;
}
```

Apps using djust-theming get the bubble styled automatically via the `config.get_framework_class()` integration (coming as a follow-up).

## Patterns

### A simple walk-through (auto-advance)

Every step auto-advances after a few seconds — no user input required. Good for "look at this quickly":

```python
class DemoView(TutorialMixin, LiveView):
    tutorial_steps = [
        TutorialStep(target="#feature-1", message="First feature.", timeout=3.0),
        TutorialStep(target="#feature-2", message="Second feature.", timeout=3.0),
        TutorialStep(target="#feature-3", message="Third feature.", timeout=3.0),
    ]
```

### Interactive onboarding (user-driven)

Each step waits for the user to actually perform the action. The tour is paced by the user, not a clock:

```python
class OnboardingView(TutorialMixin, LiveView):
    tutorial_steps = [
        TutorialStep(
            target="#btn-new-project",
            message="Create a new project.",
            wait_for="create_project",
        ),
        TutorialStep(
            target="[name=project_title]",
            message="Give it a title.",
            wait_for="save_title",
        ),
        TutorialStep(
            target="#btn-invite",
            message="Invite a teammate.",
            wait_for="send_invite",
            timeout=300,  # Give up after 5 min
        ),
    ]

    @event_handler
    def create_project(self, **kwargs):
        Project.objects.create(owner=self.request.user)

    @event_handler
    def save_title(self, title: str, **kwargs):
        self.project.title = title
        self.project.save()

    @event_handler
    def send_invite(self, email: str, **kwargs):
        Invitation.objects.create(project=self.project, email=email)
```

### Mixing auto-advance and wait-for

Most real tours blend both — a few "look at this" steps interleaved with "now you try":

```python
tutorial_steps = [
    TutorialStep(target="#welcome", message="Welcome!", timeout=2.0),
    TutorialStep(target="#dashboard", message="This is the dashboard.", timeout=3.0),
    TutorialStep(
        target="#btn-action",
        message="Go ahead and click it.",
        wait_for="user_action",
    ),
    TutorialStep(target="#result", message="Nice job!", timeout=3.0),
]
```

### Branching tours with custom handlers

For more complex flows, override `start_tutorial` or call `_run_step` directly:

```python
class AdaptiveTutorial(TutorialMixin, LiveView):
    tutorial_steps = []  # not used directly

    @event_handler
    @background
    async def start_tutorial(self, **kwargs):
        if self.tutorial_running:
            return
        self.tutorial_running = True
        try:
            if self.request.user.is_new:
                await self._run_beginner_flow()
            else:
                await self._run_advanced_flow()
        finally:
            self._cleanup_active_step()
            self.tutorial_running = False

    async def _run_beginner_flow(self):
        for step in self.beginner_steps:
            await self._run_step(step)

    async def _run_advanced_flow(self):
        for step in self.advanced_steps:
            await self._run_step(step)
```

## Skipping and cancelling

The mixin provides two user-facing exit paths:

- **`skip_tutorial`** — advances past the current step immediately. The loop moves to the next step. Use for "Next" buttons or keyboard shortcuts.
- **`cancel_tutorial`** — aborts the tour entirely. The loop exits on the next iteration. Use for "Close" buttons, Escape key, or when the user navigates away.

Both are wired to the default `{% tutorial_bubble %}` skip/close buttons.

```html
<!-- Bind keyboard shortcuts if you want -->
<div dj-keydown.escape="cancel_tutorial" dj-keydown.right="skip_tutorial"></div>
```

## View disconnect cleanup

When the user navigates away or closes the tab, the WebSocket disconnect path automatically cancels the `@background` task running the tour — there's no lingering work, no leaked waiters, no highlighted elements left behind on the (now gone) page.

If you have custom cleanup logic (analytics, draft persistence, etc.), add it to your view's `disconnect` handler or use `on_exit` chains.

## Debugging tours

The djust debug panel (`Ctrl+Shift+D`) shows every `djust:exec` push event in the Network tab. For a tour, you'll see pairs of events per step: the setup chain (add_class + dispatch + focus) followed by the cleanup chain (remove_class). Watch for:

- **Missing targets** — a step's `target` selector doesn't match any element. Check the selector in the browser console: `document.querySelectorAll('your-selector')`.
- **Handler name typos** — `wait_for` names must match `@event_handler` method names exactly. A typo blocks the step indefinitely (or until timeout).
- **Handler validation failures** — if a handler fails parameter validation, it never runs, so the waiter never resolves. The Network tab shows the validation error.

Set `window.djustDebug = true` in the browser console to see verbose logs from the auto-executor.

## Limitations

A few real constraints worth knowing:

- **LiveComponent events propagate to parent waiters automatically.** A step's `wait_for` matches handlers on either the LiveView itself *or* any embedded `LiveComponent`. When a component handler fires, the framework notifies the parent view's waiter registry with the handler's kwargs — plus an injected `component_id` key so a predicate can disambiguate events from multiple component instances:

  ```python
  # Wait for a click specifically on the project-form component
  await self.wait_for_event(
      "save",
      predicate=lambda kw: kw.get("component_id") == "project_form",
  )
  ```
- **Actor-mode views bypass the dispatch hook.** Tours don't work on views running under `use_actors = True`. The non-actor path is the default and is fully supported.
- **Handlers that fail parameter validation don't run** — meaning a waiter on them never resolves via the handler path, only via timeout. Make sure your `wait_for` handlers have matching client-side call shapes (the inline `dj-click="handler_name(args)"` syntax works fine).
- **Tours are single-user.** A tour running on user A's session doesn't affect user B. For instructor-led multi-user tours (one instructor drives many students), wait for Phase 4 (`broadcast_commands` + consent envelope) in v0.5.x.

## What's next

`TutorialMixin` is the capstone of ADR-002 Phase 1 — the three primitives (`push_commands`, `wait_for_event`, `TutorialMixin`) compose to let any djust app ship a real guided tour in under 50 lines of Python.

Future phases on the same foundation:

- **Phase 4 (v0.5.x)** — multi-user broadcast via `broadcast_commands(chain, group=...)` and the consent envelope from [ADR-005](../adr/005-consent-envelope-for-remote-control.md). Enables instructor-led classroom tours where one instructor drives 30 students' real LiveViews in lockstep.
- **Phase 5 (v0.5.x)** — LLM-driven `AssistantMixin` from [ADR-002 Phase 5](../adr/002-backend-driven-ui-automation.md#ai-interaction-llm-driven-ui-from-user-speech). Users can speak their intent and an LLM generates the tour steps on the fly, adapting to real user actions.
- **v0.6.0** — AI-generated UIs with capture-and-promote from [ADR-006](../adr/006-ai-generated-uis-with-capture-and-promote.md). Tours become one kind of generative UI among many.

## See also

- [Server-Driven UI](server-driven-ui.md) — `push_commands` and `wait_for_event` primitives
- [JS Commands](js-commands.md) — the 11-command vocabulary that tour chains use
- [ADR-002](../adr/002-backend-driven-ui-automation.md) — full design, motivation, alternatives, security model
