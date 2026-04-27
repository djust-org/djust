---
title: "Sticky LiveViews"
slug: sticky-liveviews
section: guides
order: 6.8
level: intermediate
description: "Keep a LiveView mounted across live_redirect navigations — audio players, sidebars, notification centers, app-shell widgets"
---

# Sticky LiveViews

djust LiveViews normally unmount and re-mount on every ``live_redirect``.
Sticky LiveViews opt specific children out of that lifecycle: mark the
class with ``sticky = True``, embed it via ``{% live_render ... sticky=True %}``,
and declare a ``<div dj-sticky-slot="...">`` in each destination layout
where it should re-attach. State, form values, scroll, focus, and
background tasks all survive the navigation — the Python instance is
the same object before and after.

Use this for app-shell widgets: audio players, sidebars, notification
centers, wizard preview panes, anything that must not flicker when the
user clicks between pages.

See also: [ADR-011](../../adr/011-sticky-liveviews.md) for the full
wire protocol, security model, and failure-mode catalog.

## Quick start

Persistent audio player across Dashboard and Settings.

### 1. Define the sticky LiveView class

```python
# myapp/views.py
from djust import LiveView
from djust.decorators import event_handler

class AudioPlayerView(LiveView):
    sticky = True
    sticky_id = "audio-player"  # defaults to class name lowercased
    template_name = "myapp/audio_player.html"

    def mount(self, request, **kwargs):
        self.track_title = "Nothing playing"
        self.is_playing = False

    @event_handler
    def toggle_play(self, **kwargs):
        self.is_playing = not self.is_playing
```

### 2. Embed it in a page template

```django
{# myapp/templates/myapp/dashboard.html #}
{% load live_tags %}
<div dj-root>
    <h1>Dashboard</h1>
    <div class="metrics">...</div>

    {% live_render "myapp.views.AudioPlayerView" sticky=True %}
</div>
```

The tag validates at render time that ``AudioPlayerView.sticky == True``
(raises ``TemplateSyntaxError`` if not) and emits a wrapper:

```html
<div dj-view dj-sticky-view="audio-player" dj-sticky-root
     data-djust-embedded="child_1">
    <!-- AudioPlayerView's rendered HTML -->
</div>
```

### 3. Declare the slot in other layouts

```django
{# myapp/templates/myapp/settings.html #}
<div dj-root>
    <h1>Settings</h1>
    <form>...</form>

    <div dj-sticky-slot="audio-player"></div>
</div>
```

When the user navigates Dashboard → Settings via ``live_redirect``,
the audio player's DOM subtree detaches from Dashboard's output,
survives the tear-down, and re-attaches at the ``dj-sticky-slot`` in
Settings. Same ``<audio>`` element, same playback position, same
Python instance — ``is_playing`` and ``track_title`` unchanged.

### Return-trip navigation (Dashboard → Settings → Dashboard)

The destination page can declare the same sticky inline via
``{% live_render ... sticky=True %}`` even when it's *also* the page
that originally mounted the sticky. As of v0.9.0 (ADR-014), the tag
auto-detects the carried-over survivor at template-render time, emits
a ``<dj-sticky-slot>`` placeholder rather than a fresh subtree, and
re-registers the survivor onto the new parent without re-running its
``mount()``. Dashboard → Settings → Dashboard preserves the audio
playback identical to Dashboard → Settings → Reports.

The only path that fresh-mounts a sticky is the one where the consumer
genuinely has no survivor — first navigation, HTTP GET / hard reload,
or after an intermediate page that omitted the slot AND the inline tag
(see "What happens when a slot is missing" below).

## The `sticky` class attribute

Two class attributes control sticky behavior:

| Attribute | Default | Meaning |
|---|---|---|
| `sticky` | `False` | Opt-in. Must be `True` for `{% live_render ... sticky=True %}` to accept this class. |
| `sticky_id` | `None` (→ class name lowercased) | Stable identifier shared server ↔ client. Keys the stash on the client and the `_sticky_preserved` dict on the server. |

Why two attributes? The class decides whether preservation is *possible*
(`sticky = True` means "this view's design supports re-registering on
a new parent — background tasks and DOM subtree are safe to carry
across"). The `sticky_id` decides *which slot* to re-attach to.

### Why the class must opt in

Not every LiveView is safe to make sticky. Views that hold references
to the old request / resolver_match / parent's channel group will
misbehave when the parent changes mid-session. Making opt-in explicit
ensures the class author has thought about the contract.

## The `{% live_render 'path' sticky=True %}` tag

Same dotted-path resolution as Phase A's non-sticky `live_render`:

```django
{% live_render "myapp.views.AudioPlayerView" sticky=True %}
{% live_render "myapp.views.NotificationCenter" sticky=True notifications_channel="global" %}
```

The tag:

1. Resolves the dotted path via `django.utils.module_loading.import_string`.
2. Validates against `settings.DJUST_LIVE_RENDER_ALLOWED_MODULES` (if set).
3. Asserts the resolved class has `sticky = True` (else `TemplateSyntaxError`).
4. Mounts the child, captures its rendered HTML.
5. Wraps the HTML in a `<div dj-view dj-sticky-view="<id>" dj-sticky-root ...>`.
6. Stamps `data-djust-embedded` onto every dj-event-bearing tag inside.

Any `kwargs` after `sticky=True` pass through to the child's `mount()`.

### Allowlist mismatch

If `DJUST_LIVE_RENDER_ALLOWED_MODULES` is set (a list or tuple of
module prefixes) and the dotted path doesn't match any prefix, the
tag raises `TemplateSyntaxError` — same as non-sticky `live_render`.

## `[dj-sticky-slot]` markers

Destination layouts declare re-attachment points with
`<div dj-sticky-slot="<id>">`. The element can be empty (the client
replaces it) or contain fallback content for the pre-JS render.

```django
<div dj-sticky-slot="audio-player"></div>
<div dj-sticky-slot="notification-center"></div>
```

### What happens when a slot is missing

If the destination layout does NOT contain
`<div dj-sticky-slot="audio-player">`, the server's post-render slot
scan in `handle_mount` drops the sticky from its survivor list. The
client's `reattachStickyAfterMount` also defends against drift: any
stashed subtree with no matching slot fires
`djust:sticky-unmounted` with `reason='no-slot'` and is dropped.

This is the canonical "sticky leaves the app shell" path — a
`ReportsView` that doesn't embed the audio player at all means the
user sees the audio player die when they navigate there. Design
your layouts to declare slots for every sticky you want preserved.

## Lifecycle events

The sticky subtree dispatches CustomEvents you can listen to:

| Event | When | `detail` |
|---|---|---|
| `djust:sticky-preserved` | Successful reattach at a slot in the new layout | `{sticky_id}` |
| `djust:sticky-unmounted` | Sticky was discarded | `{sticky_id, reason}` where `reason` is `'server-unmount'`, `'no-slot'`, or `'auth'` |

Events dispatch on the sticky subtree element. Since CustomEvents
bubble, `document.addEventListener('djust:sticky-preserved', ...)`
catches them after the reattach lands the subtree in the new DOM.

### Example listener

```html
<script>
document.addEventListener('djust:sticky-preserved', (e) => {
    console.log('Sticky', e.detail.sticky_id, 'survived navigation');
    // Re-initialize anything that needs the subtree to be in
    // the live DOM (e.g. a chart library that probes its container).
});
document.addEventListener('djust:sticky-unmounted', (e) => {
    if (e.detail.reason === 'auth') {
        console.log('Sticky', e.detail.sticky_id, 'revoked by auth re-check');
    }
});
</script>
```

## Auth re-check semantics

Every `live_redirect` re-runs authentication against the destination
URL via `djust.auth.check_view_auth_lightweight(child, new_request)`
for every staged sticky. The helper:

* Runs the sticky's `check_permissions(request)` if defined.
* Checks `permission_required` via Django's permission system.
* Validates `login_required` against `new_request.user`.
* Returns `True` for allow, `False` for deny.

A `False` return unmounts the sticky: `_on_sticky_unmount()` is called
on the instance (default: cancels pending `start_async` tasks), the
child is dropped from the survivor set, and the client receives a
`djust:sticky-unmounted` event with `reason='server-unmount'` once
`sticky_hold` arrives.

### Why lightweight?

The full `check_view_auth` path raises `PermissionDenied` or a redirect
response on deny. Sticky staging is a best-effort background check —
we want a boolean outcome so the consumer can fall through to mount
the new view regardless. `check_view_auth_lightweight` does the same
checks and returns `bool`.

### What `PermissionDenied` in `check_permissions` looks like

If your sticky's `check_permissions(request)` raises `PermissionDenied`
(or returns `False`), the sticky unmounts. Think of this as: *"the
user just clicked into an area where this widget shouldn't show its
state."* A notification center that reveals cross-tenant data should
deny on `request.user.tenant != self.tenant` — the re-check catches
any retained-after-logout edge cases.

## Common patterns

### Pattern 1: App-shell with global sticky widgets

```django
{# myapp/templates/myapp/shell.html — shared layout extends #}
{% extends "base.html" %}
{% load live_tags %}
{% block body %}
    <aside>... sidebar content ...</aside>
    <main dj-root>
        {% block page %}{% endblock %}
        <div dj-sticky-slot="audio-player"></div>
        <div dj-sticky-slot="notification-center"></div>
    </main>
    {# First page render embeds the stickies. Subsequent pages inherit the slots. #}
    {% if first_visit %}
        {% live_render "myapp.views.AudioPlayerView" sticky=True %}
        {% live_render "myapp.views.NotificationCenterView" sticky=True %}
    {% endif %}
{% endblock %}
```

### Pattern 2: Wizard with sticky preview pane

```django
{# Each step of a 3-step wizard shows the same live preview. #}
<div dj-root>
    <h1>Step {{ step }}/3</h1>
    <form>...</form>

    <aside>
        <h2>Preview</h2>
        {% if step == 1 %}
            {% live_render "myapp.wizard.PreviewPane" sticky=True product=product %}
        {% else %}
            <div dj-sticky-slot="wizard-preview"></div>
        {% endif %}
    </aside>
</div>
```

The preview is first embedded on step 1 (which defines it). Steps 2
and 3 declare only the slot, and the sticky survives with the user's
last state as they navigate forward/back.

## Limitations

1. **No survival across WS reconnect**. When the WS closes (tab
   backgrounded long enough for idle timeout, network blip, server
   restart), the sticky dies on the server side and the client's
   stash is cleared. Re-mount happens from scratch on reconnect.
   In `DEBUG`, the server logs a warning if a sticky's state is
   non-trivial and the reconnect dropped it.
2. **No cross-tab sync**. Two tabs of the same app don't share a
   sticky instance. Each tab has its own WS and its own sticky.
3. **`<head>` merging is not implemented**. If your sticky needs a
   specific stylesheet or font, include it in the initial layout's
   `<head>` — subsequent ``live_redirect``s don't swap the `<head>`,
   so this is a one-time decision.
4. **Sticky classes must be importable by dotted path**. The
   `{% live_render %}` tag uses `django.utils.module_loading.import_string`.
   Module-level classes only — no inner classes.
5. **Rendering errors on the sticky unmount it**. If the sticky's
   re-render raises during a `sticky_update`, the exception bubbles
   and the next navigation will unmount it. Keep sticky render paths
   defensive.

## Debugging

### Enable verbose logs

```javascript
globalThis.djustDebug = true;
```

This enables stash operation logs — every `stashStickySubtrees`,
`reconcileStickyHold`, `reattachStickyAfterMount`, `clearStash`
call logs what it did. Useful for "why did my sticky unmount?"
questions.

### Inspect DOM attributes

In the browser devtools:

* `[dj-sticky-view="<id>"]` — the outer wrapper of a sticky that's
  currently live.
* `[dj-sticky-slot="<id>"]` — a declared re-attachment point in a
  layout (replaced by the subtree on reattach, so only visible when
  the sticky has unmounted or is between stashes).
* `[dj-sticky-root]` — defensive attribute that marks "not a
  page-root view" to client code that walks `[dj-view]`.

### Inspect the stash

In dev:

```javascript
console.log(djust.stickyPreserve._stash);  // Map<string, Element>
```

The `_stash` underscore prefix signals "dev only — do not rely in
production code". Use the lifecycle events instead.

## FAQ

**Q: Can I change `sticky_id` dynamically?**

No. `sticky_id` is a static identity — the server uses it to key
the staging dict, the client uses it to key the stash and match
slots. Changing it mid-session would orphan the pre-change subtree.

**Q: Does `start_async` survive?**

Yes. The task handle lives on the child instance (Python object
identity is preserved across the live_redirect). `start_async`
tasks keep executing on the same background threadpool against the
same view instance. Same for `@background`-decorated handlers.

**Q: What if two pages declare different `sticky_id`s for the same view class?**

They get separate instances. The `sticky_id` is the identity — two
`{% live_render "MyView" sticky=True %}` with default `sticky_id` both
resolve to `"myview"` (the class name lowercased), but the
TemplateSyntaxError on collision prevents that within one template.
Across two templates, each embedding with its own `sticky_id` means
each page has its own sticky.

**Q: Can I call `self.live_redirect()` from a sticky?**

Yes. Inbound events to the sticky route through the parent's
registry by `view_id`. The sticky's `live_redirect` triggers the
parent's navigation, which runs the preservation cycle — the
sticky preserves itself across its own redirect.

**Q: How do I force-unmount a sticky?**

Call `self.cancel_async_all()` and then `self.live_redirect(...)` to
a page that omits the slot. The server's post-render scan will
drop the sticky and emit `djust:sticky-unmounted reason='no-slot'`.
