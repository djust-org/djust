# Why Not Alternatives? Choosing the Right Tool

**Honest guidance on when to use djust—and when not to**

---

## Philosophy

Every framework solves specific problems. This guide helps you choose the right tool for your use case by honestly comparing djust with alternatives.

### Quick Decision Matrix

| If you need... | Choose | Not djust because... |
|----------------|--------|----------------------|
| Simple interactivity | HTMX | djust is overkill for basic CRUD |
| Offline-first app | React/Vue | djust requires server connection |
| Extreme scale (millions) | Phoenix LiveView | Elixir/BEAM more battle-tested |
| Quick prototype | HTMX or Alpine.js | Faster learning curve |
| Django real-time app | **djust** | This is exactly what we built for |

---

## Table of Contents

1. [Why Not HTMX?](#why-not-htmx)
2. [Why Not Alpine.js?](#why-not-alpinejs)
3. [Why Not django-unicorn?](#why-not-django-unicorn)
4. [Why Not Tetra?](#why-not-tetra)
5. [Why Not Phoenix LiveView?](#why-not-phoenix-liveview)
6. [Why Not React/Vue?](#why-not-reactvue)
7. [Why Not Hotwire/Turbo?](#why-not-hotwireturbo)
8. [When to Choose djust](#when-to-choose-djust)

---

## Why Not HTMX?

### What HTMX Does Well

✅ **Simplicity**: HTML attributes only, 15-minute learning curve
✅ **Lightweight**: ~15KB, no build step
✅ **Backend-agnostic**: Works with any server
✅ **Progressive enhancement**: Graceful fallback

### What HTMX Doesn't Do

❌ **No VDOM**: Full HTML replacement, larger payloads
❌ **No component system**: Manual coordination
❌ **No state management**: Entirely stateless
❌ **No real-time updates**: HTTP polling only

### Choose HTMX Over djust If...

1. **Simple CRUD operations** (todo lists, basic forms)
2. **Progressive enhancement** is priority #1
3. **Backend-agnostic** solution needed (not tied to Django)
4. **Team has zero Python experience** (pure HTML)
5. **Performance is "good enough"** (<1000 users)

### Example Use Case for HTMX

**Infinite Scroll Blog:**

```html
<!-- Simple, effective, no framework needed -->
<div hx-get="/posts?page=2"
     hx-trigger="revealed"
     hx-swap="afterend">
</div>
```

**Why not djust:** HTMX is simpler here. djust's VDOM and actor system are overkill.

---

### Choose djust Over HTMX If...

1. **Complex interactive UIs** (dashboards, real-time data)
2. **Component reusability** matters
3. **State management** needed across views
4. **Performance critical** (>1000 concurrent users)
5. **Real-time updates** required (WebSocket)

### Example Use Case for djust

**Real-Time Dashboard:**

```python
class DashboardView(LiveView):
    def mount(self, request):
        self.metrics = self.fetch_metrics()

    def on_filter(self, timerange: str = "", **kwargs):
        self.metrics = self.fetch_metrics(timerange)
        # Automatic VDOM diff, minimal update
```

**Why djust wins:** HTMX would send entire dashboard HTML on every filter change. djust sends minimal VDOM patches (10-50 bytes vs 10-50KB).

---

## Why Not Alpine.js?

### What Alpine.js Does Well

✅ **Declarative**: Clean, readable syntax (x-data, x-show)
✅ **Lightweight**: ~15KB
✅ **No build step**: Drop-in script tag
✅ **Client-side reactivity**: Fast, no server needed

### What Alpine.js Doesn't Do

❌ **Client-side only**: No server-side logic
❌ **Manual state sync**: Must write fetch() calls
❌ **No VDOM**: Direct DOM manipulation
❌ **No backend integration**: DIY everything

### Choose Alpine.js Over djust If...

1. **Client-side only** interactions (accordions, modals, dropdowns)
2. **No server logic** needed
3. **Replacing jQuery** in existing app
4. **Static site** with sprinkles of interactivity

### Example Use Case for Alpine.js

**Client-Only Dropdown:**

```html
<div x-data="{ open: false }">
    <button @click="open = !open">Toggle</button>
    <div x-show="open">Content</div>
</div>
```

**Why not djust:** This is purely client-side, no server needed. djust would require WebSocket round-trip.

---

### Choose djust Over Alpine.js If...

1. **Server-side logic** is primary
2. **Database queries** on interactions
3. **Real-time updates** across users
4. **State persistence** required
5. **Complex business logic** involved

### Example Use Case for djust

**Live Search with DB:**

```python
class SearchView(LiveView):
    def on_search(self, query: str = "", **kwargs):
        # Server-side filtering with Django ORM
        self.results = Product.objects.filter(
            name__icontains=query,
            in_stock=True
        ).order_by('-popularity')[:10]
```

**Why djust wins:** Alpine.js would require writing custom fetch() calls, handling errors, managing state. djust does it automatically.

---

### Complementary Use

**You can use both together!**

```html
<!-- Alpine for client-only -->
<div x-data="{ tab: 'info' }">
    <button @click="tab = 'info'">Info</button>
    <button @click="tab = 'reviews'">Reviews</button>

    <div x-show="tab === 'info'">...</div>
    <div x-show="tab === 'reviews'">...</div>
</div>

<!-- djust for server interactions -->
<button @click="save_to_database">Save</button>
```

---

## Why Not django-unicorn?

### What django-unicorn Does Well

✅ **Mature**: 3+ years, 2.3k GitHub stars
✅ **HTML-first syntax**: Familiar to designers
✅ **Active community**: Good docs, examples
✅ **Battle-tested**: Production usage

### What django-unicorn Doesn't Do (as well)

❌ **Performance**: 5-10ms VDOM (vs <0.1ms djust)
❌ **Scalability**: GIL bottlenecks under load
❌ **Component actors**: View-level updates only
❌ **Actor system**: No zero-GIL concurrency

### Choose django-unicorn Over djust If...

1. **Mature ecosystem** is priority
2. **HTML-first approach** preferred
3. **Community size** matters
4. **Performance is "good enough"** (<1000 users)
5. **Risk-averse** (proven in production)

### Performance Comparison

| Metric | django-unicorn | djust |
|--------|----------------|-------|
| VDOM Diffing | 5-10ms | <0.1ms |
| Concurrent Users | ~2,000/sec | ~50,000/sec |
| Learning Curve | 1 hour | 30 minutes |

---

### Choose djust Over django-unicorn If...

1. **Performance critical** (>1000 concurrent users)
2. **Component-level updates** needed
3. **Actor-based architecture** appeals to you
4. **Want fastest possible** VDOM
5. **Building next-gen apps** (vs maintaining existing)

### Migration Path

**From django-unicorn to djust:**

Before (unicorn):
```html
<input unicorn:model.debounce-500="search_query" />
<div unicorn:loading>Searching...</div>
```

After (djust):
```python
class SearchView(LiveView):
    @debounce(0.5)  # Coming soon
    def on_search(self, query: str = "", **kwargs):
        self.results = search(query)
```

**Effort:** 1-2 days for typical app

---

## Why Not Tetra?

### What Tetra Does Well

✅ **Modern architecture**: Server + client hybrid
✅ **Python decorators**: `@public.debounce(200)`
✅ **Alpine.js integration**: Full ecosystem
✅ **Async-first**: Built on async/await

### What Tetra Doesn't Do (as well)

❌ **Requires Alpine.js**: 44KB bundle dependency
❌ **Client-side VDOM**: Alpine morphing (5-10ms)
❌ **No actor system**: Traditional request/response
❌ **Smaller community**: Newer, less battle-tested

### Choose Tetra Over djust If...

1. **Already using Alpine.js** (sunk cost)
2. **Alpine ecosystem** is valuable
3. **Hybrid approach** appeals (server + client)
4. **Want Alpine's reactivity** with Python backend

---

### Choose djust Over Tetra If...

1. **Want smallest bundle** (5KB vs 44KB)
2. **Performance critical** (Rust VDOM vs Alpine morph)
3. **Actor-based architecture** needed
4. **Don't want JS framework dependency**
5. **Pure server-side** approach preferred

### Bundle Size Comparison

```
Tetra (with Alpine): ████████████████████████████ 44KB
djust:               ████ 5KB

9x smaller
```

---

## Why Not Phoenix LiveView?

### What Phoenix LiveView Does Well

✅ **Gold standard**: 5+ years battle-tested
✅ **Blazing fast**: Elixir/BEAM performance
✅ **Horizontal scaling**: Distributed Erlang
✅ **Mature ecosystem**: Comprehensive tooling
✅ **Excellent documentation**

### What Phoenix LiveView Doesn't Do

❌ **Requires Elixir**: New language for Django devs
❌ **Requires Phoenix**: New framework
❌ **No Django integration**: Can't leverage existing app
❌ **Steep learning curve**: 1+ weeks for Django devs

### Choose Phoenix LiveView Over djust If...

1. **Greenfield project** (starting from scratch)
2. **Team excited about Elixir**
3. **Need extreme scale** (millions of users)
4. **Want proven architecture** (5+ years production)
5. **No existing Django codebase**

### Performance Comparison

| Aspect | Phoenix LiveView | djust |
|--------|------------------|-------|
| VDOM Speed | ~1ms (Elixir) | <0.1ms (Rust) |
| Language | Elixir | Python |
| Ecosystem | Hex/Phoenix | PyPI/Django |
| Learning Curve | 1+ week | 30 minutes |

---

### Choose djust Over Phoenix LiveView If...

1. **Existing Django codebase** (can't rewrite)
2. **Python expertise** (team knows Python)
3. **Want similar architecture** in Python ecosystem
4. **Leverage Django packages** (extensive ecosystem)
5. **Smaller scale** (<100k users)

### Philosophical Difference

**Phoenix LiveView:**
- "Start from scratch with best-in-class architecture"
- Elixir/BEAM advantages (process model, fault tolerance)
- Excellent for greenfield projects

**djust:**
- "Bring Phoenix architecture to existing Django apps"
- Python/Django advantages (ecosystem, familiarity)
- Excellent for Django shops wanting LiveView

---

## Why Not React/Vue?

### What React/Vue Do Well

✅ **Huge ecosystems**: Thousands of libraries
✅ **Offline-first**: Service workers, local state
✅ **Complex client logic**: State machines, routing
✅ **Mobile apps**: React Native, Ionic
✅ **Mature tooling**: DevTools, testing, debugging

### What React/Vue Don't Do (as well)

❌ **Build complexity**: webpack, vite, babel, etc.
❌ **Context switching**: Python ↔ JavaScript
❌ **Separate deployment**: Frontend + backend
❌ **Large bundles**: 42KB+ (just runtime)
❌ **Manual state sync**: fetch(), axios, etc.

### Choose React/Vue Over djust If...

1. **Offline-first** functionality required
2. **Complex client-side** state machines
3. **Mobile app** needed (React Native)
4. **Dedicated frontend team** (specialists)
5. **Huge ecosystem** is priority

### Example Use Case for React

**Offline-First PWA:**

```javascript
// Service worker, IndexedDB, complex client state
// djust requires server connection
```

---

### Choose djust Over React/Vue If...

1. **Server-side logic** is primary
2. **Python-only team** (no frontend specialists)
3. **Want simplicity** (no build step)
4. **Real-time updates** (WebSocket built-in)
5. **Smaller bundle** (5KB vs 42KB+)

### Complexity Comparison

**React Setup:**
```bash
# Install
npm install react react-dom
npm install -D webpack babel typescript

# Configure webpack, babel, TypeScript
# Write build scripts
# Setup dev server
# Configure linting, formatting
# Setup testing (Jest, React Testing Library)

# Deploy
npm run build  # Generate bundle
# Deploy static files to CDN
# Deploy API separately
```

**djust Setup:**
```bash
pip install djust
python manage.py runserver
# That's it
```

---

### Migration Effort

**From React to djust:**

Effort: Medium-High (1-2 weeks per major feature)

**Mindset shift required:**

| React | djust |
|-------|-------|
| Client state (useState) | Server state (self.attribute) |
| useEffect | LiveView lifecycle (mount, update) |
| fetch() | Automatic sync |
| Redux/Context | Actor state |
| Client routing | Django routing |

**Consider djust if:**
- 80%+ of your logic is server-side anyway
- Complex build pipeline is causing pain
- Team prefers Python over JavaScript

---

## Why Not Hotwire/Turbo?

### What Hotwire/Turbo Do Well

✅ **Mature**: Rails ecosystem, years of production
✅ **Turbo Frames**: Partial page updates
✅ **Turbo Streams**: Server-sent updates
✅ **Backend-agnostic**: Works with any framework
✅ **Progressive enhancement**

### What Hotwire/Turbo Don't Do (as well)

❌ **Client-side diffing**: morphdom (5-10ms)
❌ **No server VDOM**: Sends full HTML fragments
❌ **Rails-centric**: Best docs for Rails
❌ **Complexity**: Frames, streams, drive concepts

### Choose Hotwire Over djust If...

1. **Coming from Rails** (familiar patterns)
2. **Backend-agnostic** needed (not Django-specific)
3. **Progressive enhancement** is priority
4. **Turbo Frame** model appeals to you

---

### Choose djust Over Hotwire If...

1. **Want server-side VDOM** (minimal patches)
2. **Performance critical** (Rust VDOM vs morphdom)
3. **Django-optimized** solution
4. **Actor-based architecture** appeals
5. **Simpler mental model** (LiveView vs Frames+Streams+Drive)

---

## When to Choose djust

### ✅ Choose djust If You Have...

**1. Existing Django Codebase**
- Can't rewrite from scratch
- Want to add real-time features
- Leverage existing Django/Python expertise

**2. Performance Requirements**
- >1000 concurrent users
- Real-time dashboards
- Sub-10ms update latency needed

**3. Team Composition**
- Python-first team
- No dedicated frontend specialists
- Value simplicity over ecosystem size

**4. Use Cases**
- Real-time dashboards
- Interactive admin interfaces
- Chat/messaging applications
- Live data tables/grids
- Multi-user collaborative tools

---

### ❌ Don't Choose djust If You Need...

**1. Offline-First Functionality**
- Progressive Web App with offline support
- Service workers, IndexedDB
- Client-side state when disconnected

→ **Use React/Vue** instead

**2. Simple Interactivity**
- Basic CRUD operations
- Infinite scroll
- Modal dialogs only

→ **Use HTMX** instead (simpler)

**3. Greenfield + Extreme Scale**
- Starting from scratch
- Millions of concurrent users
- Team excited about learning Elixir

→ **Use Phoenix LiveView** instead

**4. Pure Client-Side**
- No server logic needed
- Static site with client interactions
- Dropdown menus, accordions only

→ **Use Alpine.js** instead

---

## Decision Flowchart

```
Do you have an existing Django app?
├─ No → Are you willing to learn Elixir?
│   ├─ Yes → Phoenix LiveView
│   └─ No → Do you need offline support?
│       ├─ Yes → React/Vue
│       └─ No → djust or start with HTMX
│
└─ Yes → Do you need real-time features?
    ├─ No → Stick with Django + HTMX for interactivity
    └─ Yes → Is performance critical?
        ├─ No (<1000 users) → django-unicorn (mature) or djust
        └─ Yes (>1000 users) → djust
```

---

## Mixed Approach: Use Multiple Tools

**Don't be dogmatic!** Mix and match:

```html
<!-- Alpine.js for client-only interactions -->
<div x-data="{ tab: 'info' }">
    <button @click="tab = 'info'">Info</button>
    <div x-show="tab === 'info'">...</div>
</div>

<!-- djust for server-side interactions -->
<div>
    <input @input="on_search" />
    <div>{{ results }}</div>
</div>

<!-- HTMX for simple CRUD -->
<button hx-delete="/api/items/1">Delete</button>
```

**Use the right tool for each job.**

---

## Honest Assessment

### djust's Strengths

1. **Performance**: Genuinely 10-100x faster (Rust VDOM)
2. **Architecture**: Modern, scalable (actor-based)
3. **Simplicity**: Python-only, no build step
4. **Django integration**: Seamless, familiar

### djust's Weaknesses

1. **Young**: v0.1.0, less battle-tested than alternatives
2. **Smaller community**: Fewer examples, less StackOverflow
3. **No offline support**: Requires server connection
4. **Learning curve**: New patterns vs traditional Django

### Be Honest With Yourself

**Choose djust if you're:**
- Building real-time Django applications
- Willing to use early-stage technology
- Value performance and modern architecture
- Excited about actor model and Rust integration

**Don't choose djust if you're:**
- Risk-averse (wait for v1.0)
- Need massive ecosystem (use React)
- Building simple CRUD (use HTMX)
- Can't afford learning curve right now

---

## Conclusion

### No Silver Bullet

Every framework has trade-offs:

- **HTMX**: Simplest, but limited for complex UIs
- **Alpine.js**: Great for client-only, but no server integration
- **django-unicorn**: Mature, but slower (GIL bottlenecks)
- **Tetra**: Good hybrid, but requires Alpine.js
- **Phoenix LiveView**: Best-in-class, but requires Elixir
- **React/Vue**: Huge ecosystems, but high complexity
- **djust**: Fast + simple, but young

### The Right Question

Not "Which framework is best?" but **"Which framework is best for my use case?"**

---

### Try Before You Commit

**30-Minute Evaluation:**

```bash
# 1. Install
pip install djust

# 2. Create simple LiveView
# 3. Compare with your current approach
# 4. Measure actual performance in your use case
# 5. Evaluate team learning curve
```

**Don't trust marketing (including ours).** Test in your environment.

---

**Still unsure? Ask yourself:**

1. "Do I have an existing Django app?" → djust likely fits
2. "Do I need offline support?" → Use React/Vue
3. "Is this simple interactivity?" → Use HTMX
4. "Is performance critical?" → djust excels here
5. "Is this greenfield + I want to learn Elixir?" → Phoenix LiveView

**Make the choice that's right for your team and use case.**

[Quick Start →](../QUICKSTART.md) | [Framework Comparison →](FRAMEWORK_COMPARISON.md) | [Try Examples →](../examples/demo_project/)
