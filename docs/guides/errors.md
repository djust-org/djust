# Error Handling

djust provides developer-friendly error handling with rich error overlays in development mode.

## Development Error Overlay

In debug mode, djust shows a rich error overlay when server-side errors occur:

![Error Overlay](../images/error-overlay.png)

The overlay includes:
- Exception type and message
- Syntax-highlighted Python traceback
- The event that triggered the error
- Copy button for the traceback

### Enabling Debug Mode

The error overlay appears when Django's debug mode is enabled:

```python
# settings.py
DEBUG = True
```

Or add `data-debug` to your body tag:

```html
<body data-debug>
    ...
</body>
```

### Dismissing the Overlay

- Press **Esc** to dismiss
- Click outside the overlay panel
- Click the **×** button

## Server-Side Error Handling

### Handler Errors

When an event handler raises an exception:
- In debug mode: Error overlay is shown
- In production: Error is logged, client receives generic error

```python
class MyView(LiveView):
    @event_handler
    def risky_action(self, **kwargs):
        try:
            dangerous_operation()
        except SpecificError as e:
            # Handle gracefully
            self.error_message = str(e)
        except Exception:
            # Let it bubble up for the error overlay
            raise
```

### Custom Error Handling

Catch and display errors in your template:

```python
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.error = None
        self.success = None

    @event_handler
    def save_data(self, **kwargs):
        try:
            self.do_save()
            self.success = "Saved successfully!"
            self.error = None
        except ValidationError as e:
            self.error = str(e)
            self.success = None
```

```html
{% if error %}
    <div class="alert alert-error">{{ error }}</div>
{% endif %}

{% if success %}
    <div class="alert alert-success">{{ success }}</div>
{% endif %}
```

## Client-Side Error Events

Listen for error events in JavaScript:

```javascript
window.addEventListener('djust:error', (event) => {
    const { error, traceback, event: eventName } = event.detail;
    
    // Custom error handling
    console.error('LiveView error:', error);
    
    // Example: Send to error tracking service
    Sentry.captureException(new Error(error), {
        extra: { traceback, eventName }
    });
});
```

### Suppress Default Overlay

Prevent the default error overlay:

```javascript
window.addEventListener('djust:error', (event) => {
    // Handle error yourself
    showCustomErrorUI(event.detail.error);
    
    // Prevent default overlay
    event.preventDefault();
});
```

## WebSocket Errors

### Connection Errors

djust handles WebSocket disconnections automatically:
- Shows reconnection attempts
- Restores state on reconnect
- Notifies JS hooks via `disconnected()` / `reconnected()`

### Rate Limiting

When rate limited, the server sends close code `4429`:

```javascript
// Client receives close code 4429
// djust shows rate limit message and backs off
```

## Production Error Handling

In production (`DEBUG = False`):

1. **No error overlay**: Errors don't expose internals to users
2. **Logged server-side**: Errors are logged via Django's logging
3. **Generic client message**: User sees "An error occurred"

### Custom Error Messages

```python
# settings.py
DJUST_CONFIG = {
    'error_message': 'Something went wrong. Please try again.',
}
```

### Logging Configuration

```python
# settings.py
LOGGING = {
    'version': 1,
    'handlers': {
        'file': {
            'class': 'logging.FileHandler',
            'filename': 'logs/djust.log',
        },
    },
    'loggers': {
        'djust': {
            'handlers': ['file'],
            'level': 'ERROR',
        },
    },
}
```

## Validation Errors

For form validation, use structured error handling:

```python
from djust.forms import LiveForm

class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.form = LiveForm({
            "email": {"required": True, "email": True},
            "age": {"required": True, "min": 18},
        })

    @event_handler
    def submit(self, **data):
        self.form.set_values(data)
        if not self.form.validate_all():
            # Errors are in self.form.errors
            return
        
        # Process valid data
        self.process(self.form.data)
```

```html
{% for field, error in form.errors.items %}
    <div class="error">{{ field }}: {{ error }}</div>
{% endfor %}
```

## Error Boundaries

For complex views, isolate error-prone sections:

```python
class DashboardView(LiveView):
    @event_handler
    def load_widget_data(self, widget_id, **kwargs):
        try:
            self.widgets[widget_id] = load_external_data(widget_id)
        except ExternalAPIError as e:
            # Don't break the whole dashboard
            self.widgets[widget_id] = {
                'error': True,
                'message': 'Unable to load data',
            }
```

```html
{% for widget_id, widget in widgets.items %}
    <div class="widget">
        {% if widget.error %}
            <div class="widget-error">
                {{ widget.message }}
                <button dj-click="load_widget_data" dj-value-widget_id="{{ widget_id }}">
                    Retry
                </button>
            </div>
        {% else %}
            {{ widget.content }}
        {% endif %}
    </div>
{% endfor %}
```

## Graceful Degradation

Handle missing JavaScript gracefully:

```html
<noscript>
    <div class="warning">
        This page requires JavaScript for real-time updates.
        <a href="{{ request.path }}?static=1">View static version</a>
    </div>
</noscript>
```

```python
class MyView(LiveView):
    def get(self, request, **kwargs):
        if request.GET.get('static'):
            # Render without WebSocket
            return render(request, 'static_fallback.html', self.get_context_data())
        return super().get(request, **kwargs)
```

## Best Practices

1. **Catch expected errors**: Handle known failure cases gracefully
2. **Let unexpected errors bubble**: Don't catch `Exception` blindly
3. **Show user-friendly messages**: Don't expose technical details in production
4. **Log errors**: Ensure errors are captured for debugging
5. **Test error paths**: Write tests for error handling
6. **Use validation**: Validate input before processing
