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

- **StreamingMixin** -- Server-side mixin with `stream_to()`, `stream_text()`, `stream_error()`, and more
- **Stream operations** -- append, prepend, replace, text, delete, error, start, done
- **`dj-stream` directive** -- Mark DOM elements as stream targets
- **Server-side throttling** -- `stream_to()` / `stream_text()` send at most one op per ~16 ms per view; see [Throttling](#throttling-keeps-only-the-newest-op)
- **Auto-scroll** -- Stream containers scroll to bottom when the user is near the end
- **Error recovery** -- Errors display inline without losing partial content

## Quick Start

### 1. Subclass LiveView (streaming is built in)

`StreamingMixin` is already included in `LiveView`, so the `stream_*` methods are
always available — just subclass `LiveView`. Do **not** also inherit `StreamingMixin`
(e.g. `class ChatView(StreamingMixin, LiveView)`); because it is already in `LiveView`'s
bases, that raises `TypeError: Cannot create a consistent method resolution`.

```python
from djust import LiveView
from djust.decorators import event_handler

class ChatView(LiveView):
    template_name = 'chat.html'

    def mount(self, request, **kwargs):
        self.messages = []
        self.is_streaming = False

    @event_handler()
    async def send_message(self, content="", **kwargs):
        self.messages.append({"role": "user", "content": content})
        self.is_streaming = True
        await self.push_state()  # render now, so the stream target exists

        await self.stream_start("response")
        reply = ""
        async for token in llm_stream(content):
            reply += token
            # Send the whole reply so far. Throttling keeps only the newest op
            # in each ~16 ms window, so per-token appends can lose tokens.
            await self.stream_text("response", reply, mode="replace")
        await self.stream_done("response")

        # The finished reply joins the normal render; the stream target is
        # removed (it only renders while is_streaming), so it isn't shown twice.
        self.messages.append({"role": "assistant", "content": reply})
        self.is_streaming = False
```

`mount()` must be a plain `def`. djust calls it synchronously, so an
`async def mount` never runs its body (HTTP) or raises `TypeError` (WebSocket).

### 2. Add Stream Targets in Your Template

```html
<div id="chat-messages">
    {% for msg in messages %}
        <div class="message {{ msg.role }}">{{ msg.content }}</div>
    {% endfor %}
</div>

{% if is_streaming %}
<div dj-stream="response" class="message assistant"></div>
{% endif %}
```

## Core Methods

All streaming methods are `async` and require a WebSocket connection.

### `stream_text(stream_name, text, mode="append", target=None)`

Stream plain text to a target element. The text is written with
`textContent`, so it is never parsed as HTML.

```python
reply = ""
async for token in llm_stream(prompt):
    reply += token
    await self.stream_text("output", reply, mode="replace")
```

The server always sends `mode` (default `"append"`), so pass it explicitly
rather than relying on the element's `dj-stream-mode` attribute.

#### Throttling keeps only the newest op

`stream_text()` and `stream_to()` are throttled **on the server**, to one send
per ~16 ms. A call that arrives inside that window does not queue: it
*replaces* whatever op is waiting, and only the newest op is sent when the
window ends. With `mode="append"`, each op carries one token, so tokens that
arrive faster than every 16 ms are dropped. For example, five back-to-back
appends of `a`…`e` reach the browser as `a` and `e`.

For LLM output, accumulate the text server-side and send the whole string
with `mode="replace"`, as above (or `stream_to(name, html=...)`). If you need
append mode, make sure consecutive sends are at least 16 ms apart.

### `stream_to(stream_name, target=None, html=None)`

Send a streaming partial update. If `html` is provided, sends it directly. Otherwise, re-renders the target fragment from the current template context.

```python
await self.stream_to("messages", target="#message-list")
await self.stream_to("output", html="<p>Processing...</p>")
```

> **Prefer passing `html=` for a single element.** The no-`html=` form
> re-renders the **entire** template and regex-extracts the target element —
> convenient, but it does a full render per call and is easy to double-write
> against the main diff. If you already have the fragment (e.g. from
> `render_markdown(...)`), pass it as `html=`.

### Appending or prepending raw HTML

`StreamingMixin` has an async `stream_insert(stream_name, html, at="append", target=None)`,
but on a `LiveView` the name `self.stream_insert` resolves to the **sync collection-stream
API** (`stream()` / `stream_insert(name, item, at=-1)`, see the [LiveView API reference](../api-reference/liveview.md#streaming)), which
comes first in `LiveView`'s bases. `await self.stream_insert(..., html=...)` therefore
raises `TypeError`. Call the streaming version explicitly:

```python
from djust.streaming import StreamingMixin

await StreamingMixin.stream_insert(
    self, "messages", '<div class="msg">New message</div>', at="append"
)
```

`at` is `"append"` or `"prepend"`. The HTML is inserted as-is, so escape any user
content you interpolate into it.

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

### Removing an element by selector

The same name clash applies: `self.stream_delete(name, item_or_id)` on a `LiveView` is the
sync collection-stream method. To remove a DOM element by CSS selector, call the streaming
version explicitly:

```python
await StreamingMixin.stream_delete(self, "messages", "#msg-42")
```

## Template Directives

```html
<!-- Mark as stream target -->
<div dj-stream="output">Initial content here</div>

<!-- Default mode for text ops that don't carry one. stream_text() always
     sends a mode (default "append"), so pass mode= in Python instead. -->
<div dj-stream="status" dj-stream-mode="replace"></div>

<!-- A target the handler ALSO re-renders (via djust_markdown, {{ var }}, etc.)
     should carry dj-update="ignore" so the main diff leaves it to the stream ops -->
<article dj-stream="reply" dj-update="ignore">{% djust_markdown reply %}</article>
```

> **Exclude a streamed target from the main diff with `dj-update="ignore"`.**
> If the same element is both a `dj-stream` target *and* ordinary diffed
> content (e.g. it wraps `{% djust_markdown var %}` or `{{ var }}`), the
> handler's event-completion `render_with_diff()` will re-write the region the
> stream ops just wrote — the client receives the content twice through two
> paths in close succession (visible duplicate/flicker). Add `dj-update="ignore"`
> to the target (checked client-side in `12-vdom-patch.js`) so the stream ops
> are authoritative for that region.

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
class AIChat(LiveView):  # streaming is built in — no StreamingMixin needed
    template_name = 'ai_chat.html'

    def mount(self, request, **kwargs):
        self.messages = []
        self.is_streaming = False
        self.stream_failed = False

    @event_handler()
    async def ask(self, content="", **kwargs):
        if self.is_streaming:
            return

        self.messages.append({"role": "user", "content": content})
        self.is_streaming = True
        self.stream_failed = False
        await self.push_state()

        await self.stream_start("response")
        response_text = ""

        try:
            async for token in call_llm(content):
                response_text += token
                await self.stream_text("response", response_text, mode="replace")

            await self.stream_done("response")
            self.messages.append({"role": "assistant", "content": response_text})
        except Exception as e:
            # Keep the stream target, with its partial content, on screen.
            self.stream_failed = True
            await self.stream_error("response", f"Error: {e}")
        finally:
            self.is_streaming = False
```

```html
<div id="chat">
    {% for msg in messages %}
    <div class="message {{ msg.role }}">{{ msg.content }}</div>
    {% endfor %}
    {% if is_streaming or stream_failed %}
    <div dj-stream="response" class="message assistant"></div>
    {% endif %}
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
- Use `stream_text()` for plain text (LLM tokens) and `stream_to(..., html=...)` or `StreamingMixin.stream_insert(self, ...)` when you need HTML structure (log lines, chat bubbles).
- Apply `overflow-y: auto` with a `max-height` on stream containers for auto-scroll behavior.
- Throttling happens on the server and keeps only the newest op per ~16 ms window. Send accumulated text with `mode="replace"` rather than per-token appends (see [Throttling](#throttling-keeps-only-the-newest-op)).
- Don't show the same reply through both the stream target and a normally rendered list: render the stream target only while streaming (as in the examples), or keep the reply out of the rendered list.
- Mark a `dj-stream` target `dj-update="ignore"` when the handler also re-renders it, so the main diff and the stream ops don't both write the same region (see [Template Directives](#template-directives)).
- Streaming needs an `async def` handler with explicit `stream_*` calls. Mutating a public attribute in a `@background` loop does **not** stream — the client only sees the result after the whole callback returns — and a *sync* `@background` loop cannot be interrupted mid-run (use `async def` so a sibling "Stop" event can flip a flag between `await`s).
