# Streaming (Real-Time Partial DOM Updates)

djust's streaming system enables token-by-token updates for LLM chat responses, live feeds, and any use case that requires sending incremental DOM changes without full re-renders.

## Overview

- **StreamingMixin** - Server-side mixin with `stream_to()`, `stream_insert()`, `stream_text()`, and more
- **Stream operations** - append, prepend, replace, text, delete, error, start, done
- **dj-stream directive** - Mark DOM elements as stream targets
- **Auto-batching** - Rapid updates are batched to ~60fps to avoid flooding the WebSocket
- **Auto-scroll** - Stream containers automatically scroll to bottom when the user is near the end
- **Error recovery** - Errors are displayed inline without losing partial content

## Quick Start

### 1. Add StreamingMixin to your view

```python
from djust import LiveView
from djust.streaming import StreamingMixin
from djust.decorators import event_handler

class ChatView(StreamingMixin, LiveView):
    template_name = 'chat.html'

    async def mount(self, request, **kwargs):
        self.messages = []

    @event_handler()
    async def send_message(self, content="", **kwargs):
        self.messages.append({"role": "user", "content": content})
        self.messages.append({"role": "assistant", "content": ""})

        # Signal stream start
        await self.stream_start("response")

        async for token in llm_stream(content):
            self.messages[-1]["content"] += token
            await self.stream_text("response", token)

        # Signal stream complete
        await self.stream_done("response")
```

### 2. Add stream targets in your template

```html
<div id="chat-messages">
    {% for msg in messages %}
        <div class="message {{ msg.role }}">{{ msg.content }}</div>
    {% endfor %}
</div>

<!-- Stream target for the assistant's response -->
<div dj-stream="response" dj-stream-mode="append"></div>
```

## API Reference

### StreamingMixin Methods

All streaming methods are `async` and require the view to have a WebSocket consumer attached.

#### `stream_to(stream_name, target=None, html=None)`

Send a streaming partial update. If `html` is provided, sends it directly. Otherwise, re-renders the target fragment from the current template context.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stream_name` | `str` | required | Logical name for the stream (e.g., `"messages"`) |
| `target` | `str` or `None` | `None` | CSS selector for the container. Defaults to `[dj-stream='stream_name']`. |
| `html` | `str` or `None` | `None` | Pre-rendered HTML fragment. If omitted, re-renders from template. |

```python
# Re-render from template
await self.stream_to("messages", target="#message-list")

# Send raw HTML
await self.stream_to("output", html="<p>Processing...</p>")
```

#### `stream_insert(stream_name, html, at="append", target=None)`

Insert HTML into a stream container without replacing existing content.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stream_name` | `str` | required | Stream name |
| `html` | `str` | required | HTML fragment to insert |
| `at` | `str` | `"append"` | `"append"` or `"prepend"` |
| `target` | `str` or `None` | `None` | CSS selector. Defaults to `[dj-stream='stream_name']`. |

```python
await self.stream_insert("messages",
    html='<div class="msg">New message</div>',
    at="append")
```

#### `stream_text(stream_name, text, mode="append", target=None)`

Stream text content to a target element. Ideal for token-by-token LLM output.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stream_name` | `str` | required | Stream name |
| `text` | `str` | required | Text content to stream |
| `mode` | `str` | `"append"` | `"append"`, `"replace"`, or `"prepend"` |
| `target` | `str` or `None` | `None` | CSS selector |

```python
# Append each token
async for token in llm_stream(prompt):
    await self.stream_text("output", token)
```

#### `stream_error(stream_name, error, target=None)`

Display an error inline on the stream target, preserving any partial content already rendered.

```python
try:
    async for token in llm_stream(prompt):
        await self.stream_text("output", token)
except Exception as e:
    await self.stream_error("output", str(e))
```

#### `stream_start(stream_name, target=None)`

Signal the beginning of a stream. Sets `data-stream-active="true"` on the target element and dispatches a `stream:start` DOM event.

#### `stream_done(stream_name, target=None)`

Signal the end of a stream. Removes `data-stream-active` attribute and dispatches a `stream:done` DOM event.

#### `stream_delete(stream_name, selector)`

Remove a DOM element by CSS selector.

```python
await self.stream_delete("messages", "#msg-42")
```

#### `push_state()`

Send a full re-render to the client immediately. Useful for showing intermediate state during long-running async operations.

```python
self.status = "Analyzing..."
await self.push_state()
# ... long operation ...
self.status = "Done"
await self.push_state()
```

### Template Directives

#### `dj-stream="name"`

Mark an element as a stream target. The server references this name in `stream_to()`, `stream_text()`, etc.

```html
<div dj-stream="output">Initial content here</div>
```

#### `dj-stream-mode="append|replace|prepend"`

Set the default text insertion mode for `stream_text()` operations on this element.

```html
<!-- Tokens accumulate (default) -->
<div dj-stream="response" dj-stream-mode="append"></div>

<!-- Each update replaces content -->
<div dj-stream="status" dj-stream-mode="replace"></div>
```

### Client-Side Events

Stream operations dispatch custom DOM events on the target element:

| Event | Detail | Description |
|-------|--------|-------------|
| `stream:start` | `{stream}` | Stream began |
| `stream:update` | `{op, stream}` | Content replaced, appended, or prepended |
| `stream:text` | `{text, mode, stream}` | Text content streamed |
| `stream:error` | `{error, stream}` | Error occurred |
| `stream:done` | `{stream}` | Stream completed |
| `stream:remove` | `{stream}` | Element about to be deleted |

```javascript
document.querySelector('[dj-stream="output"]')
    .addEventListener('stream:done', (e) => {
        console.log('Stream finished:', e.detail.stream);
    });
```

## Examples

### LLM Chat with Error Handling

```python
class AIChat(StreamingMixin, LiveView):
    template_name = 'ai_chat.html'

    async def mount(self, request, **kwargs):
        self.messages = []
        self.is_streaming = False

    @event_handler()
    async def ask(self, content="", **kwargs):
        if self.is_streaming:
            return

        self.messages.append({"role": "user", "content": content})
        self.is_streaming = True
        await self.push_state()

        await self.stream_start("response")
        response_text = ""

        try:
            async for token in call_llm(content):
                response_text += token
                await self.stream_text("response", token)

            self.messages.append({"role": "assistant", "content": response_text})
            await self.stream_done("response")
        except Exception as e:
            await self.stream_error("response", f"Error: {e}")
        finally:
            self.is_streaming = False
```

```html
<div id="chat">
    {% for msg in messages %}
    <div class="message {{ msg.role }}">{{ msg.content }}</div>
    {% endfor %}
    <div dj-stream="response" dj-stream-mode="append" class="message assistant"></div>
</div>

<form dj-submit="ask">
    <input type="text" name="content" placeholder="Ask something..."
           {% if is_streaming %}disabled{% endif %}>
    <button type="submit" {% if is_streaming %}disabled{% endif %}>Send</button>
</form>
```

### Live Log Viewer

```python
class LogViewer(StreamingMixin, LiveView):
    template_name = 'logs.html'

    async def mount(self, request, **kwargs):
        self.log_file = kwargs.get("file", "app.log")

    @event_handler()
    async def start_tail(self, **kwargs):
        await self.stream_start("logs")

        async for line in tail_file(self.log_file):
            level_class = "error" if "ERROR" in line else "info"
            await self.stream_insert("logs",
                html=f'<div class="log-line {level_class}">{line}</div>')

        await self.stream_done("logs")
```

```html
<div dj-stream="logs" class="log-container"
     style="max-height: 500px; overflow-y: auto;">
    <!-- Log lines appended here automatically -->
</div>
<button dj-click="start_tail">Start Tailing</button>
```

## Best Practices

### Error Handling

- Always wrap streaming loops in try/except and call `stream_error()` on failure. This preserves partial content and shows the error inline.
- Use `stream_start()` and `stream_done()` to bracket streams, enabling the client to show loading indicators via the `data-stream-active` attribute.

### Auto-Scroll Behavior

- The client automatically scrolls stream containers to the bottom when the user is within 100px of the bottom before the update.
- If the user has scrolled up to read earlier content, auto-scroll is suppressed so their position is preserved.
- Use CSS `overflow-y: auto` with a `max-height` on stream containers for best results.

### Performance

- `stream_text()` and `stream_to()` batch rapid updates to ~60fps (16ms intervals). You do not need to throttle on the server side.
- For large content, prefer `stream_insert()` with small HTML fragments over `stream_to()` which replaces the entire container.
- Use `stream_text()` for plain text (LLM tokens). Use `stream_insert()` when you need HTML structure (log lines, chat bubbles).
