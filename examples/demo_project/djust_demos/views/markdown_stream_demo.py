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

Why this handler is ``async def`` (and streams explicitly):

    Plain attribute mutation in a loop does NOT stream. Background work
    runs via ``_run_async_work`` (``djust/websocket.py``), which awaits
    the callback to completion and only calls ``render_with_diff()``
    AFTER it returns — so mutating ``self.llm_output`` in a loop would
    reveal the whole reply in a single frame at the end, not per token.
    To genuinely stream, we push each token to the client explicitly with
    ``await self.stream_to(...)`` mid-loop (see the ``streaming.md`` guide).

    ``async def`` also makes the stream interruptible: the handler yields
    control at every ``await``, so a sibling ``reset`` event can dispatch
    on the event loop between tokens and flip ``self.streaming = False``,
    which the loop's guard then honours. A *sync* ``@background`` loop
    could not be interrupted — ``sync_to_async`` runs it on a thread-pool
    slot and a ``reset`` handler queues behind it (Python threads are not
    preemptible; see the note at ``djust/websocket.py`` ~L1172).
"""

import asyncio

from djust import render_markdown
from djust.decorators import event_handler
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

# Logical stream name; also the value of ``dj-stream`` on the target
# ``<article>`` in the template.
_STREAM = "md_stream"


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
        # Explicit task name so reset()'s cancel_async("md_stream") matches
        # (a name mismatch is a silent no-op — cancel_async can't find the
        # task and does nothing).
        self.start_async(self._stream_chars, name=_STREAM)

    @event_handler
    def reset(self, **kwargs):
        """Clear output and stop any in-flight stream."""
        # Suppress the cancelled task's final re-render. The name MUST match
        # the start_async(name=...) above, or this quietly no-ops.
        self.cancel_async(_STREAM)
        # Flip the cooperative-stop flag. Because _stream_chars is async and
        # yields at every await, this event dispatches between tokens and the
        # loop's `if not self.streaming` guard stops it mid-stream.
        self.streaming = False
        self.llm_output = ""

    async def _stream_chars(self):
        """Background task: append one character every ~25 ms and push it.

        Genuinely streams — each token is sent to the client immediately via
        ``stream_to`` rather than waiting for one render at the end. The
        target ``<article dj-stream="md_stream" dj-update="ignore">`` is
        excluded from the main dj-root diff so these stream ops and the
        event-completion ``render_with_diff()`` never both write the region.
        """
        await self.stream_start(_STREAM)
        try:
            for ch in _SAMPLE_REPLY:
                if not self.streaming:
                    break
                self.llm_output += ch
                # Push the freshly-rendered Markdown for the whole reply so far.
                # ``render_markdown`` is the same Rust renderer {% djust_markdown %}
                # uses; provisional=True keeps the trailing partial line escaped.
                await self.stream_to(
                    _STREAM,
                    html=render_markdown(self.llm_output, provisional=True),
                )
                await asyncio.sleep(0.025)
            self.streaming = False
        finally:
            # Settle the target: render the final output (or the reset-cleared
            # empty output) without the provisional split, then signal done.
            # Because the target is dj-update="ignore" the main render never
            # writes it — this stream op is what settles/clears the region, so
            # a mid-stream reset() actually clears the visible Markdown too.
            await self.stream_to(
                _STREAM,
                html=render_markdown(self.llm_output, provisional=False),
            )
            await self.stream_done(_STREAM)
