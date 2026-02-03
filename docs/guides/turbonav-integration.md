# TurboNav Integration Guide

This guide explains how to integrate djust LiveViews with TurboNav (or similar SPA-style navigation libraries like Turbo Drive) in existing Django sites.

## What is TurboNav?

TurboNav is a lightweight JavaScript library that provides SPA-style navigation without full page reloads. When a user clicks a link:

1. TurboNav intercepts the click
2. Fetches the new page via AJAX
3. Swaps the `<main>` element's innerHTML with the new content
4. Updates the browser history

This creates a smooth, fast navigation experience while keeping server-rendered HTML.

## Why Use TurboNav with djust?

Combining TurboNav with djust gives you the best of both worlds:

- **Fast navigation** between pages without full reloads
- **Real-time interactivity** on pages with LiveViews
- **Progressive enhancement** — LiveViews only activate where needed
- **SEO-friendly** — all pages are server-rendered HTML

## The Contract

Understanding the contract between TurboNav and djust is critical:

### What TurboNav Does

1. Intercepts link clicks and form submissions
2. Fetches new page HTML via fetch/XHR
3. Swaps `<main>` innerHTML with the new page's `<main>` content
4. Fires `turbo:load` event after the swap
5. Handles scripts marked with `data-turbo-track="reload"`

### What djust Expects

1. The `turbo:load` event signals navigation completed
2. djust reinitializes by:
   - Disconnecting existing WebSocket connections
   - Clearing old heartbeat intervals
   - Finding new `[data-djust-view]` containers
   - Establishing new WebSocket connections
   - Rebinding event handlers

### Key Behaviors

| Scenario | djust Behavior |
|----------|----------------|
| Navigate to page with LiveView | Connect WebSocket, mount view |
| Navigate away from LiveView | Disconnect WebSocket, cleanup |
| Navigate between two LiveViews | Disconnect old, connect new |
| Navigate to page without LiveView | No WebSocket, minimal JS |
| Rapid navigation (spam clicks) | Guards prevent duplicate connections |

## Setup Instructions

### 1. Include djust Client in Base Template

Add the djust client script to your base template **once**, with `data-turbo-track="reload"`:

```html
<!-- base.html -->
<!DOCTYPE html>
<html>
<head>
    <title>{% block title %}My Site{% endblock %}</title>
    {% load static %}
    
    <!-- TurboNav (or Turbo Drive) -->
    <script src="{% static 'turbonav/turbonav.js' %}" data-turbo-track="reload"></script>
    
    <!-- djust client - must be in head with turbo-track for proper reloading -->
    <script src="{% static 'djust/client.js' %}" data-turbo-track="reload"></script>
</head>
<body>
    <nav>
        <!-- Navigation links work normally -->
        <a href="{% url 'home' %}">Home</a>
        <a href="{% url 'dashboard' %}">Dashboard</a>
    </nav>
    
    <main>
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

### 2. Create LiveView Pages

LiveView pages extend the base template normally:

```html
<!-- dashboard.html -->
{% extends "base.html" %}

{% block content %}
<div data-djust-view="dashboard.views.DashboardLiveView" data-djust-root>
    <!-- LiveView content rendered here -->
    {{ content }}
</div>
{% endblock %}
```

### 3. Create Regular Pages

Regular pages work without any special setup:

```html
<!-- about.html -->
{% extends "base.html" %}

{% block content %}
<h1>About Us</h1>
<p>This is a regular Django template with no LiveView.</p>
{% endblock %}
```

## How It Works

### Initial Page Load

1. Browser loads the page normally
2. djust client.js runs, finds `[data-djust-view]` containers
3. WebSocket connection established
4. LiveView mounted and ready

### Navigation to Another LiveView

1. User clicks a link
2. TurboNav fetches new page HTML
3. TurboNav swaps `<main>` innerHTML
4. TurboNav fires `turbo:load` event
5. djust's `turbo:load` handler:
   - Calls `reinitLiveViewForTurboNav()`
   - Disconnects old WebSocket
   - Clears old heartbeat interval
   - Finds new `[data-djust-view]` container
   - Creates new WebSocket connection
   - Mounts new LiveView

### Navigation Away from LiveView

1. User clicks link to non-LiveView page
2. TurboNav swaps content
3. djust's `turbo:load` handler:
   - Disconnects WebSocket
   - No new container found
   - No new connection established
   - Page works as normal Django template

## Common Pitfalls and Solutions

### 1. Triple Initialization (Fixed in v0.4)

**Symptom:** Console shows multiple "[LiveView] Initializing..." messages on navigation.

**Cause:** Heartbeat intervals weren't being cleared properly, and multiple WebSocket connections were created.

**Solution:** v0.4 includes guards against duplicate initialization:
- Heartbeat interval is now stored and cleared on disconnect
- `reinitInProgress` flag prevents concurrent reinitializations
- `connect()` checks for existing connections before creating new ones

### 2. Duplicate WebSocket Connections

**Symptom:** Multiple WebSocket connections in browser DevTools, high server load.

**Cause:** Rapid navigation before previous connection fully closed.

**Solution:** v0.4 adds guards:
```javascript
// In connect()
if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || 
                this.ws.readyState === WebSocket.OPEN)) {
    console.log('[LiveView] WebSocket already connected, skipping');
    return;
}

// In disconnect()
if (this.ws.readyState === WebSocket.CONNECTING || 
    this.ws.readyState === WebSocket.OPEN) {
    this.ws.close();
}
```

### 3. Scripts Not Executing After Navigation

**Symptom:** Inline scripts in LiveView templates don't run after TurboNav navigation.

**Cause:** `innerHTML` assignment doesn't execute `<script>` tags.

**Solution:** Use djust's event system instead of inline scripts:

```html
<!-- Don't do this -->
<script>
    alert('This won't run after navigation!');
</script>

<!-- Do this instead -->
<div dj-hook="my-component">
    <!-- Content -->
</div>

<script data-turbo-track="reload">
    window.djust.hooks.register('my-component', {
        mounted(el) {
            console.log('Component mounted!');
        }
    });
</script>
```

### 4. Event Handlers Not Working After Navigation

**Symptom:** `dj-click` and other handlers stop working after navigation.

**Cause:** Event bindings lost when DOM replaced, not rebound.

**Solution:** This is handled automatically by djust. The `reinitLiveViewForTurboNav()` function calls `bindLiveViewEvents()` after each navigation.

### 5. Form State Lost on Navigation

**Symptom:** User input disappears when navigating away and back.

**Solution:** Use djust's draft mode for important forms:

```html
<form data-draft-enabled data-draft-key="my-form">
    <input type="text" name="title" data-draft="true">
    <textarea name="content" data-draft="true"></textarea>
</form>
```

### 6. CSS Transitions Broken

**Symptom:** CSS animations/transitions don't play on navigated content.

**Cause:** Elements are replaced, not animated.

**Solution:** Use TurboNav's view transitions API if available, or add classes after navigation:

```javascript
window.addEventListener('turbo:load', () => {
    document.querySelector('main').classList.add('page-enter');
});
```

## Debug Checklist

If things aren't working, check:

1. **Console logs:** Look for `[LiveView:TurboNav]` prefixed messages
2. **WebSocket tab:** Only one connection should be active per LiveView page
3. **Network tab:** Verify TurboNav is fetching pages (XHR/fetch requests)
4. **Elements tab:** Confirm `[data-djust-view]` containers exist after navigation

### Enabling Debug Mode

```javascript
// In browser console
window.djustDebug = true;
```

This enables verbose logging for:
- WebSocket connect/disconnect events
- Heartbeat start/stop
- Event handler binding
- TurboNav reinit process

## Best Practices

### 1. Keep LiveViews Focused

Don't make your entire app a single LiveView. Use LiveViews for interactive sections:

```html
<main>
    <!-- Static content -->
    <h1>Dashboard</h1>
    
    <!-- Interactive LiveView widget -->
    <div data-djust-view="widgets.RealtimeStats" data-djust-root>
        {{ stats_content }}
    </div>
    
    <!-- More static content -->
    <footer>© 2024</footer>
</main>
```

### 2. Use Lazy Hydration for Below-the-Fold Content

```html
<div data-djust-view="comments.CommentsList" 
     data-djust-lazy 
     data-djust-root>
    <!-- Only connects WebSocket when scrolled into view -->
</div>
```

### 3. Handle Loading States

Show loading indicators during navigation:

```css
/* Show spinner during TurboNav fetch */
html.turbo-loading main {
    opacity: 0.5;
    pointer-events: none;
}
```

### 4. Test Navigation Scenarios

Critical scenarios to test:
- Navigate from LiveView → LiveView
- Navigate from LiveView → static page
- Navigate from static page → LiveView
- Use browser back/forward buttons
- Rapid clicking between pages
- Navigate during active WebSocket events

## Troubleshooting

### "WebSocket not connecting after navigation"

1. Check `turbo:load` event is firing (add `console.log` in handler)
2. Verify `[data-djust-view]` container exists in new content
3. Check for JavaScript errors in console
4. Ensure djust client.js has `data-turbo-track="reload"`

### "Old data showing after navigation"

1. Server-side: Ensure each view has fresh state
2. Client-side: Clear any cached data in `reinitLiveViewForTurboNav()`
3. Check `clientVdomVersion` is being reset

### "Memory leak / high CPU usage"

1. Check for accumulating heartbeat intervals (should be exactly one)
2. Verify WebSocket connections are closing properly
3. Use browser DevTools Performance tab to profile

## Example: Full Integration

Here's a complete example of a Django site with TurboNav and djust:

```python
# views.py
from djust import LiveView, event_handler

class ChatLiveView(LiveView):
    template_name = "chat/room.html"
    
    def mount(self, room_id):
        self.room_id = room_id
        self.messages = Message.objects.filter(room_id=room_id)[:50]
    
    @event_handler
    def send_message(self, text):
        Message.objects.create(room_id=self.room_id, text=text)
        self.messages = Message.objects.filter(room_id=self.room_id)[:50]
```

```html
<!-- templates/base.html -->
<!DOCTYPE html>
<html>
<head>
    {% load static %}
    <script src="{% static 'turbonav.js' %}" data-turbo-track="reload"></script>
    <script src="{% static 'djust/client.js' %}" data-turbo-track="reload"></script>
</head>
<body>
    <nav>
        <a href="/">Home</a>
        <a href="/chat/general/">General Chat</a>
        <a href="/chat/random/">Random Chat</a>
    </nav>
    <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

```html
<!-- templates/chat/room.html -->
{% extends "base.html" %}

{% block content %}
<div data-djust-view="chat.views.ChatLiveView" data-djust-root>
    <h2>Chat Room: {{ room_id }}</h2>
    <div class="messages">
        {% for msg in messages %}
            <div class="message">{{ msg.text }}</div>
        {% endfor %}
    </div>
    <form dj-submit="send_message">
        <input type="text" name="text" placeholder="Type a message...">
        <button type="submit">Send</button>
    </form>
</div>
{% endblock %}
```

With this setup:
- Clicking between chat rooms seamlessly transitions
- WebSocket connects/disconnects automatically
- Chat functionality works in each room
- Browser back/forward works correctly
- No page flicker or reload

## FAQ

**Q: Should TurboNav ship with djust?**

A: TurboNav remains a separate integration concern. djust provides hooks (`turbo:load` handler, `reinitLiveViewForTurboNav()`) but doesn't bundle TurboNav. This gives you flexibility to:
- Use Turbo Drive instead
- Use a different SPA navigation library
- Build your own navigation solution

**Q: Can I use Hotwire Turbo instead of TurboNav?**

A: Yes! djust's TurboNav integration works with any library that fires a `turbo:load` event after navigation. Hotwire Turbo Drive is fully compatible.

**Q: What about Turbo Frames?**

A: Turbo Frames work differently (scoped updates vs full page). djust LiveViews can work alongside Turbo Frames, but each has its own scope. For real-time updates within a frame, consider a LiveView inside the frame.

**Q: How do I disable TurboNav for certain links?**

A: Add `data-turbo="false"` to links that should do full page loads:

```html
<a href="/logout/" data-turbo="false">Logout</a>
```
