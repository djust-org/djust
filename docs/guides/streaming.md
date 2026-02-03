# Streaming

djust supports real-time streaming for token-by-token updates, perfect for LLM chat responses, live logs, and progress indicators.

## Quick Start

```python
from djust import LiveView, event_handler

class ChatView(LiveView):
    template_name = "chat.html"

    def mount(self, request, **kwargs):
        self.messages = []

    @event_handler
    async def send_message(self, content, **kwargs):
        # Add user message
        self.messages.append({"role": "user", "content": content})
        
        # Add empty assistant message to stream into
        self.messages.append({"role": "assistant", "content": ""})
        
        # Stream tokens from LLM
        async for token in self.llm_stream(content):
            self.messages[-1]["content"] += token
            await self.stream_to("messages")

    async def llm_stream(self, prompt):
        """Simulate LLM streaming."""
        import asyncio
        response = "Hello! I'm happy to help you with that."
        for word in response.split():
            yield word + " "
            await asyncio.sleep(0.1)
```

```html
<!-- chat.html -->
<div class="chat">
    <div id="messages" dj-stream="messages">
        {% for msg in messages %}
        <div class="message {{ msg.role }}">
            {{ msg.content }}
        </div>
        {% endfor %}
    </div>

    <form dj-submit="send_message">
        <input name="content" placeholder="Type a message...">
        <button type="submit">Send</button>
    </form>
</div>
```

## StreamingMixin

Add `StreamingMixin` to your view (included in LiveView by default):

```python
from djust import LiveView
from djust.streaming import StreamingMixin

class MyView(LiveView, StreamingMixin):
    pass
```

## Streaming Methods

### stream_to — Replace Target Content

Replace a target element's content with re-rendered HTML:

```python
@event_handler
async def generate_content(self):
    self.output = ""
    
    async for chunk in generate_text():
        self.output += chunk
        await self.stream_to("output")  # Updates [dj-stream="output"]
```

```html
<div dj-stream="output">{{ output }}</div>
```

#### Options

```python
# Target by stream name (default: [dj-stream="name"])
await self.stream_to("output")

# Target by CSS selector
await self.stream_to("output", target="#custom-id")

# Send pre-rendered HTML directly (skips re-rendering)
await self.stream_to("output", html="<p>Custom HTML</p>")
```

### stream_text — Append/Replace Text

Stream raw text content:

```python
@event_handler
async def stream_log(self):
    async for line in read_log_lines():
        await self.stream_text("log", line, mode="append")
```

```html
<pre dj-stream="log"></pre>
```

#### Modes

```python
# Append text (default for LLM-style streaming)
await self.stream_text("output", token, mode="append")

# Prepend text
await self.stream_text("log", new_line, mode="prepend")

# Replace all text
await self.stream_text("status", "Complete!", mode="replace")
```

### stream_insert — Insert HTML

Insert HTML at the start or end of a container:

```python
@event_handler
async def load_more(self):
    items = await self.fetch_items()
    for item in items:
        html = f'<div class="item">{item.name}</div>'
        await self.stream_insert("items", html, at="append")
```

```html
<div dj-stream="items">
    {% for item in items %}
    <div class="item">{{ item.name }}</div>
    {% endfor %}
</div>
```

### stream_delete — Remove Elements

Remove an element by selector:

```python
@event_handler
async def delete_item(self, item_id, **kwargs):
    await Item.objects.filter(id=item_id).adelete()
    await self.stream_delete("items", f"#item-{item_id}")
```

### stream_error — Show Error State

Display an error while preserving existing content:

```python
@event_handler
async def generate(self):
    try:
        async for token in llm_stream():
            await self.stream_text("output", token, mode="append")
    except Exception as e:
        await self.stream_error("output", str(e))
```

### stream_start / stream_done — Signal Lifecycle

Signal the start and end of a stream:

```python
@event_handler
async def generate(self):
    await self.stream_start("output")
    
    async for token in llm_stream():
        await self.stream_text("output", token, mode="append")
    
    await self.stream_done("output")
```

```html
<div dj-stream="output" 
     class="stream-container"
     data-loading-class="is-streaming">
    {{ output }}
</div>

<style>
.stream-container.is-streaming::after {
    content: "▋";
    animation: blink 1s infinite;
}
</style>
```

### push_state — Full Re-render

For long-running handlers, push intermediate state:

```python
@event_handler
async def process_files(self):
    self.status = "Analyzing..."
    await self.push_state()
    
    await self.analyze()
    
    self.status = "Processing..."
    await self.push_state()
    
    await self.process()
    
    self.status = "Complete!"
    # Final state sent automatically when handler returns
```

## Template: dj-stream Attribute

Mark streaming targets with `dj-stream`:

```html
<!-- Stream target by name -->
<div dj-stream="response">{{ response }}</div>

<!-- Multiple streams on one page -->
<div dj-stream="chat-messages">...</div>
<div dj-stream="typing-indicator">...</div>
<div dj-stream="status">...</div>
```

## Rate Limiting

Streaming is automatically batched at ~60fps to avoid overwhelming the client. You don't need to manually throttle.

## Full Example: AI Chat with Streaming

```python
from djust import LiveView, event_handler
import openai

class AIChatView(LiveView):
    template_name = "ai_chat.html"

    def mount(self, request, **kwargs):
        self.messages = []
        self.is_generating = False
        self.error = None

    @event_handler
    async def send_message(self, content, **kwargs):
        if self.is_generating:
            return
        
        self.is_generating = True
        self.error = None
        
        # Add user message
        self.messages.append({"role": "user", "content": content})
        
        # Add empty assistant message
        self.messages.append({"role": "assistant", "content": ""})
        
        await self.stream_start("messages")
        
        try:
            # Stream from OpenAI
            stream = await openai.ChatCompletion.acreate(
                model="gpt-4",
                messages=[
                    {"role": m["role"], "content": m["content"]}
                    for m in self.messages[:-1]  # Exclude empty assistant msg
                ],
                stream=True,
            )
            
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    self.messages[-1]["content"] += token
                    await self.stream_to("messages")
        
        except Exception as e:
            self.error = str(e)
            await self.stream_error("messages", f"Error: {e}")
        
        finally:
            self.is_generating = False
            await self.stream_done("messages")
```

```html
<!-- ai_chat.html -->
<div class="chat-container">
    <div id="messages" dj-stream="messages" class="messages-list">
        {% for msg in messages %}
        <div class="message {{ msg.role }}">
            <div class="avatar">
                {% if msg.role == "user" %}👤{% else %}🤖{% endif %}
            </div>
            <div class="content">{{ msg.content }}</div>
        </div>
        {% endfor %}
        
        {% if is_generating %}
        <div class="typing-indicator">
            <span></span><span></span><span></span>
        </div>
        {% endif %}
    </div>

    <form dj-submit="send_message" class="message-form">
        <input name="content" 
               placeholder="Ask me anything..."
               {% if is_generating %}disabled{% endif %}>
        <button type="submit" {% if is_generating %}disabled{% endif %}>
            {% if is_generating %}...{% else %}Send{% endif %}
        </button>
    </form>
</div>

<style>
.messages-list {
    max-height: 500px;
    overflow-y: auto;
}
.message {
    display: flex;
    gap: 12px;
    padding: 16px;
}
.message.assistant {
    background: #f5f5f5;
}
.typing-indicator span {
    display: inline-block;
    width: 8px;
    height: 8px;
    background: #888;
    border-radius: 50%;
    animation: bounce 1.4s infinite;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
    0%, 80%, 100% { transform: translateY(0); }
    40% { transform: translateY(-8px); }
}
</style>
```

## Tips

1. **Use async handlers**: Streaming requires `async def` handlers.

2. **Batch rapid updates**: The ~60fps rate limiting is automatic, but for very rapid streams (e.g., byte-by-byte), consider buffering.

3. **Show loading state**: Use `stream_start/stream_done` to show visual feedback.

4. **Handle errors gracefully**: Wrap streaming in try/except and use `stream_error`.

5. **Auto-scroll**: Add client-side JS to auto-scroll chat containers:
   ```javascript
   window.djust.hooks = {
       ChatMessages: {
           updated() {
               this.el.scrollTop = this.el.scrollHeight;
           }
       }
   };
   ```
