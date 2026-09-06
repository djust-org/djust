# State Management Quick Start

> **Inert.** `@client_state` is INERT — it stamps metadata nothing in the shipped client reads (#2680), so a decorated handler behaves exactly like an undecorated one. The `StateBus` it named was deleted in #2680.

**Get productive with djust state management in 5 minutes**

## ✅ Prerequisites

Already have djust installed? State management is built-in! No additional setup needed.

If not, install djust first:
```bash
pip install djust  # Or follow installation guide
```

## 🚀 Your First Decorator (2 minutes)

Let's build a debounced search in **8 lines of Python** (no JavaScript needed).

### Step 1: Create a LiveView with debounced search

```python
from djust import LiveView
from djust.decorators import debounce

class ProductSearchView(LiveView):
    template_string = """
    <div>
        <input dj-input="search" placeholder="Search products..." />
        <div>
            {% for product in results %}
                <div>{{ product.name }} - ${{ product.price }}</div>
            {% endfor %}
        </div>
    </div>
    """

    def mount(self, request):
        self.results = []

    @debounce(wait=0.5)  # Wait 500ms after typing stops
    def search(self, query: str = "", **kwargs):
        from myapp.models import Product
        self.results = Product.objects.filter(name__icontains=query)[:10]
```

### Step 2: Add URL routing

```python
# urls.py
from django.urls import path
from .views import ProductSearchView

urlpatterns = [
    path('search/', ProductSearchView.as_view()),
]
```

### Step 3: Test it!

1. Start your server: `python manage.py runserver`
2. Visit http://localhost:8000/search/
3. Type in the search box
4. Notice: Server only queries **after you stop typing**

**That's it!** No JavaScript, no setTimeout, no manual debouncing.

---

## ⚡ Add Instant Feedback (1 minute)

Make the UI update **instantly** while the server validates:

```python
from djust.decorators import debounce, optimistic

class ProductSearchView(LiveView):
    # ... same template ...

    @debounce(wait=0.5)
    @optimistic  # ← Add this line
    def search(self, query: str = "", **kwargs):
        from myapp.models import Product
        self.results = Product.objects.filter(name__icontains=query)[:10]
```

**What changed?**
- User types "laptop"
- UI updates **instantly** (optimistic)
- After 500ms, server validates
- Server sends corrections (if any)

**Result**: Feels native, like a desktop app!

---

## 💾 Add Response Caching (1 minute)

Avoid redundant server queries by caching responses:

```python
from djust.decorators import debounce, optimistic, cache

class ProductSearchView(LiveView):
    # ... same template ...

    @debounce(wait=0.5)
    @optimistic
    @cache(ttl=300, key_params=["query"])  # ← Add this line (5 min cache)
    def search(self, query: str = "", **kwargs):
        from myapp.models import Product
        self.results = Product.objects.filter(name__icontains=query)[:10]
```

**What changed?**
- User types "laptop" → server query
- User types "phone" → server query
- User types "laptop" **again** → **instant** (cached, no server query)

**Performance impact**:
- First search: ~200ms (server query)
- Cached search: **<1ms** (zero server load)

---

## 🎉 What You Just Built

In **3 minutes** and **12 lines of Python**, you built a production-ready search with:

✅ **Debouncing** - Only queries after user stops typing
✅ **Optimistic updates** - Instant UI feedback
✅ **Client-side caching** - Zero redundant queries
✅ **Automatic DOM diffing** - Minimal network payload
✅ **Zero JavaScript** - No manual event handling

**Traditional approach**: ~85 lines of JavaScript + Python
**djust approach**: 12 lines of Python
**Code reduction**: **87%**

---

## 📊 Quick Decorator Reference

| Decorator | Use When | Example |
|-----------|----------|---------|
| `@debounce(wait)` | User is typing | Search, autosave |
| `@throttle(interval)` | Rapid events | Scroll, resize |
| `@optimistic` | Instant feedback needed | Counter, toggle |
| `@cache(ttl, key_params)` | Same query repeated | Autocomplete, search |
| `@client_state(keys)` | Multi-component coordination | Dashboard filters |
| `DraftModeMixin` | Auto-save forms | Contact form, email |

**Quick Decision Matrix**:
```
Typing in input? → @debounce(0.5)
Scrolling/resizing? → @throttle(0.1)
Need instant UI update? → @optimistic
Same query multiple times? → @cache(ttl)
Multiple components? → @client_state([keys])
Auto-save forms? → DraftModeMixin
```

---

## 🎯 Next Steps

### 📚 Learn More

**Comprehensive Guides**:
- **[State Management Tutorial](STATE_MANAGEMENT_TUTORIAL.md)** - Step-by-step Product Search (20 min)
- **[API Reference](STATE_MANAGEMENT_API.md)** - Complete decorator documentation
- **[Examples](STATE_MANAGEMENT_EXAMPLES.md)** - Copy-paste ready code (e-commerce, chat, dashboards)
- **[Patterns](STATE_MANAGEMENT_PATTERNS.md)** - Best practices and anti-patterns

**Migration & Comparison**:
- **[Migration Guide](STATE_MANAGEMENT_MIGRATION.md)** - Convert JavaScript to Python decorators
- **[Framework Comparison](STATE_MANAGEMENT_COMPARISON.md)** - vs Phoenix LiveView & Laravel Livewire

### 🔨 Try More Decorators

**Throttling (limit event frequency)**:
```python
@throttle(interval=0.1)  # Max 10 calls/second
def on_scroll(self, scroll_y: int = 0, **kwargs):
    self.scroll_position = scroll_y
```

**Client State (coordinate components)**:
```python
@client_state(keys=["filter"])
def update_filter(self, filter: str = "", **kwargs):
    self.filter = filter  # Automatically notifies other components
```

**Draft Mode (auto-save forms)**:
```python
from djust.mixins import DraftModeMixin

class ContactFormView(DraftModeMixin, LiveView):
    draft_fields = ["name", "email", "message"]  # Auto-saved to localStorage
    draft_ttl = 3600  # 1 hour
```

### 🎓 Real-World Examples

Explore complete working examples in the repository:
- **[Product Search](../examples/demo_project/demo_app/views/)** - Debouncing + caching
- **[Dashboard](../examples/demo_project/demo_app/views/)** - Multi-component coordination
- **[Forms](../examples/demo_project/demo_app/views/)** - Draft mode + optimistic updates

### 💬 Get Help

- 📖 [Full Documentation](https://djust.org/docs)
- 💬 [Discord Community](https://discord.gg/djust)
- 🐛 [Issue Tracker](https://github.com/johnrtipton/djust/issues)

---

## 🤔 Common Questions

### "Do I need to write any JavaScript?"

**No!** All state management patterns work with zero JavaScript:
- Debouncing
- Throttling
- Optimistic updates
- Response caching
- Client-side state coordination
- Form draft saving

JavaScript is only needed for:
- Complex animations (D3.js, GSAP)
- Third-party integrations (Stripe.js, Google Maps)
- Custom canvas rendering

### "What's the performance impact?"

**Bundle size**: 7.1 KB gzipped (still 4-7x smaller than competitors)
- Phoenix LiveView: ~30 KB
- Laravel Livewire: ~50 KB

**Runtime overhead**: Negligible (~1-2ms per event)

### "Can I mix decorators?"

**Yes!** Decorators compose cleanly:

```python
@debounce(wait=0.5)      # Wait for typing to stop
@optimistic               # Update UI instantly
@cache(ttl=60)           # Cache for 1 minute
def search(self, query: str = "", **kwargs):
    self.results = expensive_query(query)
```

**Execution order**: Top to bottom (debounce → optimistic → cache → handler)

### "How do I debug decorator behavior?"

Enable debug mode to see decorator execution:

```python
# settings.py
LIVEVIEW_CONFIG = {
    'debug_vdom': True,
}
```

Or in browser console:
```javascript
window.djustDebug = true;  // Enable client-side logging
```

You'll see logs like:
- `[djust:cache] Cache hit for search(query=laptop) - age: 45s / TTL: 300s`
- `[djust:debounce] Waiting 500ms for search(query=laptop)`
- `[djust:state] Published to bus: filter=electronics`

---

## 🎊 Congratulations!

You've mastered the basics of djust state management. You now know how to:

✅ Debounce user input
✅ Add optimistic UI updates
✅ Cache responses client-side
✅ Compose multiple decorators
✅ Build production-ready reactive UIs with zero JavaScript

**Ready for more?** Check out the [full tutorial](STATE_MANAGEMENT_TUTORIAL.md) for a comprehensive guide!

---

**Questions or feedback?** Open an issue or join our [Discord](https://discord.gg/djust)!
