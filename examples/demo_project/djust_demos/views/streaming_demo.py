"""
Streaming Demo — AI chat with token-by-token streaming.

Demonstrates:
    - stream_start() / stream_done() lifecycle signals
    - stream_text() for incremental text output
    - stream_to() for HTML fragment updates
    - stream_error() for graceful error handling
    - dj-stream / dj-stream-mode attributes
"""

import asyncio
import html as html_module

from djust import LiveView
from djust.decorators import event_handler


# Simulated responses
RESPONSES = {
    "default": (
        "Hello! I'm a simulated AI assistant. "
        "I'm streaming this response **token by token** to demonstrate "
        "djust's real-time streaming capabilities. "
        "Each word appears as it's generated, just like ChatGPT or Claude. "
        "This uses `stream_text()` under the hood — no polling, no full re-renders, "
        "just surgical DOM updates over WebSocket. Pretty neat, right? 🚀"
    ),
    "error": (
        "I'll start typing but then something will go wrong... "
        "Watch what happens when an error occurs mid-stream — "
        "partial content is preserved and the error is shown gracefully."
    ),
}


async def fake_llm_stream(prompt: str, fail_after: int = 0):
    """Simulate an LLM streaming response, yielding one word at a time."""
    is_error_demo = "error" in prompt.lower()
    text = RESPONSES["error"] if is_error_demo else RESPONSES["default"]
    words = text.split(" ")

    for i, word in enumerate(words):
        if is_error_demo and fail_after and i >= fail_after:
            raise RuntimeError("Simulated LLM provider error")
        await asyncio.sleep(0.05)
        yield word + " "


class StreamingDemoView(LiveView):
    template_name = "demos/streaming_demo.html"

    def mount(self, request, **kwargs):
        self.messages = []
        self.status = "Ready"
        self.is_streaming = False

    @event_handler
    async def send_message(self, content="", **kwargs):
        if not content.strip() or self.is_streaming:
            return

        user_content = html_module.escape(content.strip())
        self.messages.append({"role": "user", "content": user_content})
        self.messages.append({"role": "assistant", "content": ""})
        self.status = "Generating..."
        self.is_streaming = True
        await self.push_state()

        try:
            await self.stream_start("response")

            fail_after = 15 if "error" in content.lower() else 0
            async for token in fake_llm_stream(content, fail_after=fail_after):
                escaped = html_module.escape(token)
                self.messages[-1]["content"] += escaped
                await self.stream_text("response", escaped)

            await self.stream_done("response")
            self.status = "Ready"

        except Exception as e:
            await self.stream_error("response", str(e))
            self.status = f"Error: {e}"

        self.is_streaming = False

    @event_handler
    async def clear_chat(self, **kwargs):
        self.messages = []
        self.status = "Ready"
        self.is_streaming = False
