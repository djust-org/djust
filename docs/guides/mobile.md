# Mobile & Touch Support

djust provides comprehensive mobile support with touch event directives, device detection, and responsive helpers. Build touch-friendly interfaces with gesture recognition, pull-to-refresh, and mobile-optimized loading states.

## Quick Start

```python
from djust import LiveView, event_handler
from djust.mixins import MobileMixin

class GalleryView(MobileMixin, LiveView):
    template_name = "gallery.html"

    def mount(self, request, **kwargs):
        self._init_mobile(request)  # Initialize device detection
        
        # Responsive configuration
        self.columns = self.responsive(mobile=1, tablet=2, desktop=4)
        self.show_swipe_hint = self.is_touch

    @event_handler
    def swipe_image(self, direction, **kwargs):
        if direction == "left":
            self.next_image()
        elif direction == "right":
            self.previous_image()

    @event_handler
    def long_press_image(self, image_id, **kwargs):
        self.show_image_menu(image_id)
```

```html
<!-- gallery.html -->
<div class="gallery" 
     dj-swipe-left="swipe_image" 
     dj-swipe-right="swipe_image"
     dj-longpress="long_press_image"
     data-image-id="{{ current_image.id }}">
    
    {% if show_swipe_hint %}
        <p class="hint">Swipe to navigate</p>
    {% endif %}
    
    <img src="{{ current_image.url }}" alt="{{ current_image.title }}">
</div>
```

## Device Detection

### MobileMixin

Add `MobileMixin` to your LiveView for automatic device detection:

```python
from djust import LiveView
from djust.mixins import MobileMixin

class MyView(MobileMixin, LiveView):
    template_name = "my_view.html"

    def mount(self, request, **kwargs):
        self._init_mobile(request)  # Must call to initialize detection
        
        # Now you can use device detection
        if self.is_mobile:
            self.items_per_page = 10
        elif self.is_tablet:
            self.items_per_page = 20
        else:
            self.items_per_page = 50
```

### Available Properties

| Property | Type | Description |
|----------|------|-------------|
| `is_mobile` | bool | True for phones (iPhone, Android Mobile, etc.) |
| `is_tablet` | bool | True for tablets (iPad, Android tablets, Kindle) |
| `is_desktop` | bool | True for desktop browsers |
| `is_touch` | bool | True for any touch device (mobile OR tablet) |
| `device_type` | str | `"mobile"`, `"tablet"`, or `"desktop"` |
| `user_agent` | str | Raw User-Agent string |

### Template Access

Device detection is automatically added to template context:

```html
{% if is_mobile %}
    <nav class="mobile-nav">
        <button dj-tap="toggle_menu">☰</button>
    </nav>
{% else %}
    <nav class="desktop-nav">
        <a href="/about/">About</a>
        <a href="/contact/">Contact</a>
    </nav>
{% endif %}

<div class="content {{ device_type }}-layout">
    <!-- Adapts to mobile/tablet/desktop -->
</div>
```

### Responsive Helpers

Use responsive helpers for clean, device-specific values:

```python
def mount(self, request, **kwargs):
    self._init_mobile(request)
    
    # Returns appropriate value based on device
    self.columns = self.responsive(mobile=1, tablet=2, desktop=4)
    
    # Shorthand for touch-specific values
    self.use_gestures = self.for_touch(True, False)
    
    # Mobile-only value
    self.simplified_ui = self.for_mobile(True, False)
```

## Touch Event Directives

### dj-tap — Fast Tap (No 300ms Delay)

`dj-tap` fires immediately on touch, without the 300ms delay that `dj-click` has on mobile browsers:

```html
<button dj-tap="select_item" data-item-id="{{ item.id }}">
    Select
</button>

<div class="card" dj-tap="open_detail('{{ item.id }}')">
    {{ item.title }}
</div>
```

**Event params sent to handler:**
- `touchX`: X coordinate of touch
- `touchY`: Y coordinate of touch
- All `data-*` attributes

### dj-longpress — Press and Hold

Fires after holding for a configurable duration (default: 500ms):

```html
<!-- Default 500ms -->
<div dj-longpress="show_context_menu" data-item-id="{{ item.id }}">
    Hold to see options
</div>

<!-- Custom duration -->
<div dj-longpress="enter_edit_mode" dj-longpress-duration="800">
    Hold longer to edit
</div>
```

**Configuration:**
- `dj-longpress-duration="800"` — Milliseconds before firing (default: 500)

**Event params:**
- `touchX`, `touchY`: Touch coordinates
- `duration`: The configured duration

### dj-swipe — Swipe Gestures

Detect swipe gestures with direction-specific handlers:

```html
<!-- Generic swipe (any direction) -->
<div dj-swipe="handle_swipe">
    Swipe me
</div>

<!-- Direction-specific handlers -->
<div class="carousel"
     dj-swipe-left="next_slide"
     dj-swipe-right="previous_slide">
    {{ slides[current_index] }}
</div>

<!-- Vertical swipes -->
<div class="feed"
     dj-swipe-up="load_more"
     dj-swipe-down="refresh">
    {% for item in items %}...{% endfor %}
</div>
```

**Configuration:**
- `dj-swipe-threshold="100"` — Minimum pixels for swipe detection (default: 50)

**Event params:**
- `direction`: `"left"`, `"right"`, `"up"`, or `"down"`
- `deltaX`: Horizontal distance swiped
- `deltaY`: Vertical distance swiped
- `velocity`: Speed of swipe (px/ms)

**Handler example:**

```python
@event_handler
def handle_swipe(self, direction, deltaX, deltaY, velocity, **kwargs):
    if direction == "left":
        self.show_next()
    elif direction == "right":
        self.show_previous()
    elif direction == "up" and velocity > 0.5:
        self.load_more()  # Fast upward swipe = load more
```

### dj-pinch — Pinch Zoom

Detect pinch gestures for zoom functionality:

```html
<div class="image-viewer" dj-pinch="handle_zoom">
    <img src="{{ image.url }}" style="transform: scale({{ zoom_level }})">
</div>
```

**Event params:**
- `scale`: Current scale factor (>1 = zoom in, <1 = zoom out)
- `pinchType`: `"zoom-in"` or `"zoom-out"`
- `currentDistance`: Current distance between fingers
- `initialDistance`: Initial distance between fingers

**Handler example:**

```python
@event_handler
def handle_zoom(self, scale, pinchType, **kwargs):
    # Clamp zoom between 0.5x and 3x
    self.zoom_level = max(0.5, min(3.0, scale))
```

### dj-pull-refresh — Pull to Refresh

Standard mobile pull-to-refresh pattern:

```html
<div class="feed" dj-pull-refresh="refresh_feed">
    {% for post in posts %}
        <article>{{ post.content }}</article>
    {% endfor %}
</div>
```

**Configuration:**
- `dj-pull-threshold="100"` — Pixels to pull before refresh triggers (default: 80)
- `dj-pull-resistance="3"` — Pull resistance factor (default: 2.5)

**Handler:**

```python
@event_handler
def refresh_feed(self, **kwargs):
    self.posts = Post.objects.order_by('-created')[:20]
    # View automatically re-renders with new data
```

The pull-to-refresh indicator is automatically shown during the pull gesture and while refreshing.

## Touch-Friendly Loading States

### dj-loading.touch

Add mobile-optimized loading behavior with larger touch targets:

```html
<button dj-tap="save"
        dj-loading.touch
        dj-loading.disable>
    Save
</button>
```

The `dj-loading.touch` modifier:
- Ensures minimum 44x44px touch target during loading
- Adds a centered loading spinner
- Prevents accidental double-taps

### Combining with Standard Loading

```html
<button dj-tap="submit_form"
        dj-loading.touch
        dj-loading.disable
        dj-loading.class="submitting">
    Submit
</button>

<div dj-loading.show="flex"
     dj-loading.touch
     class="loading-overlay"
     style="display: none;">
    <div class="spinner"></div>
    <p>Please wait...</p>
</div>
```

## Complete Examples

### Swipeable Card Stack

```python
class CardStackView(MobileMixin, LiveView):
    template_name = "cards.html"

    def mount(self, request, **kwargs):
        self._init_mobile(request)
        self.cards = list(Card.objects.all()[:10])
        self.current_index = 0

    @event_handler
    def swipe_card(self, direction, **kwargs):
        if direction == "right":
            self.like_current_card()
        elif direction == "left":
            self.skip_current_card()
        
        self.current_index += 1
        if self.current_index >= len(self.cards):
            self.load_more_cards()

    @event_handler
    def tap_card(self, **kwargs):
        self.show_card_detail = True
```

```html
<div class="card-stack">
    {% for card in cards %}
    <div class="card {% if forloop.counter0 == current_index %}active{% endif %}"
         dj-swipe-left="swipe_card"
         dj-swipe-right="swipe_card"
         dj-tap="tap_card">
        <h2>{{ card.title }}</h2>
        <p>{{ card.description }}</p>
        
        {% if is_touch %}
        <p class="hint">← Swipe → to choose</p>
        {% endif %}
    </div>
    {% endfor %}
</div>
```

### Image Gallery with Pinch Zoom

```python
class ImageGalleryView(MobileMixin, LiveView):
    template_name = "gallery.html"

    def mount(self, request, **kwargs):
        self._init_mobile(request)
        self.images = Image.objects.all()
        self.current_index = 0
        self.zoom_level = 1.0

    @event_handler
    def navigate(self, direction, **kwargs):
        if direction == "left":
            self.current_index = min(self.current_index + 1, len(self.images) - 1)
        else:
            self.current_index = max(self.current_index - 1, 0)
        self.zoom_level = 1.0  # Reset zoom on navigation

    @event_handler
    def zoom(self, scale, **kwargs):
        self.zoom_level = max(0.5, min(3.0, self.zoom_level * scale))

    @event_handler
    def reset_zoom(self, **kwargs):
        self.zoom_level = 1.0
```

```html
<div class="gallery-container"
     dj-swipe-left="navigate"
     dj-swipe-right="navigate"
     dj-pinch="zoom"
     dj-tap="reset_zoom">
    
    <img src="{{ images.current_index.url }}"
         style="transform: scale({{ zoom_level }})"
         alt="{{ images.current_index.title }}">
    
    <div class="gallery-nav">
        <span>{{ current_index + 1 }} / {{ images|length }}</span>
        {% if zoom_level != 1.0 %}
            <span>{{ zoom_level|floatformat:1 }}x</span>
        {% endif %}
    </div>
</div>
```

### Mobile Navigation Menu

```python
class NavigationView(MobileMixin, LiveView):
    template_name = "nav.html"

    def mount(self, request, **kwargs):
        self._init_mobile(request)
        self.menu_open = False

    @event_handler
    def toggle_menu(self, **kwargs):
        self.menu_open = not self.menu_open

    @event_handler
    def close_menu(self, **kwargs):
        self.menu_open = False
```

```html
{% if is_mobile %}
<header class="mobile-header">
    <button dj-tap="toggle_menu" class="menu-toggle">
        ☰
    </button>
    <h1>My App</h1>
</header>

<nav class="mobile-menu {% if menu_open %}open{% endif %}"
     dj-swipe-left="close_menu">
    <a dj-tap="close_menu" dj-navigate="/home/">Home</a>
    <a dj-tap="close_menu" dj-navigate="/profile/">Profile</a>
    <a dj-tap="close_menu" dj-navigate="/settings/">Settings</a>
</nav>

{% if menu_open %}
<div class="menu-backdrop" dj-tap="close_menu"></div>
{% endif %}

{% else %}
<header class="desktop-header">
    <nav>
        <a href="/home/">Home</a>
        <a href="/profile/">Profile</a>
        <a href="/settings/">Settings</a>
    </nav>
</header>
{% endif %}
```

## CSS for Touch Interactions

djust automatically injects minimal CSS for touch feedback, but you can customize it:

```css
/* Custom tap feedback */
.djust-tap-active {
    opacity: 0.8;
    transform: scale(0.97);
    transition: opacity 0.1s, transform 0.1s;
}

/* Custom longpress feedback */
.djust-longpress-active {
    opacity: 0.7;
    box-shadow: 0 0 0 3px rgba(0, 123, 255, 0.5);
}

/* Custom pull-to-refresh indicator */
.djust-pull-refresh-indicator {
    background: linear-gradient(to bottom, #f0f0f0, #e0e0e0);
}

.djust-pull-refresh-spinner {
    border-color: #ddd;
    border-top-color: #007bff;
}

/* Larger touch targets */
.touch-button {
    min-height: 44px;
    min-width: 44px;
    padding: 12px 24px;
}
```

## Best Practices

1. **Always call `_init_mobile(request)` in mount()** — Device detection requires the request object.

2. **Use `dj-tap` for interactive elements** — It's faster than `dj-click` on mobile.

3. **Provide visual feedback** — The built-in classes help, but consider adding your own transitions.

4. **Set appropriate thresholds** — Test gestures on real devices and adjust thresholds as needed.

5. **Consider accessibility** — Touch gestures should have keyboard/mouse alternatives:
   ```html
   <div dj-swipe-left="next" dj-click="next" tabindex="0" dj-keydown.right="next">
   ```

6. **Use `is_touch` for gesture hints** — Only show "swipe to..." hints on touch devices.

7. **Test on real devices** — Emulators don't capture all touch nuances.

## Browser Support

Touch events are supported on:
- iOS Safari 3.2+
- Android Browser 2.1+
- Chrome for Android
- Firefox for Android
- Samsung Internet

On browsers without touch support, touch directives are ignored and won't cause errors.
