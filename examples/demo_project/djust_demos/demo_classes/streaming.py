"""
Streaming Demo — Token-by-token AI chat with streaming support.

Demonstrates:
    - stream_start() / stream_done() lifecycle
    - stream_text() for token-by-token output
    - stream_error() for graceful error display
    - dj-stream attribute for targeting
    - dj-stream-mode for append/replace modes
"""


class StreamingDemo:
    """
    Self-contained streaming demo data and code examples.
    """

    PYTHON_CODE = '''class StreamingChatView(LiveView):
    def mount(self, request, **kwargs):
        self.messages = []
        self.status = "Ready"

    @event_handler
    async def send_message(self, content="", **kwargs):
        self.messages.append({"role": "user", "content": content})
        self.messages.append({"role": "assistant", "content": ""})
        await self.push_state()

        await self.stream_start("response")
        async for token in llm_stream(content):
            self.messages[-1]["content"] += token
            await self.stream_text("response", token)
        await self.stream_done("response")'''

    HTML_CODE = '''<div dj-stream="response" dj-stream-mode="append">
    <!-- Tokens appear here in real-time -->
</div>

<form dj-submit="send_message">
    <input type="text" name="content" />
    <button type="submit">Send</button>
</form>'''

    DESCRIPTION = (
        "Real-time streaming updates over WebSocket. "
        "Tokens appear one by one — no polling, no full re-renders. "
        "Supports append, replace, and prepend modes, plus error handling."
    )
