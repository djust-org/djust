# State Management Examples

> **Inert.** `@optimistic` (#2699) and `@client_state` (#2680) are both INERT: they record metadata that no shipped client code reads, so a decorated handler behaves exactly like an undecorated one. The `StateBus` that `@client_state` named was deleted in #2680. Neither decorator is used in the examples below.

**Status**: Illustrative. Adapt the models, forms and helpers to your project.

This document provides complete examples of the state management tools (`@debounce`, `@throttle`, `@cache`, `DraftModeMixin` and the `dj-loading.*` attributes) in real-world scenarios.

Three rules apply to every example on this page:

- **Every handler needs `@event_handler`.** The default `event_security` policy is `"strict"`, which rejects any event whose method is not decorated with `@event_handler`. `@debounce`, `@throttle` and `@cache` only configure the client; they do not register the method as an event.
- **`dj-input` and `dj-change` send `value` and `field`,** not a keyword named after the input. The typed value arrives as `value`, and the input's `name` arrives as `field`. Static context goes in `dj-value-*` attributes (for example `dj-value-item-id:int="{{ item.id }}"`).
- **The live region is a literal `<div dj-root>`.** The server stamps `dj-view` onto that exact string. Put classes and other attributes on an inner element, or write `dj-view="app.views.MyView"` yourself.

## Table of Contents

- [Quick Reference Card](#quick-reference-card)
- [E-Commerce Examples](#e-commerce-examples)
  - [Product Search](#product-search)
  - [Shopping Cart](#shopping-cart)
  - [Product Filters](#product-filters)
- [Chat & Messaging](#chat--messaging)
  - [Live Chat](#live-chat)
  - [Message Composer](#message-composer)
- [Dashboard & Analytics](#dashboard--analytics)
  - [Real-Time Dashboard](#real-time-dashboard)
  - [Chart Filters](#chart-filters)
- [Forms & Validation](#forms--validation)
  - [Contact Form with Drafts](#contact-form-with-drafts)
  - [Multi-Step Form](#multi-step-form)
- [Admin Panel](#admin-panel)
  - [Bulk Actions](#bulk-actions)
  - [Inline Editing](#inline-editing)
- [Real-Time Collaboration](#real-time-collaboration)
  - [Document Editor](#document-editor)
  - [Shared Whiteboard](#shared-whiteboard)

---

## Quick Reference Card

**One-Page Cheat Sheet for State Management Decorators**

### When to Use Each Decorator

| Need | Decorator | Example Use Case | Code |
|------|-----------|------------------|------|
| **Delay until user stops typing/interacting** | `@debounce(wait)` | Search input, text area, slider | `@debounce(wait=0.5)` |
| **Limit event frequency** | `@throttle(interval)` | Load-more button, refresh button | `@throttle(interval=1.0)` |
| **Cache server responses in the browser** | `@cache(ttl, key_params)` | Autocomplete, read-only lookups | `@cache(ttl=60, key_params=["value"])` |
| **Auto-save form drafts** | `DraftModeMixin` | Long forms, email composer, comments | `class MyView(DraftModeMixin, LiveView)` |
| **Show/hide loading indicators** | `dj-loading.*` (HTML) | Button states, spinner visibility | `<button dj-loading.disable>` |

### Common Decorator Combinations

| Pattern | Decorators | Use Case |
|---------|------------|----------|
| **Debounced Search** | `@debounce(0.5)` | Basic search (100 keystrokes → 1 request) |
| **Cached Lookup** | `@debounce(0.5)`<br>`@cache(ttl=60)` | Read-only lookup with browser caching |
| **Filtered Dashboard** | `@debounce(0.3)` | Dashboard with several filter inputs |
| **Form with Drafts** | `DraftModeMixin`<br>`@debounce(1.0)` | Auto-save form with server sync |
| **Load More** | `@throttle(interval=1.0)` | Load more with rate limiting |

Each combination also needs `@event_handler` on top.

### Quick Copy-Paste Templates

**1. Debounced Search:**
```python
from djust.decorators import debounce, event_handler

@event_handler
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    self.query = value
    self.results = Model.objects.filter(name__icontains=value)
```

**2. Counter:**
```python
from djust.decorators import event_handler

@event_handler
def increment(self, **kwargs):
    self.count += 1
```

**3. Cached API Call:**
```python
from djust.decorators import cache, event_handler

# A cache hit replays the stored patches without running this method,
# so keep cached handlers to read-only lookups.
@event_handler
@cache(ttl=300, key_params=["value"])
def get_weather(self, value: str = "", **kwargs):
    self.weather = fetch_weather_api(value)
```

**4. Several Filters, One Handler:**
```python
from djust.decorators import debounce, event_handler

FILTER_FIELDS = {"category", "min_price"}

@event_handler
@debounce(wait=0.3)
def apply_filters(self, value: str = "", field: str = "", **kwargs):
    # dj-change sends the input's name as `field` and its value as `value`
    if field not in FILTER_FIELDS:
        return
    setattr(self, field, int(value or 0) if field == "min_price" else value)
    self.results = Product.objects.filter(
        category=self.category,
        price__gte=self.min_price,
    )
```

**5. Form with Auto-Save:**
```python
from djust.drafts import DraftModeMixin
from djust.forms import FormMixin

class ContactView(DraftModeMixin, FormMixin, LiveView):
    form_class = ContactForm
    draft_key = "contact_form"
```

```html
<!-- Draft attributes go on an element inside the literal <div dj-root> -->
<div dj-root>
    <div {% if draft_enabled %}data-draft-enabled data-draft-key="{{ draft_key }}"{% endif %}>
        <input name="name" data-draft="true" />
        <input name="email" data-draft="true" />
        <textarea name="message" data-draft="true"></textarea>
    </div>
</div>
```

The client checks only whether `data-draft-enabled` is present, so render it conditionally rather than as `data-draft-enabled="{{ draft_enabled }}"`.

**6. Throttled Load More:**
```python
from djust.decorators import event_handler, throttle

@event_handler
@throttle(interval=1.0, leading=True, trailing=False)
def load_more(self, **kwargs):
    self.page += 1
    self.items = Item.objects.all()[: self.page * 20]
```

### Performance Guidelines

| Decorator | Recommended Values | Impact |
|-----------|-------------------|--------|
| `@debounce` | `wait=0.3-0.5` (search)<br>`wait=1.0-2.0` (auto-save) | Reduces requests by 80-95% |
| `@throttle` | `interval=0.1-0.2` (rapid input)<br>`interval=1.0-5.0` (buttons, polling) | Limits to 5-10 events/sec or 0.2-1 events/sec |
| `@cache` | `ttl=60` (autocomplete)<br>`ttl=300` (search)<br>`ttl=3600` (static data) | Reduces server load by 40-80% |
| `DraftModeMixin` | Auto-saves after 500ms debounce | No server requests (localStorage only) |

### Decorator Order

The order of `@debounce`, `@throttle` and `@cache` does not matter. Each one only adds client-side configuration to the handler's metadata. Put `@event_handler` on every handler (on top, by convention). If both `@debounce` and `@throttle` are present, `@debounce` wins.

```python
@event_handler
@debounce(wait=0.5)
@cache(ttl=60, key_params=["value"])
def my_handler(self, value: str = "", **kwargs):
    pass
```

### Troubleshooting Quick Fixes

| Problem | Solution |
|---------|----------|
| Event rejected: "not decorated with @event_handler" | Add `@event_handler` to the handler |
| Handler receives default arguments | `dj-input`/`dj-change` send `value` and `field`; pass static context with `dj-value-*` |
| Debounce not working | Check `wait` is in seconds (not milliseconds): `wait=0.5` not `wait=500` |
| Cache always misses | Ensure `key_params` matches the event's parameter names (`value`, `field`, `dj-value-*` keys) |
| Form drafts not restoring | Check the template has `data-draft-enabled` and `data-draft-key` on the draft root, and fields have `data-draft="true"` |
| Loading indicator stuck | Verify handler doesn't throw exception (causes loading state to persist) |

---

## E-Commerce Examples

### Product Search

**Use Case**: Real-time product search with debouncing and filters.

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, event_handler
from shop.models import Product

class ProductSearchView(LiveView):
    template_name = 'shop/product_search.html'

    CATEGORIES = [
        ('', 'All Categories'),
        ('electronics', 'Electronics'),
        ('clothing', 'Clothing'),
        ('books', 'Books'),
    ]
    SEARCH_FIELDS = {"query", "category", "min_price"}

    def mount(self, request, **kwargs):
        self.query = ""
        self.category = ""
        self.min_price = 0
        self.max_price = 10000
        self.results = Product.objects.filter(in_stock=True)[:20]

    @event_handler
    @debounce(wait=0.5)
    def search(self, value: str = "", field: str = "", **kwargs):
        """Update one filter (named by the input's `name`) and re-run the search."""
        if field not in self.SEARCH_FIELDS:
            return
        if field == "min_price":
            self.min_price = int(value or 0)
        else:
            setattr(self, field, value)

        # Build query
        filters = {'in_stock': True}
        if self.query:
            filters['name__icontains'] = self.query
        if self.category:
            filters['category'] = self.category
        filters['price__gte'] = self.min_price
        filters['price__lte'] = self.max_price

        self.results = Product.objects.filter(**filters)[:20]

    def get_context_data(self, **kwargs):
        return {
            'query': self.query,
            'category': self.category,
            'min_price': self.min_price,
            'max_price': self.max_price,
            'categories': self.CATEGORIES,
            'results': self.results,
            'count': self.results.count()
        }
```

```html
<!-- templates/shop/product_search.html -->
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    <title>Product Search</title>
    {% djust_client_config %}
</head>
<body>
    <div dj-root>
    <div class="container">
        <input
            type="text"
            name="query"
            dj-input="search"
            value="{{ query }}"
            placeholder="Search products..."
        />

        <select dj-change="search" name="category">
            {% for value, label in categories %}
            <option value="{{ value }}" {% if value == category %}selected{% endif %}>
                {{ label }}
            </option>
            {% endfor %}
        </select>

        <input
            type="range"
            dj-change="search"
            name="min_price"
            value="{{ min_price }}"
            min="0"
            max="10000"
        />

        <span dj-loading.show dj-loading.for="search">Searching…</span>

        <div class="results">
            {% for product in results %}
            <div class="product">
                <h3>{{ product.name }}</h3>
                <p>${{ product.price }}</p>
            </div>
            {% endfor %}
        </div>

        <p>Found {{ count }} products</p>
    </div>
    </div>
</body>
</html>
```

The client script is injected automatically; `{% djust_client_config %}` is optional.

---

### Shopping Cart

**Use Case**: Add/remove items with server validation.

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler
from shop.models import Product, CartItem
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator

@method_decorator(login_required, name='dispatch')
class ShoppingCartView(LiveView):
    template_name = 'shop/cart.html'

    def mount(self, request, **kwargs):
        self.cart_items = CartItem.objects.filter(user=request.user)
        self.total = sum(item.subtotal for item in self.cart_items)

    @event_handler
    def update_quantity(self, item_id: int = 0, value: int = 1, **kwargs):
        """
        Update cart item quantity.

        `item_id` comes from dj-value-item-id; `value` is the input's value.
        The server validates and corrects if needed.
        """
        quantity = value
        try:
            item = CartItem.objects.get(id=item_id, user=self.request.user)

            if quantity <= 0:
                item.delete()
            elif quantity > item.product.stock:
                # Server correction: limit to available stock
                item.quantity = item.product.stock
                item.save()
            else:
                item.quantity = quantity
                item.save()

            # Recalculate
            self.cart_items = CartItem.objects.filter(user=self.request.user)
            self.total = sum(item.subtotal for item in self.cart_items)

        except CartItem.DoesNotExist:
            pass

    @event_handler
    def remove_item(self, item_id: int = 0, **kwargs):
        """Remove item from cart."""
        try:
            item = CartItem.objects.get(id=item_id, user=self.request.user)
            item.delete()

            self.cart_items = CartItem.objects.filter(user=self.request.user)
            self.total = sum(item.subtotal for item in self.cart_items)
        except CartItem.DoesNotExist:
            pass

    @event_handler
    def clear_cart(self, **kwargs):
        """Clear entire cart."""
        CartItem.objects.filter(user=self.request.user).delete()
        self.cart_items = []
        self.total = 0

    def get_context_data(self, **kwargs):
        return {
            'cart_items': self.cart_items,
            'total': self.total,
            'item_count': len(self.cart_items)
        }
```

```html
<!-- templates/shop/cart.html -->
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    <title>Shopping Cart</title>
    {% djust_client_config %}
</head>
<body>
    <div dj-root>
    <div class="container">
        <h1>Shopping Cart ({{ item_count }} items)</h1>

        {% for item in cart_items %}
        <div class="cart-item">
            <h3>{{ item.product.name }}</h3>
            <p>${{ item.product.price }}</p>

            <input
                type="number"
                dj-change="update_quantity"
                dj-value-item-id:int="{{ item.id }}"
                value="{{ item.quantity }}"
                min="0"
            />

            <button
                dj-click="remove_item"
                dj-value-item-id:int="{{ item.id }}"
            >
                Remove
            </button>
        </div>
        {% endfor %}

        <div class="total">
            <h2>Total: ${{ total }}</h2>
            <button dj-click="clear_cart">Clear Cart</button>
        </div>
    </div>
    </div>
</body>
</html>
```

---

### Product Filters

**Use Case**: Multiple filters (category, price, rating) driven by one handler.

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, event_handler
from shop.models import Product

class ProductFilterView(LiveView):
    template_name = 'shop/product_filter.html'

    INT_FIELDS = {"min_price", "max_price", "min_rating"}
    TEXT_FIELDS = {"category"}
    SORT_OPTIONS = {"name", "price", "-price", "-rating"}

    def mount(self, request, **kwargs):
        self.category = ""
        self.min_price = 0
        self.max_price = 10000
        self.min_rating = 0
        self.sort = "name"
        self.results = Product.objects.filter(in_stock=True).order_by(self.sort)

    @event_handler
    @debounce(wait=0.3)
    def apply_filters(self, value: str = "", field: str = "", **kwargs):
        """Update the filter named by the input's `name`, then re-query."""
        if field in self.INT_FIELDS:
            setattr(self, field, int(value or 0))
        elif field in self.TEXT_FIELDS:
            setattr(self, field, value)
        elif field == "sort" and value in self.SORT_OPTIONS:
            self.sort = value
        else:
            return

        # Build query
        filters = {'in_stock': True}
        if self.category:
            filters['category'] = self.category
        filters['price__gte'] = self.min_price
        filters['price__lte'] = self.max_price
        filters['rating__gte'] = self.min_rating

        self.results = Product.objects.filter(**filters).order_by(self.sort)

    @event_handler
    def reset_filters(self, **kwargs):
        """Reset to defaults."""
        self.category = ""
        self.min_price = 0
        self.max_price = 10000
        self.min_rating = 0
        self.sort = "name"
        self.results = Product.objects.filter(in_stock=True).order_by('name')

    def get_context_data(self, **kwargs):
        return {
            'category': self.category,
            'min_price': self.min_price,
            'max_price': self.max_price,
            'min_rating': self.min_rating,
            'sort': self.sort,
            'results': self.results,
            'count': self.results.count()
        }
```

---

## Chat & Messaging

### Live Chat

**Use Case**: Real-time chat with typing indicators and message drafts.

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler, throttle
from djust.drafts import DraftModeMixin
from chat.models import Message, ChatRoom

class LiveChatView(DraftModeMixin, LiveView):
    template_name = 'chat/live_chat.html'
    draft_key = "chat_message"

    def mount(self, request, room_id, **kwargs):
        self.room = ChatRoom.objects.get(id=room_id)
        self.messages = Message.objects.filter(room=self.room).order_by('-created_at')[:50]
        self.message = ""
        self.typing_users = []

    def get_draft_key(self) -> str:
        """Include room ID in draft key for per-room drafts"""
        return f"chat_message_{self.room.id}"

    @event_handler
    @throttle(interval=2.0, leading=True, trailing=False)
    def typing_indicator(self, **kwargs):
        """
        Broadcast typing indicator.

        Throttled to max 1 update per 2 seconds
        Reduces server load during fast typing
        """
        # LiveView has no built-in broadcast_* methods. Notify the room with
        # your own helper built on server push (djust.push_to_view) or presence.
        notify_room_typing(self.room.id, self.request.user.username)  # your helper

    @event_handler
    def send_message(self, message: str = "", **kwargs):
        """
        Send chat message.

        dj-submit sends the form's fields by name, so `message` arrives here.
        Server persists and notifies others.
        """
        if not message.strip():
            return

        # Save to database
        msg = Message.objects.create(
            room=self.room,
            user=self.request.user,
            text=message
        )

        # Notify other users (your helper, e.g. built on push_to_view)
        notify_room_message(self.room.id, msg.id)

        # Update local state
        self.message = ""
        self.messages = Message.objects.filter(room=self.room).order_by('-created_at')[:50]

    def get_context_data(self, **kwargs):
        # Call super() so DraftModeMixin adds draft_enabled / draft_key
        context = super().get_context_data(**kwargs)
        context.update({
            'room': self.room,
            'messages': self.messages,
            'message': self.message,
            'typing_users': self.typing_users
        })
        return context
```

```html
<!-- templates/chat/live_chat.html -->
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    <title>Chat: {{ room.name }}</title>
    {% djust_client_config %}
</head>
<body>
    <div dj-root>
    <div class="chat-container"
         {% if draft_enabled %}data-draft-enabled data-draft-key="{{ draft_key }}"{% endif %}>

        <div class="messages">
            {% for msg in messages %}
            <div class="message">
                <strong>{{ msg.user.username }}:</strong>
                {{ msg.text }}
            </div>
            {% endfor %}
        </div>

        <div class="typing-indicator">
            {% for user in typing_users %}
            {{ user }} is typing...
            {% endfor %}
        </div>

        <form dj-submit="send_message">
            <input
                type="text"
                dj-input="typing_indicator"
                name="message"
                data-draft="true"
                value="{{ message }}"
                placeholder="Type a message..."
            />
            <button
                type="submit"
                dj-disable-with="Sending..."
            >
                Send
            </button>
        </form>
    </div>
    </div>
</body>
</html>
```

---

### Message Composer

**Use Case**: Email composer with auto-save drafts.

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler
from djust.forms import FormMixin
from djust.drafts import DraftModeMixin
from messaging.forms import MessageForm

class MessageComposerView(DraftModeMixin, FormMixin, LiveView):
    template_name = 'messaging/composer.html'
    form_class = MessageForm
    draft_key = "message_composer"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)  # FormMixin sets up form state here
        self.to = ""
        self.subject = ""
        self.body = ""
        self.attachments = []
        self.success_message = ""

    def form_valid(self, form):
        """Send message and clear draft."""
        message = form.save(commit=False)
        message.sender = self.request.user
        message.save()

        # Clear draft on successful send (applied on the next page load)
        self.clear_draft()
        self.success_message = "Message sent!"
        self.to = ""
        self.subject = ""
        self.body = ""

    @event_handler
    def add_attachment(self, file_data, **kwargs):
        """Add attachment."""
        # Handle file upload
        self.attachments.append(file_data)

    @event_handler
    def remove_attachment(self, index: int = 0, **kwargs):
        """Remove attachment."""
        if 0 <= index < len(self.attachments):
            self.attachments.pop(index)

    def get_context_data(self, **kwargs):
        # Call super() so DraftModeMixin adds draft_enabled / draft_key
        context = super().get_context_data(**kwargs)
        context.update({
            'to': self.to,
            'subject': self.subject,
            'body': self.body,
            'attachments': self.attachments,
            'success_message': self.success_message,
        })
        return context
```

---

## Dashboard & Analytics

### Real-Time Dashboard

**Use Case**: Dashboard with auto-refresh and manual refresh.

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler, throttle
from analytics.models import Metric
from django.utils import timezone
from datetime import timedelta

class DashboardView(LiveView):
    template_name = 'analytics/dashboard.html'

    PERIODS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}

    def mount(self, request, **kwargs):
        self.period = "24h"
        self.metrics = self.fetch_metrics(self.period)
        self.last_updated = timezone.now()

    def fetch_metrics(self, period: str = "24h"):
        """Fetch metrics for period.

        This is a plain helper, not an event, so @cache would do nothing here
        (@cache caches event responses in the browser). For server-side
        memoization use Django's cache framework (django.core.cache).
        """
        start = timezone.now() - self.PERIODS.get(period, timedelta(hours=24))
        return Metric.objects.filter(timestamp__gte=start)

    @event_handler
    @throttle(interval=5.0, leading=True, trailing=False)
    def refresh(self, **kwargs):
        """
        Manual refresh.

        Throttled to max 1 refresh per 5 seconds
        Prevents spam clicking
        """
        self.metrics = self.fetch_metrics(self.period)
        self.last_updated = timezone.now()

    @event_handler
    def change_period(self, value: str = "24h", **kwargs):
        """Change time period (dj-change sends the selected option as `value`)."""
        self.period = value if value in self.PERIODS else "24h"
        self.metrics = self.fetch_metrics(self.period)
        self.last_updated = timezone.now()

    def get_context_data(self, **kwargs):
        return {
            'period': self.period,
            'metrics': self.metrics,
            'last_updated': self.last_updated,
            'total_users': self.metrics.filter(metric='users').count(),
            'total_revenue': sum(m.value for m in self.metrics.filter(metric='revenue'))
        }
```

```html
<!-- templates/analytics/dashboard.html -->
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    <title>Analytics Dashboard</title>
    {% djust_client_config %}
</head>
<body>
    <div dj-root>
    <div class="dashboard">
        <div class="controls">
            <select dj-change="change_period">
                <option value="24h" {% if period == "24h" %}selected{% endif %}>Last 24 Hours</option>
                <option value="7d" {% if period == "7d" %}selected{% endif %}>Last 7 Days</option>
                <option value="30d" {% if period == "30d" %}selected{% endif %}>Last 30 Days</option>
            </select>

            <button
                dj-click="refresh"
                dj-loading.disable
                dj-disable-with="Refreshing..."
            >
                Refresh
            </button>

            <span>Last updated: {{ last_updated|date:"H:i:s" }}</span>
        </div>

        <div class="metrics">
            <div class="metric-card">
                <h3>Total Users</h3>
                <p class="value">{{ total_users }}</p>
            </div>

            <div class="metric-card">
                <h3>Revenue</h3>
                <p class="value">${{ total_revenue }}</p>
            </div>
        </div>
    </div>
    </div>
</body>
</html>
```

---

### Chart Filters

**Use Case**: Interactive chart with several filter inputs.

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, event_handler
from analytics.models import SalesData
from django.db.models import Sum, Count
from datetime import datetime, timedelta

class SalesChartView(LiveView):
    template_name = 'analytics/sales_chart.html'

    def mount(self, request, **kwargs):
        self.start_date = (datetime.now() - timedelta(days=30)).date()
        self.end_date = datetime.now().date()
        self.group_by = "day"
        self.region = ""
        self.chart_data = self.calculate_chart_data()

    @event_handler
    @debounce(wait=0.5)
    def update_chart(self, value: str = "", field: str = "", **kwargs):
        """Update the filter named by the input's `name`, then recalculate."""
        if field in ("start_date", "end_date"):
            if value:
                setattr(self, field, datetime.strptime(value, "%Y-%m-%d").date())
        elif field == "group_by" and value in ("day", "week", "month"):
            self.group_by = value
        elif field == "region":
            self.region = value
        else:
            return

        self.chart_data = self.calculate_chart_data()

    def calculate_chart_data(self):
        """Calculate chart data points."""
        filters = {
            'date__gte': self.start_date,
            'date__lte': self.end_date
        }
        if self.region:
            filters['region'] = self.region

        queryset = SalesData.objects.filter(**filters)

        # Group by day/week/month
        if self.group_by == "day":
            return queryset.values('date').annotate(total=Sum('amount'))
        elif self.group_by == "week":
            return queryset.extra(select={'week': 'WEEK(date)'}).values('week').annotate(total=Sum('amount'))
        else:  # month
            return queryset.extra(select={'month': 'MONTH(date)'}).values('month').annotate(total=Sum('amount'))

    def get_context_data(self, **kwargs):
        return {
            'start_date': self.start_date,
            'end_date': self.end_date,
            'group_by': self.group_by,
            'region': self.region,
            'chart_data': list(self.chart_data)
        }
```

---

## Forms & Validation

### Contact Form with Drafts

**Use Case**: Contact form with auto-save drafts and real-time validation.

```python
# views.py
from djust import LiveView
from djust.forms import FormMixin
from djust.drafts import DraftModeMixin
from contact.forms import ContactForm

class ContactFormView(DraftModeMixin, FormMixin, LiveView):
    template_name = 'contact/form.html'
    form_class = ContactForm
    draft_key = "contact_form"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)  # FormMixin sets up form state here
        self.success_message = ""
        self.error_message = ""

    def form_valid(self, form):
        """Handle valid form submission."""
        # Send email
        form.send_email()

        self.success_message = "Message sent! We'll respond within 24 hours."
        self.error_message = ""

        # Clear the saved draft
        self.clear_draft()

    def form_invalid(self, form):
        """Handle invalid form."""
        self.error_message = "Please fix the errors below."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['success_message'] = self.success_message
        context['error_message'] = self.error_message
        return context
```

```html
<!-- templates/contact/form.html -->
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    <title>Contact Us</title>
    {% djust_client_config %}
</head>
<body>
    <div dj-root>
    <div class="container"
         {% if draft_enabled %}data-draft-enabled data-draft-key="{{ draft_key }}"{% endif %}
         {% if draft_clear %}data-draft-clear{% endif %}>
        <h1>Contact Us</h1>

        {% if success_message %}
        <div class="alert alert-success">{{ success_message }}</div>
        {% endif %}
        {% if error_message %}
        <div class="alert alert-danger">{{ error_message }}</div>
        {% endif %}

        <form dj-submit="submit_form">
            <input name="name" value="{{ form_data.name }}" data-draft="true" />
            <input name="email" type="email" value="{{ form_data.email }}" data-draft="true" />
            <textarea name="message" data-draft="true">{{ form_data.message }}</textarea>

            <button
                type="submit"
                dj-disable-with="Sending..."
            >
                Send Message
            </button>
        </form>

        <p class="help-text">
            Your message is automatically saved in this browser as you type.
        </p>
    </div>
    </div>
</body>
</html>
```

`submit_form` is FormMixin's submit handler; it validates the posted fields and calls `form_valid` or `form_invalid`. Errors are available as `field_errors`. The fields are written by hand here because each one needs `data-draft="true"`; without drafts, `{% live_form view %}` renders the whole form.

`clear_draft()` does not remove the draft immediately. The client reads `data-draft-clear` only when the page loads, so the saved draft is removed on the next full page load, not by the live update after submission.

---

### Multi-Step Form

**Use Case**: Multi-step registration with state persistence.

djust ships a multi-step helper, `WizardMixin`. It keeps per-step data on the view, validates each step with that step's form, and exposes the `next_step`, `prev_step` and `submit_wizard` handlers. FormMixin has no `get_form_class()` hook or `self.form` attribute, so don't build a wizard on top of it.

```python
# views.py
from django.contrib.auth.models import User
from djust import LiveView
from djust.wizard import WizardMixin
from registration.forms import Step1Form, Step2Form, Step3Form

class MultiStepRegistrationView(WizardMixin, LiveView):
    template_name = 'registration/multi_step.html'
    wizard_steps = [
        {"name": "account", "title": "Account", "form_class": Step1Form},
        {"name": "profile", "title": "Profile", "form_class": Step2Form},
        {"name": "confirm", "title": "Confirm", "form_class": Step3Form},
    ]

    def on_wizard_complete(self, step_data):
        """Called by submit_wizard once every step is valid.

        step_data maps each step name to the raw string values entered.
        """
        data = {**step_data["account"], **step_data["profile"], **step_data["confirm"]}
        User.objects.create_user(**data)
        self.success_message = "Registration complete!"
```

The template receives `current_step`, `total_steps`, `progress_percent`, `field_html`, `step_errors` and related values; see `djust.wizard` for the full list.

---

## Admin Panel

### Bulk Actions

**Use Case**: Select multiple items and perform bulk actions.

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler
from products.models import Product

class ProductBulkActionsView(LiveView):
    template_name = 'admin/product_bulk.html'

    def mount(self, request, **kwargs):
        self.products = Product.objects.all()
        self.selected_ids = []

    @event_handler
    def toggle_select(self, product_id: int = 0, **kwargs):
        """Toggle product selection (dj-value-product-id:int on the checkbox)."""
        if product_id in self.selected_ids:
            self.selected_ids.remove(product_id)
        else:
            self.selected_ids.append(product_id)

    @event_handler
    def select_all(self, **kwargs):
        """Select all products."""
        self.selected_ids = [p.id for p in self.products]

    @event_handler
    def deselect_all(self, **kwargs):
        """Deselect all products."""
        self.selected_ids = []

    @event_handler
    def bulk_delete(self, **kwargs):
        """Delete selected products."""
        Product.objects.filter(id__in=self.selected_ids).delete()
        self.products = Product.objects.all()
        self.selected_ids = []

    @event_handler
    def bulk_activate(self, **kwargs):
        """Activate selected products."""
        Product.objects.filter(id__in=self.selected_ids).update(is_active=True)
        self.products = Product.objects.all()

    @event_handler
    def bulk_deactivate(self, **kwargs):
        """Deactivate selected products."""
        Product.objects.filter(id__in=self.selected_ids).update(is_active=False)
        self.products = Product.objects.all()

    def get_context_data(self, **kwargs):
        return {
            'products': self.products,
            'selected_ids': self.selected_ids,
            'selected_count': len(self.selected_ids)
        }
```

---

### Inline Editing

**Use Case**: Edit table cells inline.

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, event_handler
from products.models import Product

class ProductInlineEditView(LiveView):
    template_name = 'admin/product_inline_edit.html'

    # Only these fields may be edited inline. `field` comes from the client
    # (dj-change fills it from the input's name), so never setattr an
    # arbitrary name onto the model.
    EDITABLE_FIELDS = {"name", "sku"}

    def mount(self, request, **kwargs):
        self.products = Product.objects.all()
        self.editing_cell = None

    @event_handler
    @debounce(wait=0.5)
    def update_field(
        self,
        product_id: int = 0,
        field: str = "",
        value: str = "",
        **kwargs
    ):
        """
        Update product field inline.

        Bind with: <input name="name" dj-change="update_field"
                          dj-value-product-id:int="{{ product.id }}">
        Debounced: Server request delayed 500ms
        """
        if field not in self.EDITABLE_FIELDS:
            return
        try:
            product = Product.objects.get(id=product_id)
            setattr(product, field, value)
            product.full_clean()
            product.save()

            # Refresh products
            self.products = Product.objects.all()

        except Product.DoesNotExist:
            pass

    @event_handler
    def start_editing(self, product_id: int = 0, field: str = "", **kwargs):
        """Mark cell as being edited."""
        self.editing_cell = f"{product_id}_{field}"

    @event_handler
    def stop_editing(self, **kwargs):
        """Stop editing."""
        self.editing_cell = None

    def get_context_data(self, **kwargs):
        return {
            'products': self.products,
            'editing_cell': self.editing_cell
        }
```

For richer validation, run the edit through a `ModelForm` restricted to the editable fields.

---

## Real-Time Collaboration

### Document Editor

**Use Case**: Collaborative document editing with conflict resolution.

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, event_handler
from documents.models import Document, DocumentVersion

class CollaborativeEditorView(LiveView):
    template_name = 'documents/editor.html'

    def mount(self, request, document_id, **kwargs):
        self.document = Document.objects.get(id=document_id)
        self.content = self.document.content
        self.version = self.document.version
        self.active_users = self.get_active_users()

    @event_handler
    @debounce(wait=1.0)
    def update_content(self, value: str = "", **kwargs):
        """
        Update document content (<textarea dj-input="update_content">).

        Debounced: Save after 1 second of inactivity
        """
        content = value
        # Check for conflicts
        current_doc = Document.objects.get(id=self.document.id)
        if current_doc.version != self.version:
            # Conflict! Merge changes
            self.content = self.merge_content(current_doc.content, content)
        else:
            self.content = content

        # Save new version
        self.document.content = self.content
        self.document.version += 1
        self.document.save()

        # Create version history
        DocumentVersion.objects.create(
            document=self.document,
            content=self.content,
            user=self.request.user,
            version=self.document.version
        )

        self.version = self.document.version

        # Notify other users. LiveView has no built-in broadcast_* methods;
        # use your own helper built on server push (djust.push_to_view).
        notify_document_updated(self.document.id)  # your helper

    def merge_content(self, base_content, new_content):
        """Simple merge strategy (override with sophisticated diff-merge)."""
        # In production, use difflib or similar
        return new_content

    def get_active_users(self):
        """Get list of active users."""
        # Implementation depends on session tracking
        return []

    def get_context_data(self, **kwargs):
        return {
            'document': self.document,
            'content': self.content,
            'version': self.version,
            'active_users': self.active_users
        }
```

---

### Shared Whiteboard

**Use Case**: Real-time collaborative whiteboard.

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler
from whiteboard.models import Whiteboard, DrawingAction

class SharedWhiteboardView(LiveView):
    template_name = 'whiteboard/shared.html'

    def mount(self, request, board_id, **kwargs):
        self.board = Whiteboard.objects.get(id=board_id)
        self.actions = DrawingAction.objects.filter(board=self.board).order_by('created_at')

    @event_handler
    def draw(
        self,
        x: int = 0,
        y: int = 0,
        color: str = "#000000",
        tool: str = "pen",
        **kwargs
    ):
        """
        Handle drawing action.

        Sent from a canvas hook: this.pushEvent("draw", {x, y, color, tool}).
        A hook's pushEvent bypasses @throttle/@debounce, so rate-limit
        pointer events in the hook itself (e.g. at most 20 per second).
        """
        # Save action
        action = DrawingAction.objects.create(
            board=self.board,
            user=self.request.user,
            x=x,
            y=y,
            color=color,
            tool=tool
        )

        # Notify other users (your helper, e.g. built on push_to_view)
        notify_board_action(self.board.id, action.id)

    @event_handler
    def clear_board(self, **kwargs):
        """Clear entire board."""
        DrawingAction.objects.filter(board=self.board).delete()
        self.actions = DrawingAction.objects.none()

        # Notify other users (your helper)
        notify_board_cleared(self.board.id)

    def get_context_data(self, **kwargs):
        return {
            'board': self.board,
            'actions': list(self.actions.values())
        }
```

---

## Summary

These examples demonstrate:

1. **@event_handler**: Required on every handler
2. **@debounce**: Search, filters, form inputs
3. **@throttle**: Refresh buttons, typing indicators, load more
4. **@cache**: Read-only lookups cached in the browser
5. **dj-loading.\*** and **dj-disable-with**: Loading states in HTML
6. **DraftModeMixin**: Forms, email composer, chat
7. **WizardMixin**: Multi-step forms

`@optimistic` and `@client_state` are inert and are not used here. For multi-user sync, use server push or presence.

**Key Takeaways:**

- ✅ Zero custom JavaScript required (except canvas-style hooks)
- ✅ Declarative Python decorators
- ✅ Composable patterns

**Next Steps:**

- Customize these examples for your use case
- Read [STATE_MANAGEMENT_API.md](STATE_MANAGEMENT_API.md) for complete API reference
- Check [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) for best practices

---

## Related Documentation

### State Management Documentation
- [State Management API Reference](STATE_MANAGEMENT_API.md) - Complete decorator documentation
- [State Management Patterns](STATE_MANAGEMENT_PATTERNS.md) - Best practices and anti-patterns
- [State Management Tutorial](STATE_MANAGEMENT_TUTORIAL.md) - Step-by-step Product Search tutorial
- [State Management Migration](STATE_MANAGEMENT_MIGRATION.md) - Migrate from JavaScript to Python
- [State Management Architecture](STATE_MANAGEMENT_ARCHITECTURE.md) - Implementation architecture
- [State Management Comparison](STATE_MANAGEMENT_COMPARISON.md) - vs Phoenix LiveView & Laravel Livewire

---

**Questions?** Open an issue on GitHub!
