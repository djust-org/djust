"""
Streaming Demo — Simulated LLM chat with token-by-token streaming.

Usage:
    1. Add to your urls.py:
        from examples.streaming_demo import StreamingChatView
        path('chat/', StreamingChatView.as_view(), name='streaming-chat'),

    2. Visit /chat/ in your browser.

This demonstrates:
    - stream_to() for real-time partial updates
    - stream_insert() for appending new messages
    - push_state() for immediate state updates
    - Async event handlers with streaming
"""

import asyncio
import html

from djust.live_view import LiveView
from djust.decorators import event_handler


# Simulated LLM response — yields tokens with realistic delays
SAMPLE_RESPONSE = (
    "Hello! I'm a simulated AI assistant. "
    "I'm streaming this response **token by token** to demonstrate "
    "djust's streaming capabilities. "
    "Each word appears as it's generated, just like ChatGPT or Claude. "
    "This uses `stream_to()` under the hood — no polling, no full re-renders, "
    "just surgical DOM updates over WebSocket. Pretty neat, right? 🚀"
)


async def fake_llm_stream(prompt: str):
    """Simulate an LLM streaming response, yielding one word at a time."""
    words = SAMPLE_RESPONSE.split(" ")
    for word in words:
        await asyncio.sleep(0.05)  # ~50ms per token
        yield word + " "


class StreamingChatView(LiveView):
    template = """
    <div class="chat-container" style="max-width: 600px; margin: 2rem auto; font-family: system-ui;">
        <h2>🤖 Streaming Chat Demo</h2>
        <div id="message-list" dj-stream="messages"
             style="height: 400px; overflow-y: auto; border: 1px solid #ddd;
                    border-radius: 8px; padding: 1rem; margin-bottom: 1rem;
                    background: #fafafa;">
            {% for msg in messages %}
            <div class="message" style="margin-bottom: 0.75rem; padding: 0.5rem 0.75rem;
                        border-radius: 8px;
                        {% if msg.role == 'user' %}
                        background: #007bff; color: white; margin-left: 4rem; text-align: right;
                        {% else %}
                        background: #e9ecef; margin-right: 4rem;
                        {% endif %}">
                {{ msg.content }}
            </div>
            {% endfor %}
            {% if not messages %}
            <p style="color: #999; text-align: center;">Send a message to start chatting!</p>
            {% endif %}
        </div>

        <form dj-submit="send_message" style="display: flex; gap: 0.5rem;">
            <input type="text" name="content" placeholder="Type a message..."
                   style="flex: 1; padding: 0.5rem 0.75rem; border: 1px solid #ddd;
                          border-radius: 8px; font-size: 1rem;"
                   autocomplete="off" />
            <button type="submit"
                    style="padding: 0.5rem 1.5rem; background: #007bff; color: white;
                           border: none; border-radius: 8px; cursor: pointer; font-size: 1rem;">
                Send
            </button>
        </form>

        <p style="color: #999; font-size: 0.85rem; margin-top: 0.5rem;">
            Status: {{ status }}
        </p>
    </div>
    """

    def mount(self, request, **kwargs):
        self.messages = []
        self.status = "Ready"

    @event_handler
    async def send_message(self, content="", **kwargs):
        if not content.strip():
            return

        # Add user message
        self.messages.append({"role": "user", "content": html.escape(content)})

        # Show user message immediately
        self.status = "Thinking..."
        await self.push_state()

        # Add empty assistant message
        self.messages.append({"role": "assistant", "content": ""})

        # Stream tokens
        async for token in fake_llm_stream(content):
            self.messages[-1]["content"] += html.escape(token)
            await self.stream_to("messages", target="#message-list")

        self.status = "Ready"
        # Final state pushed by normal re-render after handler returns
