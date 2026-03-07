# TurboNav Integration Guide

This guide covers integrating djust LiveViews with TurboNav for SPA-style navigation in Django projects.

## What is TurboNav?

TurboNav intercepts link clicks and form submissions, fetches the new page via AJAX, and swaps the `<main>` element's innerHTML — avoiding full page reloads. This gives users a faster, app-like navigation experience while keeping server-rendered HTML as the source of truth.

djust works with TurboNav out of the box. When TurboNav swaps page content, djust automatically tears down old WebSocket connections and reinitializes LiveViews found in the new content.

## How It Works

1. User clicks a link
2. TurboNav fetches the target page via `fetch()`
3. TurboNav swaps `<main>` innerHTML with the new page content
4. TurboNav runs `loadPageScripts()` for any `<script data-turbo-track="reload">` tags
5. TurboNav fires a `turbo:load` event
6. djust listens for `turbo:load`, disconnects any existing WebSocket, and reinitializes LiveViews in the new DOM

## Setup

### 1. Add TurboNav to Your Base Template

Include the TurboNav script in your base template's `<head>`:

```html
<head>
    <script src="{% static 'turbo.js' %}" data-turbo-track="reload"></script>
    <script src="{% static 'djust/client.js' %}" data-turbo-track="reload"></script>
</head>
```

The `data-turbo-track="reload"` attribute tells TurboNav's `loadPageScripts()` to manage these scripts across navigations.

### 2. Configure the Swap Target

TurboNav swaps the contents of `<main>`. Structure your base template accordingly:

```html
<body>
    <nav><!-- persistent navigation --></nav>

    <main>
        {% block content %}{% endblock %}
    </main>

    <footer><!-- persistent footer --></footer>
</body>
```

Elements outside `<main>` (nav, footer, sidebars) persist across navigations.

### 3. Place LiveViews Inside Main

Your LiveView templates render inside `{% block content %}`, so they naturally land inside `<main>`:

```html
{% block content %}
<div dj-view="{{ view_id }}">
    <!-- LiveView content -->
</div>
{% endblock %}
```

## How djust Handles Navigation

When TurboNav fires `turbo:load`, djust runs `reinitLiveViewForTurboNav()` which:

1. **Disconnects the existing WebSocket** — closes the old connection cleanly
2. **Resets client VDOM version** — prevents stale diff conflicts
3. **Clears lazy hydration state** — resets `dj-lazy` tracking
4. **Stamps root attributes** — auto-applies `data-djust-root` on new content
5. **Scans for LiveView containers** — finds all `[dj-view]` elements in the new DOM
6. **Opens a new WebSocket** — connects to the server for the new page's LiveViews
7. **Re-binds event handlers** — attaches `dj-click`, `dj-submit`, etc. to new elements
8. **Cleans up poll intervals** — stops old `dj-poll` timers before creating new ones

### Initialization Order

djust guards against race conditions between `DOMContentLoaded` and `turbo:load`:

- If `turbo:load` fires before djust has initialized (first page load), reinit is deferred
- If `turbo:load` fires after initialization, reinit runs immediately
- Duplicate WebSocket connections are prevented by disconnecting before reconnecting

## Inline Scripts

TurboNav swaps innerHTML, which means inline `<script>` tags in the new content are **not executed automatically** by the browser. TurboNav's `loadPageScripts()` handles `<script data-turbo-track="reload">` tags, but other inline scripts require explicit execution.

If you need inline scripts in a LiveView page:

```html
<!-- Option 1: Use data-turbo-track -->
<script data-turbo-track="reload">
    // This will be executed by loadPageScripts
</script>

<!-- Option 2: Move logic to a djust event handler (preferred) -->
<button dj-click="do_something">Click me</button>
```

Prefer moving client-side logic into djust event handlers when possible.

## Known Caveats

### DOMContentLoaded Does Not Fire

After TurboNav swaps content, `DOMContentLoaded` does not fire again. Any code that depends on this event will not run on subsequent navigations. djust handles this internally by checking `document.readyState` and using the `turbo:load` event.

If you have non-djust JavaScript that relies on `DOMContentLoaded`, listen for `turbo:load` as well:

```javascript
function initMyWidget() {
    // Widget initialization code
}

document.addEventListener('DOMContentLoaded', initMyWidget);
window.addEventListener('turbo:load', initMyWidget);
```

### Debug Panel State

The djust debug panel does not persist its state across TurboNav navigations. Switching pages resets the panel. This is tracked as a remaining item in the roadmap.

### Pages Without LiveViews

If TurboNav navigates to a page with no `[dj-view]` containers, djust disconnects the WebSocket and does not reconnect. This is the correct behavior — no LiveViews means no WebSocket needed.

## Design Decision: Separate Integration

TurboNav is **not bundled** with djust. It remains a separate library that users opt into. This decision was made because:

- Not all Django projects need SPA-style navigation
- TurboNav adds complexity (script execution, event lifecycle) that not every app benefits from
- Keeping them separate allows each to evolve independently
- djust's `turbo:load` handler is minimal (~60 lines) and adds no overhead when TurboNav is absent

If TurboNav is not present, the `turbo:load` event never fires and djust operates normally with full page reloads.

## Troubleshooting

**LiveView not initializing after navigation**: Ensure your LiveView markup is inside `<main>`. TurboNav only swaps `<main>` content.

**Duplicate WebSocket connections**: This was fixed in v0.3.6. Update to the latest version. The fix ensures `reinitLiveViewForTurboNav()` always disconnects before reconnecting.

**Scripts not executing**: Add `data-turbo-track="reload"` to any `<script>` tags that need to run after navigation.

**Triple initialization on first load**: Fixed in v0.3.6. The `pendingTurboReinit` guard prevents djust from reinitializing multiple times when both `DOMContentLoaded` and `turbo:load` fire.
