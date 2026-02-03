# Accessibility Guide

djust includes built-in accessibility features to help you build WCAG-compliant applications that work well with screen readers and keyboard navigation.

## Overview

The accessibility features include:

- **Screen reader announcements** — Announce dynamic changes to screen readers
- **Focus management** — Control focus after DOM updates
- **ARIA live regions** — Auto-inject ARIA attributes on patched elements
- **Keyboard navigation** — Make all interactive elements keyboard accessible
- **Loading state announcements** — Inform screen readers about loading states

## Quick Start

```python
from djust import LiveView

class ContactForm(LiveView):
    template_name = "contact.html"
    
    def mount(self, request, **kwargs):
        self.name = ""
        self.email = ""
        self.message = ""
        self.errors = {}
        self.success = False
    
    def handle_submit(self, name, email, message):
        self.errors = self.validate(name, email, message)
        
        if self.errors:
            # Announce errors to screen readers
            self.announce("Form contains errors. Please review.", priority="assertive")
            # Focus the first error
            self.focus_first_error()
        else:
            self.save_message(name, email, message)
            self.success = True
            # Announce success
            self.announce("Message sent successfully!")
            # Focus the success message
            self.focus("#success-message")
```

## Screen Reader Announcements

Use `announce()` to push messages to screen readers via ARIA live regions.

### Basic Usage

```python
def handle_save(self):
    self.save_data()
    self.announce("Changes saved successfully!")
```

### Priority Levels

- **polite** (default) — Waits for the user to finish what they're doing
- **assertive** — Interrupts immediately; use for errors and critical alerts

```python
# Polite announcement (default)
self.announce("3 new messages")

# Assertive announcement for errors
self.announce("Error: Invalid email address", priority="assertive")
```

### When to Use Each Priority

| Priority | Use Case |
|----------|----------|
| `polite` | Success messages, status updates, non-critical info |
| `assertive` | Errors, warnings, time-sensitive alerts |

## Focus Management

Control where focus goes after DOM updates.

### Basic Focus

```python
def handle_submit(self):
    if self.errors:
        self.focus("#error-summary")
    else:
        self.focus("#success-message")
```

### Focus Options

```python
# Focus with scroll (default)
self.focus("#element", scroll=True)

# Focus without scrolling
self.focus("#element", scroll=False)

# Prevent any scroll behavior
self.focus("#element", prevent_scroll=True)

# Delay focus (useful for animations)
self.focus("#element", delay_ms=300)
```

### Auto-Focus First Error

```python
def handle_validate(self):
    self.errors = self.validate_form()
    if self.errors:
        self.focus_first_error()
```

This looks for elements with common error classes:
- `.error`, `.is-invalid`, `.field-error`, `.form-error`
- `[aria-invalid="true"]`, `[role="alert"]`

## ARIA Live Regions

djust automatically injects `aria-live` attributes on elements that receive DOM patches.

### Default Behavior

- Patched elements get `aria-live="polite"` by default
- Elements with error classes get `aria-live="assertive"`

### Configuration

Control the default behavior at the view level:

```python
class MyView(LiveView):
    aria_live_default = "polite"  # "polite", "assertive", or "off"
    auto_focus_errors = True      # Auto-focus first error after form submission
    announce_loading = True       # Announce loading states
```

### Per-Element Control

Use `dj-aria-live` to control ARIA on specific elements:

```html
<!-- Use assertive for this element -->
<div dj-aria-live="assertive" id="alerts">
    {% for alert in alerts %}
        <p>{{ alert.message }}</p>
    {% endfor %}
</div>

<!-- Disable ARIA announcements for this element -->
<div dj-aria-live="off" id="live-preview">
    {{ preview_content }}
</div>

<!-- Use polite (explicit) -->
<div dj-aria-live="polite" id="status">
    {{ status_message }}
</div>
```

## Keyboard Navigation

djust automatically makes `dj-click` elements keyboard accessible.

### Automatic Enhancements

For non-button elements with `dj-click`:
- Adds `role="button"` attribute
- Adds `tabindex="0"` to make it focusable
- Handles Enter and Space key presses

```html
<!-- This div becomes keyboard accessible automatically -->
<div dj-click="select_item" data-item-id="123">
    Select Item
</div>
```

### Custom Key Handlers

Use `dj-keyboard` for custom keyboard interactions:

```html
<!-- Handle any keydown -->
<input dj-keyboard="handle_key" type="text">

<!-- Handle specific keys -->
<input dj-keyboard.enter="submit_search" type="text">
<input dj-keyboard.escape="clear_search" type="text">

<!-- Multiple key handlers -->
<div dj-keyboard.up="previous_item" 
     dj-keyboard.down="next_item"
     dj-keyboard.enter="select_item">
```

Server-side handler:

```python
def handle_key(self, key, code, ctrlKey, shiftKey, altKey, metaKey):
    if key == "ArrowDown":
        self.selected_index += 1
    elif key == "ArrowUp":
        self.selected_index -= 1
```

### Supported Key Modifiers

| Modifier | Key |
|----------|-----|
| `enter` | Enter |
| `escape` | Escape |
| `space` | Space |
| `tab` | Tab |
| `up` | ArrowUp |
| `down` | ArrowDown |
| `left` | ArrowLeft |
| `right` | ArrowRight |

## Loading State Announcements

When `dj-loading` triggers, screen readers are notified automatically.

### Configuration

Enable/disable at the view level:

```python
class MyView(LiveView):
    announce_loading = True  # Default
```

### Behavior

- When loading starts (>200ms), announces "Loading..."
- Loading completion is handled by the actual content update
- Quick operations (<200ms) don't announce to avoid noise

## Focus Directive

Use `dj-focus` in templates for one-time focus after updates:

```html
<!-- Focus this element after it appears -->
{% if show_message %}
<div dj-focus id="message">
    {{ message }}
</div>
{% endif %}
```

The `dj-focus` attribute is removed after the focus is applied.

## Focus Preservation

djust automatically preserves focus position during DOM patches:

- Saves the focused element before patches
- Restores focus after patches complete
- Preserves text selection in inputs

This works automatically — no configuration needed.

## Best Practices

### 1. Announce State Changes

Always announce important state changes that might not be obvious visually:

```python
def handle_toggle_menu(self):
    self.menu_open = not self.menu_open
    state = "expanded" if self.menu_open else "collapsed"
    self.announce(f"Menu {state}")
```

### 2. Provide Context

Make announcements informative:

```python
# ❌ Too vague
self.announce("Done")

# ✅ Provides context
self.announce(f"Email sent to {recipient}")
```

### 3. Don't Over-Announce

Only announce changes that matter to users:

```python
# ❌ Don't announce every keystroke
def handle_input(self, value):
    self.search_query = value
    self.announce(f"Searching for {value}")  # Too noisy!

# ✅ Announce results instead
def handle_search_complete(self):
    count = len(self.results)
    self.announce(f"Found {count} results")
```

### 4. Use Semantic HTML

Screen readers work best with semantic HTML:

```html
<!-- ✅ Good: Semantic elements -->
<button dj-click="save">Save</button>
<nav>...</nav>
<main>...</main>

<!-- ❌ Avoid: Non-semantic elements for interactive content -->
<div dj-click="save">Save</div>
```

### 5. Test with Screen Readers

Test your application with actual screen readers:

- **macOS**: VoiceOver (built-in, Cmd+F5)
- **Windows**: NVDA (free), JAWS
- **Linux**: Orca

## WCAG Compliance

These features help you meet WCAG 2.1 guidelines:

| Guideline | djust Feature |
|-----------|---------------|
| 1.3.1 Info and Relationships | Auto ARIA attributes |
| 2.1.1 Keyboard | Keyboard navigation for dj-click |
| 2.4.3 Focus Order | Focus management |
| 4.1.2 Name, Role, Value | role="button" on clickable elements |
| 4.1.3 Status Messages | announce() for live updates |

## API Reference

### Python Methods

```python
# Announce to screen readers
view.announce(message: str, priority: str = "polite")

# Set focus
view.focus(selector: str, scroll: bool = True, 
           prevent_scroll: bool = False, delay_ms: int = 0)

# Focus first error
view.focus_first_error()
```

### View Configuration

```python
class MyView(LiveView):
    aria_live_default = "polite"  # Default ARIA live priority
    auto_focus_errors = True      # Auto-focus first error
    announce_loading = True       # Announce loading states
```

### Template Directives

```html
<!-- ARIA live region control -->
<div dj-aria-live="polite|assertive|off">...</div>

<!-- Focus after update -->
<div dj-focus>...</div>

<!-- Custom keyboard handlers -->
<input dj-keyboard="handler_name">
<input dj-keyboard.enter="on_enter">
```

### JavaScript API

```javascript
// Manual announcements
window.djust.accessibility.announce("Message", "polite");

// Manual focus
window.djust.accessibility.setFocus("#element", { scroll: true });

// Update configuration
window.djust.accessibility.setConfig({
    ariaLiveDefault: "polite",
    autoFocusErrors: true,
    announceLoading: true
});
```

## Troubleshooting

### Announcements Not Working

1. Check browser/screen reader combination
2. Verify the live region exists in the DOM
3. Test with different screen readers

### Focus Not Moving

1. Ensure the target element exists after DOM update
2. Check that the selector is valid
3. Try adding `delay_ms` for animated elements

### Keyboard Navigation Issues

1. Verify `dj-click` is on the element
2. Check that no JavaScript is preventing default behavior
3. Ensure the element isn't disabled
