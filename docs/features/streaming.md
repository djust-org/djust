# Streaming

djust provides real-time partial DOM updates during async handler execution. Perfect for LLM chat (token-by-token streaming), live feeds, progress indicators, and any scenario where you need to show incremental results.

## Core Concepts

- **`stream_to()`** — Replace content in a target element
- **`stream_text()`** — Stream text incrementally (append/replace/prepend)
- **`stream_insert()`** — Add new elements to a container
- **`stream_delete()`** — Remove elements from DOM
- **`dj-stream`** — Mark elements as stream targets

## Basic Usage: LLM Chat

```python
from djust import LiveView
from djust.streaming import StreamingMixin
from djust.decorators import event_handler

class ChatView(LiveView, StreamingMixin):
    template_name = "chat.html"

    async def mount(self, request, **kwargs):
        self.messages = []

    @event_handler
    async def send_message(self, content, **kwargs):
        # Add user message
        self.messages.append({"role": "user", "content": content})
        
        # Add empty assistant message
        self.messages.append({"role": "assistant", "content": ""})
        
        # Signal stream start
        await self.stream_start("response")
        
        # Stream tokens from LLM
        async for token in llm_stream(content):
            self.messages[-1]["content"] += token
            await self.stream_text("response", token, mode="append")
        
        # Signal stream complete
        await self.stream_done("response")
```

```html
<!-- chat.html -->
<div class="chat">
    <div class="messages">
        {% for msg in messages %}
            <div class="message {{ msg.role }}">
                {% if msg.role == 'assistant' %}
                    <div dj-stream="response">{{ msg.content }}</div>
                {% else %}
                    {{ msg.content }}
                {% endif %}
            </div>
        {% endfor %}
    </div>
    
    <form dj-submit="send_message">
        <input name="content" placeholder="Type a message..." />
        <button type="submit">Send</button>
    </form>
</div>
```

## StreamingMixin API

### `stream_to(stream_name, target=None, html=None)`

Replace the innerHTML of a target element.

```python
# Replace with explicit HTML
await self.stream_to("output", html="<p>New content!</p>")

# Replace by re-rendering from current state
await self.stream_to("results", target="#search-results")
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `stream_name` | `str` | **required** | Logical stream name |
| `target` | `str` | `None` | CSS selector. Defaults to `[dj-stream='name']` |
| `html` | `str` | `None` | HTML to insert. If None, re-renders from state |

### `stream_text(stream_name, text, mode="append", target=None)`

Stream text content incrementally.

```python
# Append text (default)
await self.stream_text("output", "Hello ")
await self.stream_text("output", "World!")  # Shows "Hello World!"

# Replace all text
await self.stream_text("status", "Complete!", mode="replace")

# Prepend text
await self.stream_text("log", "[INFO] Starting...\n", mode="prepend")
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `stream_name` | `str` | **required** | Logical stream name |
| `text` | `str` | **required** | Text content to stream |
| `mode` | `str` | `"append"` | `"append"`, `"replace"`, or `"prepend"` |
| `target` | `str` | `None` | CSS selector |

### `stream_insert(stream_name, html, at="append", target=None)`

Insert HTML elements into a container.

```python
# Append new item
await self.stream_insert("feed", "<div class='item'>New post!</div>")

# Prepend (newest first)
await self.stream_insert("notifications", 
    "<li>New message</li>", 
    at="prepend"
)
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `stream_name` | `str` | **required** | Logical stream name |
| `html` | `str` | **required** | HTML fragment to insert |
| `at` | `str` | `"append"` | `"append"` or `"prepend"` |
| `target` | `str` | `None` | CSS selector |

### `stream_delete(stream_name, selector)`

Remove an element from the DOM.

```python
await self.stream_delete("messages", "#msg-42")
await self.stream_delete("items", ".completed")
```

### `stream_error(stream_name, error, target=None)`

Display an error state while preserving partial content.

```python
try:
    async for token in llm_stream(prompt):
        await self.stream_text("response", token)
except Exception as e:
    await self.stream_error("response", f"Error: {str(e)}")
```

### `stream_start(stream_name, target=None)`

Signal that a stream is starting. Sets `data-stream-active="true"` on the target.

```python
await self.stream_start("response")
# Target element now has data-stream-active="true"
# Use CSS to show loading state
```

### `stream_done(stream_name, target=None)`

Signal that a stream is complete. Removes `data-stream-active` attribute.

```python
await self.stream_done("response")
# Target element no longer has data-stream-active
```

### `push_state()`

Send a full re-render immediately during async execution.

```python
@event_handler
async def long_process(self, **kwargs):
    self.status = "Processing..."
    await self.push_state()  # Update UI now
    
    result = await expensive_operation()
    
    self.status = "Complete!"
    self.result = result
    # View re-renders automatically after handler
```

## Template Directive: `dj-stream`

Mark an element as a stream target:

```html
<div dj-stream="stream_name">Initial content</div>
```

Optional mode attribute for text streaming:

```html
<!-- Default: append -->
<div dj-stream="output">Loading...</div>

<!-- Explicit mode -->
<div dj-stream="output" dj-stream-mode="replace">Loading...</div>
<div dj-stream="log" dj-stream-mode="prepend"></div>
```

## Auto-Scrolling

Stream containers automatically scroll to show new content when the user is near the bottom (within 100px). This provides a natural chat-like experience.

```html
<div class="chat-messages" dj-stream="messages" style="overflow-y: auto; max-height: 400px;">
    <!-- Messages auto-scroll as they're added -->
</div>
```

## Rate Limiting

Streams are automatically batched to ~60fps (16ms minimum interval) to avoid overwhelming the client. Rapid updates are coalesced.

## CSS Styling

```css
/* Loading state */
[data-stream-active="true"] {
    position: relative;
}
[data-stream-active="true"]::after {
    content: '';
    display: inline-block;
    width: 8px;
    height: 8px;
    background: currentColor;
    border-radius: 50%;
    animation: pulse 1s infinite;
}

/* Error state */
[data-stream-error="true"] {
    border-left: 3px solid #dc3545;
    padding-left: 1rem;
}
.dj-stream-error {
    color: #dc3545;
    font-size: 0.875rem;
    margin-top: 0.5rem;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}
```

## Client-Side Events

Listen for stream events in JavaScript:

```javascript
// Stream update (replace/append/prepend)
element.addEventListener('stream:update', (e) => {
    console.log('Stream updated:', e.detail.op, e.detail.stream);
});

// Text streamed
element.addEventListener('stream:text', (e) => {
    console.log('Text streamed:', e.detail.text, e.detail.mode);
});

// Stream started
element.addEventListener('stream:start', (e) => {
    console.log('Stream started:', e.detail.stream);
});

// Stream completed
element.addEventListener('stream:done', (e) => {
    console.log('Stream done:', e.detail.stream);
});

// Stream error
element.addEventListener('stream:error', (e) => {
    console.log('Stream error:', e.detail.error);
});

// Element removed
element.addEventListener('stream:remove', (e) => {
    console.log('Element removed:', e.detail.stream);
});
```

## Complete Example: AI Chat with Tool Use

```python
# views.py
from djust import LiveView
from djust.streaming import StreamingMixin
from djust.decorators import event_handler
import openai

class AIChatView(LiveView, StreamingMixin):
    template_name = "ai_chat.html"

    async def mount(self, request, **kwargs):
        self.messages = []
        self.thinking = False

    @event_handler
    async def send_message(self, content, **kwargs):
        # Add user message
        self.messages.append({
            "role": "user", 
            "content": content,
            "id": f"msg-{len(self.messages)}"
        })
        
        # Add placeholder for assistant
        msg_id = f"msg-{len(self.messages)}"
        self.messages.append({
            "role": "assistant",
            "content": "",
            "id": msg_id,
            "thinking": False
        })
        
        # Update UI to show user message
        await self.push_state()
        
        # Start streaming
        await self.stream_start("response")
        
        try:
            client = openai.AsyncOpenAI()
            stream = await client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": m["role"], "content": m["content"]}
                    for m in self.messages[:-1]  # Exclude empty assistant
                ],
                stream=True
            )
            
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    self.messages[-1]["content"] += token
                    await self.stream_text("response", token)
            
            await self.stream_done("response")
            
        except Exception as e:
            await self.stream_error("response", f"Error: {str(e)}")

    @event_handler
    async def clear_chat(self, **kwargs):
        self.messages = []
```

```html
<!-- ai_chat.html -->
<div class="ai-chat">
    <div class="messages" id="messages">
        {% for msg in messages %}
            <div class="message {{ msg.role }}" id="{{ msg.id }}">
                <div class="avatar">
                    {% if msg.role == 'user' %}👤{% else %}🤖{% endif %}
                </div>
                <div class="content">
                    {% if msg.role == 'assistant' and forloop.last %}
                        <div dj-stream="response">{{ msg.content }}</div>
                    {% else %}
                        {{ msg.content }}
                    {% endif %}
                </div>
            </div>
        {% empty %}
            <div class="empty">Start a conversation!</div>
        {% endfor %}
    </div>
    
    <form dj-submit="send_message" class="input-area">
        <input name="content" 
               placeholder="Ask anything..." 
               autocomplete="off"
               dj-loading.disable />
        <button type="submit" dj-loading.disable>
            <span dj-loading.hide>Send</span>
            <span dj-loading.show style="display:none">...</span>
        </button>
    </form>
    
    <button dj-click="clear_chat" class="clear-btn">Clear Chat</button>
</div>

<style>
.ai-chat {
    max-width: 800px;
    margin: 0 auto;
}
.messages {
    height: 500px;
    overflow-y: auto;
    padding: 1rem;
    border: 1px solid #ddd;
    border-radius: 8px;
}
.message {
    display: flex;
    gap: 1rem;
    margin-bottom: 1rem;
}
.message.assistant .content {
    background: #f0f7ff;
    padding: 1rem;
    border-radius: 8px;
}
.message.user .content {
    background: #e8f5e9;
    padding: 1rem;
    border-radius: 8px;
}
.input-area {
    display: flex;
    gap: 0.5rem;
    margin-top: 1rem;
}
.input-area input {
    flex: 1;
    padding: 0.75rem;
    border: 1px solid #ddd;
    border-radius: 4px;
}
/* Streaming cursor */
[data-stream-active="true"]::after {
    content: '▋';
    animation: blink 1s infinite;
}
@keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
}
</style>
```

## API Reference

### StreamingMixin Methods

| Method | Description |
|--------|-------------|
| `stream_to(name, target, html)` | Replace target content |
| `stream_text(name, text, mode, target)` | Stream text incrementally |
| `stream_insert(name, html, at, target)` | Insert HTML element |
| `stream_delete(name, selector)` | Remove element from DOM |
| `stream_error(name, error, target)` | Show error state |
| `stream_start(name, target)` | Signal stream starting |
| `stream_done(name, target)` | Signal stream complete |
| `push_state()` | Full re-render immediately |

### Template Directives

| Directive | Description |
|-----------|-------------|
| `dj-stream="name"` | Mark element as stream target |
| `dj-stream-mode="mode"` | Default mode for text streaming |

### Stream Operations (Client)

| Operation | Description |
|-----------|-------------|
| `replace` | Replace innerHTML |
| `append` | Add to end |
| `prepend` | Add to beginning |
| `delete` | Remove element |
| `text` | Stream text content |
| `error` | Show error state |
| `start` | Set active state |
| `done` | Clear active state |
