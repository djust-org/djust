---
title: "Streaming & Real-Time Partial Updates"
slug: streaming
section: guides
order: 1
level: intermediate
description: "Stream token-by-token LLM responses and live feeds with StreamingMixin"
---

# Streaming & Real-Time Partial Updates

djust's streaming system enables token-by-token updates for LLM chat responses, live feeds, and any use case that requires sending incremental DOM changes without full re-renders.

## What You Get

- **StreamingMixin** -- Server-side mixin with `stream_to()`, `stream_insert()`, `stream_text()`, and more
- **Stream operations** -- append, prepend, replace, text, delete, error, start, done
- **`dj-stream` directive** -- Mark DOM elements as stream targets
- **Auto-batching** -- Rapid updates are batched to ~60fps to avoid flooding the WebSocket
- **Auto-scroll** -- Stream containers scroll to bottom when the user is near the end
- **Error recovery** -- Errors display inline without losing partial content

## Quick Start

### 1. Add StreamingMixin to Your View

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

        await self.stream_start("response")

        async for token in llm_stream(content):
            self.messages[-1]["content"] += token
            await self.stream_text("response", token)

        await self.stream_done("response")
```

### 2. Add Stream Targets in Your Template

```html
<div id="chat-messages">
    {% for msg in messages %}
        <div class="message {{ msg.role }}">{{ msg.content }}</div>
    {% endfor %}
</div>

<div dj-stream="response" dj-stream-mode="append"></div>
```

## Core Methods

All streaming methods are `async` and require a WebSocket connection.

### `stream_text(stream_name, text, mode="append", target=None)`

Stream plain text to a target element. Ideal for token-by-token LLM output.

```python
async for token in llm_stream(prompt):
    await self.stream_text("output", token)
```

### `stream_to(stream_name, target=None, html=None)`

Send a streaming partial update. If `html` is provided, sends it directly. Otherwise, re-renders the target fragment from the current template context.

```python
await self.stream_to("messages", target="#message-list")
await self.stream_to("output", html="<p>Processing...</p>")
```

### `stream_insert(stream_name, html, at="append", target=None)`

Insert HTML into a stream container without replacing existing content.

```python
await self.stream_insert("messages",
    html='<div class="msg">New message</div>',
    at="append")
```

### `stream_error(stream_name, error, target=None)`

Display an error inline, preserving any partial content already rendered.

```python
try:
    async for token in llm_stream(prompt):
        await self.stream_text("output", token)
except Exception as e:
    await self.stream_error("output", str(e))
```

### `stream_start(stream_name)` / `stream_done(stream_name)`

Signal stream lifecycle. Sets `data-stream-active="true"` on start, removes it on done. Dispatches `stream:start` and `stream:done` DOM events.

### `stream_delete(stream_name, selector)`

Remove a DOM element by CSS selector.

```python
await self.stream_delete("messages", "#msg-42")
```

## Template Directives

```html
<!-- Mark as stream target -->
<div dj-stream="output">Initial content here</div>

<!-- Tokens accumulate (default) -->
<div dj-stream="response" dj-stream-mode="append"></div>

<!-- Each update replaces content -->
<div dj-stream="status" dj-stream-mode="replace"></div>
```

## Client-Side Events

| Event | Detail | Fires When |
|-------|--------|------------|
| `stream:start` | `{stream}` | Stream begins |
| `stream:text` | `{text, mode, stream}` | Text content streamed |
| `stream:update` | `{op, stream}` | HTML replaced/appended/prepended |
| `stream:error` | `{error, stream}` | Error occurred |
| `stream:done` | `{stream}` | Stream completed |

```javascript
document.querySelector('[dj-stream="output"]')
    .addEventListener('stream:done', (e) => {
        console.log('Stream finished:', e.detail.stream);
    });
```

## Full Example: LLM Chat with Error Handling

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

## Best Practices

- Always wrap streaming loops in `try/except` and call `stream_error()` on failure to preserve partial content.
- Use `stream_start()` and `stream_done()` to bracket streams so the client can show loading states via `data-stream-active`.
- Use `stream_text()` for plain text (LLM tokens) and `stream_insert()` when you need HTML structure (log lines, chat bubbles).
- Apply `overflow-y: auto` with a `max-height` on stream containers for auto-scroll behavior.
- You do not need to throttle on the server side -- the client batches rapid updates to ~60fps automatically.
