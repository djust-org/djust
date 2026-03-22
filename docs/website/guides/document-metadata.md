---
title: "Document Metadata"
slug: document-metadata
section: guides
order: 8
level: beginner
description: "Dynamically update the browser tab title and meta tags from any LiveView handler -- no page reload needed"
---

# Document Metadata

djust lets you update `document.title` and `<meta>` tags from any LiveView event handler using simple property setters. Changes are pushed over the WebSocket as lightweight side-channel messages -- no VDOM diff cycle required.

## What You Get

- **`self.page_title`** -- Set the browser tab title from Python
- **`self.page_meta`** -- Set or create `<meta>` tags (description, og:image, twitter:card, etc.)
- **Side-channel delivery** -- Updates bypass the VDOM diff, arriving instantly
- **Both transports** -- Works over WebSocket and SSE

## Quick Start

```python
from djust import LiveView
from djust.decorators import event_handler


class ChatView(LiveView):
    template_name = "chat.html"

    def mount(self, request, **kwargs):
        self.unread = 0
        self.page_title = "Chat"

    @event_handler()
    def new_message(self, **kwargs):
        self.unread += 1
        self.page_title = f"Chat ({self.unread} unread)"
```

That is the entire setup for dynamic page titles. No template tags, no extra configuration.

## Page Title

Set `self.page_title` in `mount()` or any event handler to update the browser tab:

```python
def mount(self, request, **kwargs):
    self.page_title = "Dashboard"

@event_handler()
def select_tab(self, tab: str = "", **kwargs):
    self.page_title = f"Dashboard - {tab.title()}"
```

### Initial Render (HTTP)

On the initial HTTP render, `self.page_title` is available as a property on the view instance. Include it in your template context to set the initial `<title>`:

```python
def get_context_data(self, **kwargs):
    ctx = super().get_context_data(**kwargs)
    ctx["page_title"] = self.page_title
    return ctx
```

```html
<head>
    <title>{{ page_title }}</title>
    {% djust_scripts %}
</head>
```

After the WebSocket connects, subsequent `self.page_title = "..."` assignments update the title in-place without a page reload.

## Page Meta Tags

Set `self.page_meta` to a dictionary to update or create `<meta>` tags in the document `<head>`:

```python
@event_handler()
def select_article(self, article_id: int = 0, **kwargs):
    article = Article.objects.get(pk=article_id)
    self.page_title = article.title
    self.page_meta = {
        "description": article.summary,
        "og:title": article.title,
        "og:image": article.image_url,
        "twitter:card": "summary_large_image",
    }
```

### How Meta Tags Are Resolved

- **Standard meta tags** (e.g., `description`, `author`) use `<meta name="..." content="...">`
- **Open Graph and Twitter tags** (names starting with `og:` or `twitter:`) use `<meta property="..." content="...">`
- If a matching `<meta>` tag already exists in the document, its `content` attribute is updated
- If no matching tag exists, a new `<meta>` element is appended to `<head>`

### Setting Initial Meta Tags (HTTP)

For the initial page load, include standard `<meta>` tags in your template as usual:

```html
<head>
    <title>{{ page_title }}</title>
    <meta name="description" content="{{ page_description }}">
    <meta property="og:title" content="{{ page_title }}">
    {% djust_scripts %}
</head>
```

After mount, `self.page_meta = {...}` updates these tags in-place over the WebSocket.

## Common Patterns

### Unread Count in Tab Title

```python
class InboxView(LiveView):
    def mount(self, request, **kwargs):
        self.unread = Message.objects.filter(read=False, user=request.user).count()
        self._update_title()

    @event_handler()
    def mark_read(self, message_id: int = 0, **kwargs):
        Message.objects.filter(pk=message_id).update(read=True)
        self.unread = max(0, self.unread - 1)
        self._update_title()

    def _update_title(self):
        if self.unread:
            self.page_title = f"Inbox ({self.unread})"
        else:
            self.page_title = "Inbox"
```

### Status Indicator in Tab Title

```python
@event_handler()
def toggle_recording(self, **kwargs):
    self.recording = not self.recording
    prefix = "● REC" if self.recording else "◼ Stopped"
    self.page_title = f"{prefix} - Studio"
```

### Dynamic SEO Meta for SPA Navigation

```python
@event_handler()
def navigate_to_product(self, product_id: int = 0, **kwargs):
    product = Product.objects.get(pk=product_id)
    self.page_title = f"{product.name} | Store"
    self.page_meta = {
        "description": product.short_description,
        "og:title": product.name,
        "og:description": product.short_description,
        "og:image": product.image.url,
        "og:type": "product",
    }
    self.current_product = product
```

### Combined with Flash Messages

```python
@event_handler()
def save(self, **kwargs):
    save_data(self._form_data)
    self.put_flash("success", "Changes saved!")
    self.page_title = f"{self.project_name} - Saved"
```

## How It Works

1. Setting `self.page_title` or `self.page_meta` queues metadata commands on an internal `_pending_page_metadata` list
2. After each WebSocket/SSE response, the consumer calls `_drain_page_metadata()` to collect pending commands
3. Each command is sent as a `{"type": "page_metadata", ...}` message over the transport
4. The client JS (`25-page-metadata.js`) receives the message and updates `document.title` or `<meta>` tags directly in the DOM

This uses the same side-channel pattern as [flash messages](flash-messages.md) -- metadata updates are flushed after the main response, avoiding VDOM overhead.

## See Also

- [Flash Messages](flash-messages.md) -- Transient notifications using the same side-channel pattern
- [LiveView API Reference](../api-reference/liveview.md) -- Full API for `page_title` and `page_meta`
- [Template Cheat Sheet](template-cheatsheet.md) -- Quick reference for all directives
