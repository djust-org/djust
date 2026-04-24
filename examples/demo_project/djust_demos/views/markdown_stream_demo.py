"""
Streaming Markdown demo — simulates an LLM token-by-token reply and
renders it safely via ``{% djust_markdown %}``.

Showcases the v0.7.0 streaming-Markdown feature:

- Safe rendering: any ``<script>`` / raw HTML in the "LLM output" is
  escaped by the Rust parser, not rendered.
- Provisional-line handling: while the last line is partially typed
  (``**bol`` before it closes) it is shown as escaped plain text
  instead of flashing as malformed HTML.
- No client-side Markdown library needed.
"""

from djust.decorators import background, event_handler
from djust_shared.views import BaseViewWithNavbar


# A fixed sample "reply". Rendered one character at a time to simulate a
# streaming LLM response. Includes bold, headings, a table, a code fence,
# and an embedded ``<script>`` payload to demonstrate XSS-safe output.
_SAMPLE_REPLY = """# Streaming Markdown demo

This is a **streaming** reply, rendered one token at a time.

djust renders incoming Markdown *safely* on the server — even hostile
payloads like `<script>alert('xss')</script>` are HTML-escaped, never
executed.

## Features

- Tables: | col | val |
  | --- | --- |
  | a | 1 |
  | b | 2 |
- Code: `rustc --version`
- Fenced blocks:

```
fn main() {
    println!("hello");
}
```

Done.
"""


class MarkdownStreamDemoView(BaseViewWithNavbar):
    """LiveView that streams a fixed Markdown reply character-by-character."""

    template_name = "demos/markdown_stream_demo.html"

    def mount(self, request, **kwargs):
        # Public — auto-exposed to the template.
        self.llm_output = ""
        self.streaming = False

    @event_handler
    def start_stream(self, **kwargs):
        """Reset and begin a fresh "LLM" stream."""
        if self.streaming:
            return
        self.llm_output = ""
        self.streaming = True
        self._stream_chars()

    @event_handler
    def reset(self, **kwargs):
        """Clear output."""
        self.cancel_async("md_stream")
        self.streaming = False
        self.llm_output = ""

    @background
    def _stream_chars(self):
        """Background task: append one character every ~25 ms until done."""
        import time

        for ch in _SAMPLE_REPLY:
            if not self.streaming:
                return
            self.llm_output += ch
            # Short sleep — the re-render after each assignment pushes a
            # VDOM patch to the client.
            time.sleep(0.025)
        self.streaming = False
