# `{% dj_activity %}` — Pre-Rendered Hidden Panels (React 19.2 Parity, v0.7.0)

React 19.2 shipped the `<Activity>` primitive for pre-rendering UI regions
that should keep their local state (form inputs, scroll offsets, transient
JS) when the user is not looking at them. `{% dj_activity %}` brings the
same semantics to djust: a server-rendered region that can be hidden via
the HTML `hidden` attribute without losing any client-side state.

## When to use it

- **Tabbed interfaces.** You want each tab's form to preserve what the
  user typed when they briefly switch away.
- **Multi-step flows.** Pre-rendering the next step keeps it instant on
  reveal while you can still show the current step.
- **Collapsible panels** where the user expects their in-progress input
  to still be there the next time they expand.

If you just need to toggle *whether* a region is rendered at all, use a
plain `{% if %}` — a re-render is fine. `{% dj_activity %}` matters when
re-rendering would drop local DOM state.

## Basic usage

```django
{% load live_tags %}

<button dj-click="switch_tab('profile')">Profile</button>
<button dj-click="switch_tab('notes')">Notes</button>

{% dj_activity "profile" visible=active_tab %}
    <input type="text" dj-input="set_name" value="{{ name }}"/>
{% enddj_activity %}

{% dj_activity "notes" visible=active_tab %}
    <textarea dj-input="set_notes">{{ notes }}</textarea>
{% enddj_activity %}
```

When `active_tab` is not `"profile"`, the profile panel's wrapper
receives the HTML `hidden` attribute plus `aria-hidden="true"` — but the
`<input>` element stays in the DOM with its current value. Switch back
to it and the value is still there.

> **Note on `visible` semantics.** The tag accepts any truthy expression.
> In the example above `visible=active_tab` is compared for truthiness
> against the string `"profile"` by the panel that wants to show when
> `active_tab == "profile"`. To make that clean, pass an explicit
> boolean from your handler — e.g. `self.profile_visible = (tab == "profile")` —
> and write `visible=profile_visible`. Both forms work; boolean assigns
> are clearer.

## Arguments

| Argument  | Type    | Default | Purpose                                      |
| --------- | ------- | ------- | -------------------------------------------- |
| `name`    | string  | —       | Required. Unique within the template.        |
| `visible` | boolean | `True`  | Whether the panel is user-visible this pass. |
| `eager`   | boolean | `False` | Keep dispatching events while hidden.        |

### `eager=True`

By default, events that fire inside a hidden activity are dropped
client-side (so a hidden panel can't send ghost inputs) and any pending
server-side events for that activity are queued until it becomes
visible. `eager=True` opts out: the activity always dispatches, even
while its wrapper is `hidden`. Useful for a background timer or polling
panel that should keep running.

You can also declare eager-activities on the LiveView class:

```python
class Dashboard(LiveView):
    eager_activities = frozenset({"live-ticker"})
```

This is equivalent to setting `eager=True` on every
`{% dj_activity "live-ticker" %}` block.

## Client API

The client exposes `window.djust.activityVisible(name)` which returns
the current DOM visibility of an activity by name. It reads directly
from the DOM (via a MutationObserver-maintained map), so it reflects
patches applied seconds ago as well as the initial render:

```js
if (window.djust.activityVisible('profile')) {
    startCamera();
}
```

A bubbling `djust:activity-shown` CustomEvent fires on the activity
root (and bubbles to `window`) whenever an activity flips from hidden
to visible:

```js
window.addEventListener('djust:activity-shown', (e) => {
    console.log('Activity shown:', e.detail.name);
});
```

## Server API — `ActivityMixin`

`LiveView` composes in `ActivityMixin` automatically. Use these methods
from event handlers:

```python
class TabbedView(LiveView):
    def switch_tab(self, tab: str = "", **kwargs):
        # Either assign directly, or use the mixin helper:
        self.set_activity_visible("profile", tab == "profile")
        self.set_activity_visible("notes",   tab == "notes")

    def check_status(self):
        if self.is_activity_visible("notes"):
            # Notes panel is currently shown to the user.
            ...
```

## Deferred events

When a client event fires in a hidden (non-eager) activity and somehow
slips past the client-side gate (e.g. a mid-morph race), the server:

1. Drops the event into a per-activity FIFO queue (capped at 100 by
   default; override via `activity_event_queue_cap`).
2. Replies with a no-op so the client's loading state clears.
3. On the next handler that flips the activity to `visible=True`,
   drains the queue in FIFO order. Each deferred event is dispatched
   via the WebSocket consumer's `_dispatch_single_event` helper and
   awaited inline, so every queued event completes inside the SAME
   WebSocket round-trip as the handler that flipped the panel visible
   — never as a delayed fire-and-forget task.

## Comparison with nearby features

| Feature                 | Preserves local DOM | Re-renders body | When to pick                             |
| ----------------------- | ------------------- | --------------- | ---------------------------------------- |
| `{% if %}`              | No                  | Yes             | Simple conditional content               |
| `{% dj_activity %}`     | Yes                 | Yes (while visible) | Tabbed UIs, multi-step forms         |
| `{% live_render %}`     | Yes (own view)      | Yes (own view)  | Embedding a full child LiveView          |
| `dj-prefetch`           | N/A                 | Fetches ahead   | Pre-loading the *next page*              |
| Sticky LiveView         | Yes (across routes) | Yes             | Persistent widget across route changes   |

## Gotchas

### Hidden `<input>` still submits

The HTML `hidden` attribute suppresses rendering but does **not** remove
an element from its form's submission. If a hidden activity contains a
form input, that input's value still posts with the form. Workarounds:

- Put each panel's inputs in a separate `<form>`.
- Add `disabled` to the inputs when hiding (but note: `disabled` inputs
  are skipped from submission — you may want `readonly` instead).
- Only submit a subset server-side (validate on `handle_event`).

### Nested activities

An outer `hidden` wrapper visually hides every descendant regardless of
their own declared state. `is_activity_visible` on the server returns
only the *declared* state of the named activity — it does not walk
ancestors. The client gate handles nesting in two steps:

1. **Drop if any hidden non-eager ancestor exists.** The event-dispatch
   gate uses `closest('[data-djust-activity][hidden]:not([data-djust-eager="true"])')`
   on the trigger element. A match *anywhere* up the chain drops the
   event — so an `<outer hidden><inner visible>` combination still
   drops, even though the inner wrapper is marked visible.
2. **Otherwise, stamp `_activity` with the closest ancestor.** Once no
   hidden ancestor is found, the gate calls `closest('[data-djust-activity]')`
   and attaches that wrapper's name as `_activity` on the outbound
   payload. The server uses that name for per-activity routing /
   deferral on the rare mid-morph race where the client gate is stale.

### Event ordering across show/hide

Deferred events drain in FIFO order per-activity. If you need strict
global ordering with events from other activities, don't rely on the
queue — flip the activity visible first and let the client send new
events.

## Security model

Activity gating is a **UX-correctness feature, not an auth boundary.**
Treat it as "don't waste round-trips on events the user can't see" —
never as "prevent hidden handlers from running."

- **Real auth still runs on every event.** `_validate_event_security`,
  `@permission_required`, handler allowlists, and rate limits apply to
  deferred/replayed events exactly as they apply to live ones. A user
  who cannot call `admin_only()` cannot call it by tricking the client
  into marking the trigger as an activity event.
- **The `_activity` param is client-supplied.** A client can omit it,
  forge it to an unknown name, or point it at a different activity.
  Absent or unknown `_activity` → server treats the call as visible
  and dispatches normally. This is deliberate — the server must not
  rely on the client-side gate for correctness.
- **If a handler must refuse to run when a panel is hidden for a
  business reason**, add an explicit guard in the handler body:

  ```python
  @event_handler()
  def purchase(self, **kwargs):
      if not self.is_activity_visible("checkout"):
          return  # or raise / log
      ...
  ```

  Relying on the client-side gate or on `_activity` routing for this
  is a bug — you'll ship a bypass.

## Troubleshooting

| Check  | Meaning                                               | Fix                                                   |
| ------ | ----------------------------------------------------- | ----------------------------------------------------- |
| `A070` | `{% dj_activity %}` is missing a `name` argument.     | Add a name: `{% dj_activity "panel" %}`.              |
| `A071` | Two `{% dj_activity %}` blocks share a name in one template. | Rename one — names must be unique per template. |

Run `python manage.py check --tag djust` to surface both.

## See also

- [Prefetch](prefetch.md) — pre-load the next page (sibling "make nav
  feel instant" primitive shipped in the same v0.7.0 batch).
- [Sticky LiveViews](sticky-liveviews.md) — state persistence across
  routes; includes the ADR-011 section on how Sticky and Activity
  compose (within-page show/hide vs across-page preservation).
