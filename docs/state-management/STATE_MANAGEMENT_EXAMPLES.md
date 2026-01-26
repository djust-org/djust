# State Management Examples

**Status**: Ready to Copy
**Version**: djust 0.4.0

This document provides complete, copy-paste ready examples demonstrating state management decorators in real-world scenarios.

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
| **Limit event frequency** | `@throttle(interval)` | Scroll handler, resize, mousemove | `@throttle(interval=0.1)` |
| **Instant UI feedback** | `@optimistic` | Counter, toggle, checkbox, cart add | `@optimistic` |
| **Cache server responses** | `@cache(ttl, key_params)` | Autocomplete, search results, API calls | `@cache(ttl=60, key_params=["query"])` |
| **Share state between components** | `@client_state(keys)` | Dashboard filters, coordinated views | `@client_state(keys=["filter"])` |
| **Auto-save form drafts** | `DraftModeMixin` | Long forms, email composer, comments | `class MyView(DraftModeMixin, LiveView)` |
| **Show/hide loading indicators** | `@loading` (HTML) | Button states, spinner visibility | `<button @loading>` |

### Common Decorator Combinations

| Pattern | Decorators | Use Case |
|---------|------------|----------|
| **Debounced Search** | `@debounce(0.5)` | Basic search (100 keystrokes → 1 request) |
| **Smart Search** | `@debounce(0.5)`<br>`@optimistic`<br>`@cache(ttl=60)` | Search with instant feedback + caching |
| **Real-Time Updates** | `@optimistic`<br>`@client_state(keys=["count"])` | Counter with component coordination |
| **Filtered Dashboard** | `@debounce(0.3)`<br>`@client_state(keys=["filter", "sort"])`<br>`@cache(ttl=300)` | Dashboard with multiple coordinated filters |
| **Form with Drafts** | `DraftModeMixin`<br>`@debounce(1.0)` | Auto-save form with server sync |
| **Infinite Scroll** | `@throttle(interval=1.0)`<br>`@cache(ttl=60)` | Load more with rate limiting |

### Quick Copy-Paste Templates

**1. Debounced Search:**
```python
from djust.decorators import debounce

@debounce(wait=0.5)
def search(self, query: str = "", **kwargs):
    self.results = Model.objects.filter(name__icontains=query)
```

**2. Optimistic Counter:**
```python
from djust.decorators import optimistic

@optimistic
def increment(self, **kwargs):
    self.count += 1
```

**3. Cached API Call:**
```python
from djust.decorators import cache

@cache(ttl=300, key_params=["city"])
def get_weather(self, city: str = "", **kwargs):
    self.weather = fetch_weather_api(city)
```

**4. Coordinated Filters:**
```python
from djust.decorators import client_state, debounce

@debounce(wait=0.3)
@client_state(keys=["category", "min_price"])
def apply_filters(self, category: str = "", min_price: int = 0, **kwargs):
    self.results = Product.objects.filter(
        category=category,
        price__gte=min_price
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
<!-- Template needs draft attributes on root -->
<div data-liveview-root
     data-draft-enabled="{{ draft_enabled }}"
     data-draft-key="{{ draft_key }}">
    <input name="name" data-draft="true" />
    <input name="email" data-draft="true" />
    <textarea name="message" data-draft="true"></textarea>
</div>
```

**6. Throttled Scroll Handler:**
```python
from djust.decorators import throttle

@throttle(interval=0.2, leading=True, trailing=True)
def on_scroll(self, scroll_y: int = 0, **kwargs):
    if scroll_y > 1000:
        self.show_back_to_top = True
```

### Performance Guidelines

| Decorator | Recommended Values | Impact |
|-----------|-------------------|--------|
| `@debounce` | `wait=0.3-0.5` (search)<br>`wait=1.0-2.0` (auto-save) | Reduces requests by 80-95% |
| `@throttle` | `interval=0.1-0.2` (scroll)<br>`interval=1.0-5.0` (polling) | Limits to 5-10 events/sec or 0.2-1 events/sec |
| `@cache` | `ttl=60` (autocomplete)<br>`ttl=300` (search)<br>`ttl=3600` (static data) | Reduces server load by 40-80% |
| `DraftModeMixin` | Auto-saves after 500ms debounce | No server requests (localStorage only) |

### Decorator Order Rules

**Always use this order (top to bottom):**

```python
@debounce(wait=0.5)       # 1. Rate limiting (debounce/throttle)
@optimistic                # 2. Optimistic updates
@cache(ttl=60)            # 3. Response caching
@client_state(keys=[...]) # 4. State sharing
def my_handler(self, **kwargs):
    pass
```

**Why this order?**
1. **Rate limiting first** - Reduces events before processing
2. **Optimistic second** - UI updates before debounce delay
3. **Cache third** - Check cache before sending to server
4. **Client state last** - Broadcast after all processing

### Troubleshooting Quick Fixes

| Problem | Solution |
|---------|----------|
| Debounce not working | Check `wait` is in seconds (not milliseconds): `wait=0.5` not `wait=500` |
| Optimistic updates flicker | Add `@debounce()` to batch rapid changes |
| Cache always misses | Ensure `key_params` matches event data keys |
| Form drafts not restoring | Check template has `data-draft-enabled` and `data-draft-key` on root element, fields have `data-draft="true"` |
| Loading indicator stuck | Verify handler doesn't throw exception (causes loading state to persist) |

---

## E-Commerce Examples

### Product Search

**Use Case**: Real-time product search with debouncing, caching, and filters.

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, optimistic, cache, client_state
from shop.models import Product

class ProductSearchView(LiveView):
    template_name = 'shop/product_search.html'

    CATEGORIES = [
        ('', 'All Categories'),
        ('electronics', 'Electronics'),
        ('clothing', 'Clothing'),
        ('books', 'Books'),
    ]

    def mount(self, request, **kwargs):
        self.query = ""
        self.category = ""
        self.min_price = 0
        self.max_price = 10000
        self.results = Product.objects.filter(in_stock=True)[:20]

    @debounce(wait=0.5)
    @optimistic
    @cache(ttl=60, key_params=["query", "category", "min_price", "max_price"])
    @client_state(keys=["query", "category", "min_price", "max_price"])
    def search(
        self,
        query: str = "",
        category: str = "",
        min_price: int = 0,
        max_price: int = 10000,
        **kwargs
    ):
        """Search products with filters."""
        self.query = query
        self.category = category
        self.min_price = min_price
        self.max_price = max_price

        # Build query
        filters = {'in_stock': True}
        if query:
            filters['name__icontains'] = query
        if category:
            filters['category'] = category
        filters['price__gte'] = min_price
        filters['price__lte'] = max_price

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
{% load djust %}
<!DOCTYPE html>
<html>
<head>
    <title>Product Search</title>
    {% djust_head %}
</head>
<body>
    <div class="container" @loading>
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
            dj-input="search"
            name="min_price"
            value="{{ min_price }}"
            min="0"
            max="10000"
        />

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
    {% djust_body %}
</body>
</html>
```

---

### Shopping Cart

**Use Case**: Add/remove items with optimistic updates and server validation.

```python
# views.py
from djust import LiveView
from djust.decorators import optimistic, throttle
from shop.models import Product, CartItem
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator

@method_decorator(login_required, name='dispatch')
class ShoppingCartView(LiveView):
    template_name = 'shop/cart.html'

    def mount(self, request, **kwargs):
        self.cart_items = CartItem.objects.filter(user=request.user)
        self.total = sum(item.subtotal for item in self.cart_items)

    @optimistic
    def update_quantity(self, item_id: int = 0, quantity: int = 1, **kwargs):
        """
        Update cart item quantity.

        Optimistic: UI updates instantly
        Server validates and corrects if needed
        """
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

    @optimistic
    def remove_item(self, item_id: int = 0, **kwargs):
        """Remove item from cart."""
        try:
            item = CartItem.objects.get(id=item_id, user=self.request.user)
            item.delete()

            self.cart_items = CartItem.objects.filter(user=self.request.user)
            self.total = sum(item.subtotal for item in self.cart_items)
        except CartItem.DoesNotExist:
            pass

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
{% load djust %}
<!DOCTYPE html>
<html>
<head>
    <title>Shopping Cart</title>
    {% djust_head %}
</head>
<body>
    <div class="container">
        <h1>Shopping Cart ({{ item_count }} items)</h1>

        {% for item in cart_items %}
        <div class="cart-item">
            <h3>{{ item.product.name }}</h3>
            <p>${{ item.product.price }}</p>

            <input
                type="number"
                dj-input="update_quantity"
                data-item-id="{{ item.id }}"
                value="{{ item.quantity }}"
                min="0"
            />

            <button
                dj-click="remove_item"
                data-item-id="{{ item.id }}"
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
    {% djust_body %}
</body>
</html>
```

---

### Product Filters

**Use Case**: Multiple coordinated filters (category, price, rating).

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, client_state, cache
from shop.models import Product

class ProductFilterView(LiveView):
    template_name = 'shop/product_filter.html'

    def mount(self, request, **kwargs):
        self.category = ""
        self.min_price = 0
        self.max_price = 10000
        self.min_rating = 0
        self.sort = "name"
        self.results = Product.objects.filter(in_stock=True).order_by(self.sort)

    @debounce(wait=0.3)
    @cache(ttl=120, key_params=["category", "min_price", "max_price", "min_rating", "sort"])
    @client_state(keys=["category", "min_price", "max_price", "min_rating", "sort"])
    def apply_filters(
        self,
        category: str = "",
        min_price: int = 0,
        max_price: int = 10000,
        min_rating: int = 0,
        sort: str = "name",
        **kwargs
    ):
        """Apply all filters at once."""
        self.category = category
        self.min_price = min_price
        self.max_price = max_price
        self.min_rating = min_rating
        self.sort = sort

        # Build query
        filters = {'in_stock': True}
        if category:
            filters['category'] = category
        filters['price__gte'] = min_price
        filters['price__lte'] = max_price
        filters['rating__gte'] = min_rating

        self.results = Product.objects.filter(**filters).order_by(sort)

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
from djust.decorators import throttle, optimistic
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

    @throttle(interval=2.0, leading=True, trailing=False)
    def typing_indicator(self, **kwargs):
        """
        Broadcast typing indicator.

        Throttled to max 1 update per 2 seconds
        Reduces server load during fast typing
        """
        # Broadcast to other users in room
        self.broadcast_typing(self.request.user.username)

    @optimistic
    def send_message(self, message: str = "", **kwargs):
        """
        Send chat message.

        Optimistic: Message appears instantly
        Server persists and broadcasts to others
        """
        if not message.strip():
            return

        # Save to database
        msg = Message.objects.create(
            room=self.room,
            user=self.request.user,
            text=message
        )

        # Broadcast to other users
        self.broadcast_message(msg)

        # Update local state
        self.message = ""
        self.messages = Message.objects.filter(room=self.room).order_by('-created_at')[:50]

    def get_context_data(self, **kwargs):
        return {
            'room': self.room,
            'messages': self.messages,
            'message': self.message,
            'typing_users': self.typing_users
        }
```

```html
<!-- templates/chat/live_chat.html -->
{% load djust %}
<!DOCTYPE html>
<html>
<head>
    <title>Chat: {{ room.name }}</title>
    {% djust_head %}
</head>
<body>
    <div class="chat-container"
         data-liveview-root
         data-draft-enabled="{{ draft_enabled }}"
         data-draft-key="{{ draft_key }}">

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
                @loading-text="Sending..."
            >
                Send
            </button>
        </form>
    </div>
    {% djust_body %}
</body>
</html>
```

---

### Message Composer

**Use Case**: Email composer with auto-save drafts.

```python
# views.py
from djust import LiveView
from djust.forms import FormMixin
from djust.drafts import DraftModeMixin
from messaging.forms import MessageForm

class MessageComposerView(DraftModeMixin, FormMixin, LiveView):
    template_name = 'messaging/composer.html'
    form_class = MessageForm
    draft_key = "message_composer"

    def mount(self, request, **kwargs):
        self.to = ""
        self.subject = ""
        self.body = ""
        self.attachments = []

    def form_valid(self, form):
        """Send message and clear draft."""
        message = form.save(commit=False)
        message.sender = self.request.user
        message.save()

        # Clear draft on successful send
        self.clear_draft()
        self.success_message = "Message sent!"
        self.to = ""
        self.subject = ""
        self.body = ""

    def add_attachment(self, file_data, **kwargs):
        """Add attachment."""
        # Handle file upload
        self.attachments.append(file_data)

    def remove_attachment(self, index: int = 0, **kwargs):
        """Remove attachment."""
        if 0 <= index < len(self.attachments):
            self.attachments.pop(index)

    def get_context_data(self, **kwargs):
        return {
            'to': self.to,
            'subject': self.subject,
            'body': self.body,
            'attachments': self.attachments
        }
```

---

## Dashboard & Analytics

### Real-Time Dashboard

**Use Case**: Dashboard with auto-refresh and manual refresh.

```python
# views.py
from djust import LiveView
from djust.decorators import cache, throttle
from analytics.models import Metric
from django.utils import timezone
from datetime import timedelta

class DashboardView(LiveView):
    template_name = 'analytics/dashboard.html'

    def mount(self, request, **kwargs):
        self.period = "24h"
        self.metrics = self.fetch_metrics(self.period)
        self.last_updated = timezone.now()

    @cache(ttl=300, key_params=["period"])  # Cache for 5 minutes
    def fetch_metrics(self, period: str = "24h"):
        """Fetch metrics for period."""
        if period == "24h":
            start = timezone.now() - timedelta(hours=24)
        elif period == "7d":
            start = timezone.now() - timedelta(days=7)
        elif period == "30d":
            start = timezone.now() - timedelta(days=30)
        else:
            start = timezone.now() - timedelta(hours=24)

        return Metric.objects.filter(timestamp__gte=start)

    @throttle(interval=5.0, leading=True, trailing=False)
    def refresh(self, **kwargs):
        """
        Manual refresh.

        Throttled to max 1 refresh per 5 seconds
        Prevents spam clicking
        """
        self.metrics = self.fetch_metrics(self.period)
        self.last_updated = timezone.now()

    def change_period(self, period: str = "24h", **kwargs):
        """Change time period."""
        self.period = period
        self.metrics = self.fetch_metrics(period)
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
{% load djust %}
<!DOCTYPE html>
<html>
<head>
    <title>Analytics Dashboard</title>
    {% djust_head %}
</head>
<body>
    <div class="dashboard">
        <div class="controls">
            <select dj-change="change_period">
                <option value="24h" {% if period == "24h" %}selected{% endif %}>Last 24 Hours</option>
                <option value="7d" {% if period == "7d" %}selected{% endif %}>Last 7 Days</option>
                <option value="30d" {% if period == "30d" %}selected{% endif %}>Last 30 Days</option>
            </select>

            <button
                dj-click="refresh"
                @loading-text="Refreshing..."
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
    {% djust_body %}
</body>
</html>
```

---

### Chart Filters

**Use Case**: Interactive chart with coordinated filters.

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, client_state, cache
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

    @debounce(wait=0.5)
    @cache(ttl=600, key_params=["start_date", "end_date", "group_by", "region"])
    @client_state(keys=["start_date", "end_date", "group_by", "region"])
    def update_chart(
        self,
        start_date: str = "",
        end_date: str = "",
        group_by: str = "day",
        region: str = "",
        **kwargs
    ):
        """Update chart data."""
        if start_date:
            self.start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        if end_date:
            self.end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        self.group_by = group_by
        self.region = region

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
    draft_ttl = 3600  # 1 hour

    def mount(self, request, **kwargs):
        self.success_message = ""

    def form_valid(self, form):
        """Handle valid form submission."""
        # Send email
        form.send_email()

        self.success_message = "Message sent! We'll respond within 24 hours."

        # Clear draft
        self.draft_key = None

    def form_invalid(self, form):
        """Handle invalid form."""
        self.error_message = "Please fix the errors below."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['success_message'] = self.success_message
        return context
```

```html
<!-- templates/contact/form.html -->
{% load djust %}
<!DOCTYPE html>
<html>
<head>
    <title>Contact Us</title>
    {% djust_head %}
</head>
<body>
    <div class="container">
        <h1>Contact Us</h1>

        {% if success_message %}
        <div class="alert alert-success">{{ success_message }}</div>
        {% endif %}

        <form dj-submit="handle_form_submit">
            {{ form.as_p }}

            <button
                type="submit"
                @loading-text="Sending..."
            >
                Send Message
            </button>
        </form>

        <p class="help-text">
            Your message is automatically saved as you type.
        </p>
    </div>
    {% djust_body %}
</body>
</html>
```

---

### Multi-Step Form

**Use Case**: Multi-step registration with state persistence.

```python
# views.py
from djust import LiveView
from djust.forms import FormMixin
from djust.drafts import DraftModeMixin
from registration.forms import Step1Form, Step2Form, Step3Form

class MultiStepRegistrationView(DraftModeMixin, FormMixin, LiveView):
    template_name = 'registration/multi_step.html'
    draft_key = "registration_form"
    draft_ttl = 7200  # 2 hours

    def mount(self, request, **kwargs):
        self.step = 1
        self.step1_data = {}
        self.step2_data = {}
        self.step3_data = {}

    def get_form_class(self):
        """Return form for current step."""
        if self.step == 1:
            return Step1Form
        elif self.step == 2:
            return Step2Form
        else:
            return Step3Form

    def next_step(self, **kwargs):
        """Move to next step."""
        # Save current step data
        if self.step == 1:
            self.step1_data = self.form.cleaned_data
        elif self.step == 2:
            self.step2_data = self.form.cleaned_data

        self.step += 1

    def previous_step(self, **kwargs):
        """Move to previous step."""
        if self.step > 1:
            self.step -= 1

    def form_valid(self, form):
        """Handle final submission."""
        if self.step < 3:
            self.next_step()
        else:
            # Final step - save all data
            self.step3_data = form.cleaned_data
            self.save_registration()

    def save_registration(self):
        """Save all registration data."""
        # Combine all step data
        data = {**self.step1_data, **self.step2_data, **self.step3_data}

        # Create user
        user = User.objects.create(**data)

        # Clear draft
        self.draft_key = None

        self.success_message = "Registration complete!"

    def get_context_data(self, **kwargs):
        return {
            'step': self.step,
            'total_steps': 3,
            'progress': (self.step / 3) * 100
        }
```

---

## Admin Panel

### Bulk Actions

**Use Case**: Select multiple items and perform bulk actions.

```python
# views.py
from djust import LiveView
from djust.decorators import optimistic
from products.models import Product

class ProductBulkActionsView(LiveView):
    template_name = 'admin/product_bulk.html'

    def mount(self, request, **kwargs):
        self.products = Product.objects.all()
        self.selected_ids = []

    @optimistic
    def toggle_select(self, product_id: int = 0, **kwargs):
        """Toggle product selection."""
        if product_id in self.selected_ids:
            self.selected_ids.remove(product_id)
        else:
            self.selected_ids.append(product_id)

    def select_all(self, **kwargs):
        """Select all products."""
        self.selected_ids = [p.id for p in self.products]

    def deselect_all(self, **kwargs):
        """Deselect all products."""
        self.selected_ids = []

    def bulk_delete(self, **kwargs):
        """Delete selected products."""
        Product.objects.filter(id__in=self.selected_ids).delete()
        self.products = Product.objects.all()
        self.selected_ids = []

    def bulk_activate(self, **kwargs):
        """Activate selected products."""
        Product.objects.filter(id__in=self.selected_ids).update(is_active=True)
        self.products = Product.objects.all()

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

**Use Case**: Edit table cells inline with optimistic updates.

```python
# views.py
from djust import LiveView
from djust.decorators import optimistic, debounce
from products.models import Product

class ProductInlineEditView(LiveView):
    template_name = 'admin/product_inline_edit.html'

    def mount(self, request, **kwargs):
        self.products = Product.objects.all()
        self.editing_cell = None

    @optimistic
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

        Optimistic: UI updates instantly
        Debounced: Server request delayed 500ms
        """
        try:
            product = Product.objects.get(id=product_id)
            setattr(product, field, value)
            product.save()

            # Refresh products
            self.products = Product.objects.all()

        except Product.DoesNotExist:
            pass

    def start_editing(self, product_id: int = 0, field: str = "", **kwargs):
        """Mark cell as being edited."""
        self.editing_cell = f"{product_id}_{field}"

    def stop_editing(self, **kwargs):
        """Stop editing."""
        self.editing_cell = None

    def get_context_data(self, **kwargs):
        return {
            'products': self.products,
            'editing_cell': self.editing_cell
        }
```

---

## Real-Time Collaboration

### Document Editor

**Use Case**: Collaborative document editing with conflict resolution.

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, optimistic, client_state
from documents.models import Document, DocumentVersion

class CollaborativeEditorView(LiveView):
    template_name = 'documents/editor.html'

    def mount(self, request, document_id, **kwargs):
        self.document = Document.objects.get(id=document_id)
        self.content = self.document.content
        self.version = self.document.version
        self.active_users = self.get_active_users()

    @debounce(wait=1.0)
    @optimistic
    @client_state(keys=["content"])
    def update_content(self, content: str = "", **kwargs):
        """
        Update document content.

        Optimistic: Textarea updates instantly
        Debounced: Save after 1 second of inactivity
        Client State: Broadcast to other users
        """
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

        # Broadcast to other users
        self.broadcast_update()

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
from djust.decorators import throttle, optimistic, client_state
from whiteboard.models import Whiteboard, DrawingAction

class SharedWhiteboardView(LiveView):
    template_name = 'whiteboard/shared.html'

    def mount(self, request, board_id, **kwargs):
        self.board = Whiteboard.objects.get(id=board_id)
        self.actions = DrawingAction.objects.filter(board=self.board).order_by('created_at')

    @throttle(interval=0.05, leading=True, trailing=True)  # Max 20 events/sec
    @optimistic
    @client_state(keys=["x", "y", "color", "tool"])
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

        Throttled: Max 20 events/second (60 FPS / 3)
        Optimistic: Canvas updates instantly
        Client State: Broadcast to other users
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

        # Broadcast to other users
        self.broadcast_drawing_action(action)

    def clear_board(self, **kwargs):
        """Clear entire board."""
        DrawingAction.objects.filter(board=self.board).delete()
        self.actions = []

        # Broadcast clear action
        self.broadcast_clear()

    def get_context_data(self, **kwargs):
        return {
            'board': self.board,
            'actions': list(self.actions.values())
        }
```

---

## Summary

These examples demonstrate:

1. **@debounce**: Search, filters, form inputs
2. **@throttle**: Scroll events, drawing, refresh buttons
3. **@optimistic**: Shopping cart, inline editing, chat
4. **@cache**: Expensive queries, dashboard metrics
5. **@client_state**: Coordinated filters, collaborative editing
6. **DraftModeMixin**: Forms, email composer, registration

**Key Takeaways:**

- ✅ Zero custom JavaScript required
- ✅ Declarative Python decorators
- ✅ Composable patterns
- ✅ Production-ready examples

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

### Marketing & Competitive Analysis
- [Marketing Overview](MARKETING.md) - Feature highlights and positioning
- [Framework Comparison](FRAMEWORK_COMPARISON.md) - djust vs 13+ frameworks
- [Technical Pitch](TECHNICAL_PITCH.md) - Technical selling points
- [Why Not Alternatives](WHY_NOT_ALTERNATIVES.md) - When to choose djust

---

**Questions?** Open an issue on GitHub!
