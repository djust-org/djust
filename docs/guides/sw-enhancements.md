# Service Worker Enhancements for djust

Beyond offline support, the service worker can significantly improve djust's LiveView architecture by intercepting network requests, caching intelligently, and bridging connection gaps. This document details seven enhancement areas, each designed to leverage the SW's position between the browser and server.

## Overview

djust's architecture — server-rendered HTML over WebSocket with VDOM diffing — creates unique opportunities for SW optimization that don't exist in traditional SPA frameworks. The SW sits at the network layer and can cache, prefetch, and replay in ways that complement the server-driven model.

| Enhancement | Impact | Complexity | Priority |
|---|---|---|---|
| Instant Page Shell | High | Medium | P0 |
| Prefetch on Hover | High | Low | P0 |
| Smart Static Asset Caching | Medium | Low | P1 |
| WebSocket Reconnection Bridge | High | High | P1 |
| VDOM Patch Caching | Medium | Medium | P2 |
| LiveView State Snapshots | Medium | High | P2 |
| Request Batching | Low | Medium | P3 |

---

## 1. Instant Page Shell (App Shell Pattern)

### Problem

Every djust page navigation requires a full server round-trip. Even with fast Rust rendering (~2ms), network latency adds 50-200ms before the user sees anything.

### Solution

Cache the "shell" of each page (the `<head>`, nav, sidebar, footer — everything outside `<main>`) and serve it instantly from the SW. When the server response arrives, the SW (or client JS) swaps in the dynamic `<main>` content.

### How It Works

```
User clicks link
  → SW intercepts fetch
  → SW returns cached shell HTML immediately (0ms)
  → SW fetches full page from server in background
  → Client swaps <main> content when server responds
  → User sees instant layout, then content fills in
```

### Implementation Approach

```javascript
// In service worker
self.addEventListener('fetch', (event) => {
  if (event.request.mode === 'navigate') {
    event.respondWith(
      // Race: cached shell vs network
      caches.match('/shell').then(cachedShell => {
        const networkFetch = fetch(event.request);
        if (cachedShell) {
          // Serve shell immediately, swap content when ready
          event.waitUntil(networkFetch.then(response => {
            // Post message to client with full content
            self.clients.matchAll().then(clients => {
              clients.forEach(c => c.postMessage({
                type: 'shell-swap',
                url: event.request.url,
                html: response.clone().text()
              }));
            });
          }));
          return cachedShell;
        }
        return networkFetch;
      })
    );
  }
});
```

### djust-Specific Benefits

- Works with TurboNav — the shell cache replaces TurboNav's `<main>` swap for the first load
- djust's `{% block content %}` convention makes shell extraction straightforward
- The Rust VDOM engine can diff the shell-served content against the server response, applying only the delta

### Configuration

```python
# settings.py
DJUST_PWA = {
    'SERVICE_WORKER': {
        'APP_SHELL': {
            'ENABLED': True,
            'SHELL_URL': '/shell/',        # Endpoint serving shell-only HTML
            'CONTENT_SELECTOR': '[dj-root]',
            'EXCLUDE_URLS': ['/admin/', '/api/'],
        }
    }
}
```

---

## 2. Prefetch on Hover

### Problem

Users hover over links 200-400ms before clicking. This dead time is wasted.

### Solution

When the user hovers over an internal link, the client notifies the SW to prefetch that page. By the time they click, the response is already cached.

### How It Works

```
User hovers over <a href="/demos/pwa/">
  → Client JS sends message to SW: prefetch /demos/pwa/
  → SW fetches the page in background, caches response
  → User clicks link (200-400ms later)
  → SW intercepts navigation, serves cached response instantly
```

### Implementation Approach

```javascript
// Client-side (in djust client.js or a small addition)
document.addEventListener('pointerenter', (e) => {
  const link = e.target.closest('a[href]');
  if (!link || link.origin !== location.origin) return;

  navigator.serviceWorker?.controller?.postMessage({
    type: 'prefetch',
    url: link.href
  });
}, { capture: true, passive: true });

// Service worker
self.addEventListener('message', (event) => {
  if (event.data.type === 'prefetch') {
    const url = event.data.url;
    // Don't re-fetch if already cached recently
    caches.open('prefetch').then(cache =>
      cache.match(url).then(existing => {
        if (!existing) {
          fetch(url).then(response => {
            if (response.ok) {
              cache.put(url, response);
            }
          });
        }
      })
    );
  }
});
```

### djust-Specific Benefits

- Zero-config for internal `<a>` tags — djust already controls the client JS
- Pairs with TurboNav for instant perceived navigation
- Can be smart about LiveView pages: prefetch the initial HTML but skip WebSocket setup until actual navigation

### Configuration

```python
DJUST_PWA = {
    'SERVICE_WORKER': {
        'PREFETCH': {
            'ENABLED': True,
            'TRIGGER': 'hover',         # 'hover' | 'viewport' | 'both'
            'MAX_PREFETCHES': 5,         # Limit concurrent prefetches
            'CACHE_TTL': 30,             # Seconds before prefetched pages expire
            'EXCLUDE_PATTERNS': [r'/admin/', r'/api/'],
        }
    }
}
```

---

## 3. Smart Static Asset Caching

### Problem

djust's client JS (~5KB), CSS, and icons are small but loaded on every page. Django's `ManifestStaticFilesStorage` uses content hashes, making cache invalidation safe but still requiring a network check.

### Solution

The SW pre-caches all static assets listed in the manifest at install time and serves them cache-first. On activation, old versions are purged. This eliminates all static asset network requests after the first visit.

### How It Works

```
First visit:
  → SW installs, pre-caches djust.js, styles.css, icons
  → Assets served from network (normal)

Subsequent visits:
  → SW intercepts /static/* requests
  → Serves from cache instantly (0ms, no network)
  → On SW update: old cache purged, new assets pre-cached
```

### Implementation Approach

```javascript
const STATIC_CACHE = 'djust-static-v1';
const STATIC_ASSETS = [
  '/static/djust/js/djust.min.js',
  '/static/djust/css/djust.css',
  // Auto-populated by generate_sw command
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(cache =>
      cache.addAll(STATIC_ASSETS)
    )
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.url.includes('/static/')) {
    event.respondWith(
      caches.match(event.request).then(cached =>
        cached || fetch(event.request).then(response => {
          const clone = response.clone();
          caches.open(STATIC_CACHE).then(c => c.put(event.request, clone));
          return response;
        })
      )
    );
  }
});
```

### djust-Specific Benefits

- `manage.py generate_sw` already exists — extend it to auto-populate the static asset list from `collectstatic` output
- Content-hashed filenames from `ManifestStaticFilesStorage` make cache busting automatic
- djust's minimal JS footprint means the pre-cache is tiny (<20KB total)

---

## 4. WebSocket Reconnection Bridge

### Problem

When a djust LiveView's WebSocket drops (network blip, sleep/wake, cell tower switch), the user sees a reconnection delay. During this gap, events (clicks, form input) are lost.

### Solution

The SW acts as a message queue between the page and the WebSocket. During disconnection, events are buffered in the SW. When the WebSocket reconnects, buffered events are replayed in order.

### How It Works

```
WebSocket connected:
  Page → events → Server (normal flow)

WebSocket drops:
  Page → events → SW buffer (queued)
  SW detects disconnect, starts reconnection attempts

WebSocket reconnects:
  SW replays buffered events → Server
  Server processes events, sends VDOM patches
  Page updates normally
```

### Implementation Approach

```javascript
// Service worker maintains an event buffer
let eventBuffer = [];
let wsConnected = true;

self.addEventListener('message', (event) => {
  if (event.data.type === 'ws-status') {
    wsConnected = event.data.connected;
    if (wsConnected && eventBuffer.length > 0) {
      // Replay buffered events
      event.source.postMessage({
        type: 'replay-events',
        events: eventBuffer
      });
      eventBuffer = [];
    }
  }

  if (event.data.type === 'djust-event' && !wsConnected) {
    eventBuffer.push(event.data.payload);
  }
});
```

### djust-Specific Benefits

- djust's event system is already serializable (JSON messages over WebSocket) — trivial to buffer
- The VDOM engine handles out-of-order patches gracefully (version numbering)
- Combines with optimistic UI from OfflineMixin for seamless UX during reconnection
- The existing `djust.offline` client module already tracks connection state

### Risks and Mitigations

- **Stale events**: A form submission buffered for 30s might conflict with server state. Mitigation: attach timestamps to buffered events; server rejects events older than a threshold.
- **Buffer overflow**: Cap the buffer at N events (e.g., 100) and drop oldest if exceeded.
- **Idempotency**: Event handlers should be idempotent where possible; the replay mechanism should deduplicate by event ID.

---

## 5. VDOM Patch Caching

### Problem

When navigating back to a previously visited LiveView page, djust must re-mount, re-render, and send the full initial HTML over WebSocket. The Rust engine then diffs from scratch.

### Solution

Cache the last VDOM state (the rendered HTML) for each LiveView page in the SW. On back-navigation, serve the cached VDOM state immediately while the WebSocket re-mounts in the background.

### How It Works

```
First visit to /demos/counter/:
  → Server renders HTML, sends over WebSocket
  → Client renders page
  → SW caches the current DOM snapshot for /demos/counter/

User navigates to /demos/pwa/, then clicks Back:
  → SW intercepts navigation to /demos/counter/
  → Serves cached DOM snapshot instantly
  → WebSocket mounts in background
  → Server sends current state; client diffs against cached state
  → Only the delta is applied (e.g., counter value changed)
```

### Implementation Approach

```javascript
// Client-side: snapshot DOM state before navigation
window.addEventListener('beforeunload', () => {
  const root = document.querySelector('[dj-root]');
  if (root) {
    navigator.serviceWorker?.controller?.postMessage({
      type: 'cache-vdom',
      url: location.href,
      html: root.innerHTML,
      timestamp: Date.now()
    });
  }
});
```

### djust-Specific Benefits

- djust's VDOM diffing engine can efficiently diff the cached state against the fresh server state — only applying minimal patches
- `[dj-root]` provides a clean boundary for what to cache
- Works naturally with the `djust_id` keying system for stable diffs

---

## 6. LiveView State Snapshots (Back Navigation)

### Problem

Pressing the browser back button in a LiveView app often shows stale or empty content because the LiveView was unmounted when the user navigated away.

### Solution

Before the LiveView unmounts, serialize its Python state (the same data stored in the state backend) and cache it in the SW. On back-navigation, restore the state snapshot to the new LiveView instance, achieving instant back-button navigation with full state restoration.

### How It Works

```
User on /demos/counter/ (count=5, items=[...]):
  → User navigates away → LiveView unmounts
  → Client sends state snapshot to SW: {url, state_json, timestamp}

User presses Back:
  → SW sends cached state to new LiveView mount
  → Server initializes LiveView with cached state instead of defaults
  → User sees count=5, items=[...] immediately
```

### Implementation Approach

```python
# Server-side: accept state restoration in mount
class LiveView:
    def mount(self, request, **kwargs):
        restored_state = kwargs.get('_restored_state')
        if restored_state:
            self._restore_from_snapshot(restored_state)
        else:
            # Normal initialization
            self.count = 0
```

```javascript
// Client-side: send snapshot on unmount
window.addEventListener('pagehide', () => {
  const stateJson = window.__djust_last_state__;
  if (stateJson) {
    navigator.serviceWorker?.controller?.postMessage({
      type: 'snapshot-state',
      url: location.href,
      state: stateJson
    });
  }
});
```

### djust-Specific Benefits

- djust already serializes state for the state backend (memory/Redis) — the snapshot format is identical
- The JIT serializer handles complex types (querysets, model instances, datetimes)
- State snapshots are small (typically <10KB) — safe to cache dozens

### Security Considerations

- State snapshots may contain sensitive data. The SW cache should be encrypted or cleared on logout.
- Snapshots should have a short TTL (e.g., 5 minutes) to avoid serving very stale state.
- Server-side validation must still run on restored state.

---

## 7. Request Batching

### Problem

A page with multiple djust components may trigger several parallel HTTP requests on load (API calls, data fetching). Each request has individual overhead (TLS handshake, headers).

### Solution

The SW intercepts parallel requests to the same origin within a short window (e.g., 50ms) and batches them into a single HTTP request to a batch endpoint. The server processes all sub-requests and returns a single response containing all results.

### How It Works

```
Page loads 3 components, each fetching data:
  Component A → GET /api/users/
  Component B → GET /api/items/
  Component C → GET /api/stats/

Without batching: 3 HTTP requests, 3 round-trips

With SW batching:
  SW buffers all 3 requests for 50ms
  SW sends POST /api/batch/ with [{url, method, body}, ...]
  Server returns [{status, headers, body}, ...]
  SW resolves each original request with its batched response
```

### Implementation Approach

```javascript
let batchQueue = [];
let batchTimer = null;

function batchFetch(request) {
  return new Promise((resolve) => {
    batchQueue.push({ request, resolve });
    if (!batchTimer) {
      batchTimer = setTimeout(flushBatch, 50);
    }
  });
}

async function flushBatch() {
  const queue = batchQueue;
  batchQueue = [];
  batchTimer = null;

  const batchPayload = await Promise.all(
    queue.map(async ({ request }) => ({
      url: new URL(request.url).pathname,
      method: request.method,
      body: await request.text()
    }))
  );

  const response = await fetch('/api/batch/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(batchPayload)
  });

  const results = await response.json();
  results.forEach((result, i) => {
    queue[i].resolve(new Response(JSON.stringify(result.body), {
      status: result.status,
      headers: result.headers
    }));
  });
}
```

### djust-Specific Benefits

- Useful for pages combining multiple LiveView components that each fetch independent data
- Reduces server-side connection pool pressure
- The batch endpoint can share database connections across sub-requests

### When Not to Use

- WebSocket-based LiveViews already multiplex over a single connection — batching is only for initial HTTP loads and non-WebSocket API calls
- Not needed for pages with a single LiveView component

---

## Implementation Roadmap

### Phase 1 (v0.4.0) — Quick Wins

1. **Prefetch on Hover** — Low complexity, high impact. Add ~30 lines to client JS and ~20 lines to SW.
2. **Smart Static Asset Caching** — Extend existing `generate_sw` command to include static asset pre-caching.

### Phase 2 (v0.4.x) — Core Improvements

3. **Instant Page Shell** — Requires a shell endpoint and client-side content swap logic. Pairs with TurboNav.
4. **WebSocket Reconnection Bridge** — Needs careful testing with various disconnect scenarios.

### Phase 3 (v0.5.0) — Advanced Features

5. **VDOM Patch Caching** — Coordinate with the Rust VDOM engine for cache-aware diffing.
6. **LiveView State Snapshots** — Requires server-side state restoration API.
7. **Request Batching** — Needs a server-side batch endpoint and Django middleware.

## Related Documentation

- [PWA Guide](pwa.md) — Current PWA setup and configuration
- [Roadmap](../roadmap.md) — Strategic vision
- [Deployment Guide](DEPLOYMENT.md) — Production deployment considerations
