---
title: "Streaming Markdown"
slug: streaming-markdown
section: guides
order: 24
level: intermediate
description: "Safely render streaming LLM output as Markdown on the server with {% djust_markdown %}."
---

# Streaming Markdown (v0.7.0)

LLM chat UIs usually render Markdown character-by-character as it streams back
from the model. Doing that safely in the browser means shipping a Markdown
library plus a DOMPurify pass per token, watching the text flicker through
half-parsed states, and worrying about every jailbreak that ends with a raw
`<script>` tag.

djust does this on the server, in Rust:

```django
{% djust_markdown llm_output %}
```

No client-side Markdown library. No DOMPurify. No flicker on partial input.
Any raw HTML in the source is HTML-escaped before it reaches the browser.

## When to use it

- **Chat UIs** that stream token-by-token from an LLM.
- **AI report / summary views** where the model writes Markdown and you want
  it rendered as soon as each character arrives.
- **Any user-authored Markdown** you want to render without bolting on a JS
  library — blog bodies, comments, issue descriptions.

If you have fully-formed, trusted Markdown that never streams, you can also
call the helper directly from Python:

```python
from djust import render_markdown

html = render_markdown("# hello\n")  # returns a SafeString
```

## The template tag

```django
{% djust_markdown llm_output %}
```

The first argument is a context variable containing Markdown source. The tag
renders it to sanitised HTML. The rendered output is already safe to embed
directly — no `|safe` needed, no double-escape.

> **Requires the djust template backend.** `{% djust_markdown %}` is registered
> only with djust's Rust template engine (via `register_tag_handler`), **not**
> Django's stock template backend. A project whose `TEMPLATES` setting uses only
> `django.template.backends.django.DjangoTemplates` (e.g. one created with
> `django-admin startproject` rather than the djust scaffold) raises
> `TemplateSyntaxError: Invalid block tag 'djust_markdown'` on the initial GET —
> even with `{% load live_tags %}` at the top. Ensure `DjustTemplateBackend` is
> configured in `TEMPLATES` (the `djust`-scaffolded settings do this for you).
> See the [installation guide](/quickstart/) for the backend configuration.

### Keyword arguments

All arguments are optional booleans:

| kwarg | default | effect |
|---|---|---|
| `provisional` | `True` | Split the trailing unfinished line off as escaped text (streaming-safe). Disable when the Markdown is complete. |
| `tables` | `True` | Enable GFM tables (`\| a \| b \|`). |
| `strikethrough` | `True` | Enable `~~strikethrough~~`. |
| `task_lists` | `False` | Enable `- [ ]` / `- [x]` checkboxes. |

```django
{% djust_markdown post.body tables=False %}
{% djust_markdown llm_output provisional=False %}
```

## How streaming safety works

When the LLM has only emitted part of a line — say, `Hello **wor` — a naive
Markdown renderer will see an unclosed `**` and do one of two things:

1. Render the `**` as literal characters (the reader sees the raw syntax).
2. Emit a half-open `<strong>` tag that tears open the rest of the page.

Neither is good. `{% djust_markdown %}` splits the source at the *last
newline*. Everything before the last newline is sent to the Markdown parser
as usual; the partial trailing line is escaped and wrapped in:

```html
<p class="djust-md-provisional">Hello **wor</p>
```

When the model finishes the line and emits `\n`, the split disappears and
the text is re-rendered by the Markdown parser as `<p>Hello <strong>word</strong></p>`.

This behaviour is controlled by `provisional=True` (the default). Turn it
off when you're rendering a finalised document.

### Styling the provisional line

The wrapper class is `djust-md-provisional`. Grey it out, italicise it,
animate it — any plain CSS will do:

```css
.djust-md-provisional {
    opacity: 0.55;
    font-style: italic;
}
```

## Security guarantees

The Rust renderer is built to be **safe by construction**. These properties
are enforced in the core crate and tested (see
`crates/djust_templates/src/markdown.rs`):

- **Raw HTML is escaped.** `Options::ENABLE_HTML` is never set on the
  underlying pulldown-cmark parser. Anything that looks like a tag
  (`<script>`, `<img onerror=…>`, `<iframe>`, …) becomes escaped text.
- **`javascript:` / `vbscript:` / `data:` URLs are neutralised.** Links and
  images pointing at those schemes are rewritten to `#`.
- **Input is size-capped.** Anything larger than 10 MiB is returned as an
  escaped `<pre class="djust-md-toobig">` block without hitting the parser.
- **No panics.** pulldown-cmark's contract is panic-free on arbitrary input;
  we don't wrap it in `catch_unwind`.

The tag output is handed to Django via `SafeString`, but because we never
emit HTML we didn't produce ourselves, the "safe" marker does not bypass
any real escaping.

### Operational limits

The 10 MiB per-call input cap is a defence against an individual runaway
Markdown source — it is **not** a concurrency limiter. If many users are
streaming large replies at once you should still cap request concurrency at
the operator level (ASGI worker count, load balancer limits, or a dedicated
rate limiter on the LiveView event). Treat `{% djust_markdown %}` the way
you'd treat any per-request render — fast, but not free, and ultimately
bounded by the same infrastructure that bounds the rest of your app.

## Example: streaming LLM reply

Streaming Markdown to the client requires **explicit** `stream_*` calls per
chunk — exactly like [the streaming guide's Quick Start](streaming.md#quick-start).
Plain attribute mutation in a loop does **not** stream: background work runs
via `_run_async_work`, which awaits the whole callback and only re-renders
*after* it returns, so `self.llm_output += ch` in a loop reveals the entire
reply in a single frame at the end — the UI sits empty for the whole
generation, then flashes the full text.

Push each token to the client yourself with `await self.stream_to(...)`. The
handler is `async def` for two reasons: (1) so it can `await` the stream ops
mid-loop, and (2) so it yields control between tokens, letting a sibling
`reset`/"Stop" event dispatch on the event loop and flip `self.streaming =
False` mid-stream. A *sync* `@background` loop cannot be interrupted — it holds
a `sync_to_async` thread-pool slot and the stop handler queues behind it.

```python
from djust import LiveView, render_markdown
from djust.decorators import event_handler


REPLY = "# Hi\n\nHere is a **bold** reply.\n"

_STREAM = "reply"  # matches dj-stream="reply" in the template


class ChatView(LiveView):
    template_name = "chat.html"

    def mount(self, request, **kwargs):
        self.llm_output = ""
        self.streaming = False

    @event_handler
    def start(self, **kwargs):
        if self.streaming:
            return
        self.llm_output = ""
        self.streaming = True
        # Explicit task name so reset()'s cancel_async("reply") matches it.
        # (cancel_async against a name that was never registered is a silent
        # no-op — it neither raises nor warns.)
        self.start_async(self._stream, name=_STREAM)

    @event_handler
    def reset(self, **kwargs):
        self.cancel_async(_STREAM)   # name MUST match start_async(name=...)
        self.streaming = False       # the async loop yields, sees this, stops
        self.llm_output = ""

    async def _stream(self):
        import asyncio

        await self.stream_start(_STREAM)
        try:
            for ch in REPLY:
                if not self.streaming:
                    break
                self.llm_output += ch
                # render_markdown is the same Rust renderer {% djust_markdown %}
                # uses; provisional=True keeps the trailing partial line escaped.
                await self.stream_to(
                    _STREAM,
                    html=render_markdown(self.llm_output, provisional=True),
                )
                await asyncio.sleep(0.025)
            self.streaming = False
        finally:
            # Settle the target once (drop the provisional split); because the
            # target is dj-update="ignore" the main render never writes it, so
            # this stream op is what settles — or, after a reset, clears — it.
            await self.stream_to(
                _STREAM,
                html=render_markdown(self.llm_output, provisional=False),
            )
            await self.stream_done(_STREAM)
```

```django
{# chat.html #}
{# dj-stream marks the target the view streams into; dj-update="ignore"
   excludes it from the main dj-root VDOM diff so the stream ops and the
   event-completion render don't both write this region (see the note below). #}
<article dj-stream="reply" dj-update="ignore">
    {% djust_markdown llm_output %}
</article>

<button dj-click="start">Send</button>
<button dj-click="reset">Stop</button>
```

> **Mark a streamed target `dj-update="ignore"`.** A `dj-stream` target that
> `stream_to`/`stream_text` writes to should also carry `dj-update="ignore"`
> (or otherwise be excluded from the main diff). Otherwise the handler's
> event-completion `render_with_diff()` re-renders the same region the stream
> ops just wrote, and the client can receive the content twice through two
> code paths in close succession — visible duplicate/flicker. This is
> especially true when calling `stream_to(name, target=...)` **without**
> `html=`: that form re-renders the *entire* template and regex-extracts the
> target, so it is easy to double-write. Passing `html=` (as above) sends
> exactly one element and avoids the full-template re-render.

A live version is registered at `/demos/markdown-stream/` in the demo
project (`examples/demo_project/djust_demos/views/markdown_stream_demo.py`).

## Calling the renderer from Python

If you need the HTML outside a template — e.g. in an API response, a report
generator, or a test — import it directly:

```python
from djust import render_markdown

html = render_markdown(
    "# Title\n\nparagraph with <script>alert(1)</script>\n",
    tables=True,
    strikethrough=True,
)
# '<h1>Title</h1>\n<p>paragraph with &lt;script&gt;alert(1)&lt;/script&gt;</p>\n'
```

The return value is a `django.utils.safestring.SafeString`.

## Limitations (v0.7.0)

These are intentional — they'll be reconsidered in later releases:

- **Tag-only API.** There is no `|djust_markdown` filter form. Deferred to
  v0.7.1 if demand emerges.
- **No syntax highlighting.** Fenced code blocks render as `<pre><code>`
  with no language-class styling. Highlighting pulls in a multi-megabyte
  dependency; roll your own client-side (Prism / highlight.js) if needed.
- **No MathJax / LaTeX.**
- **No autolinks in plain text.** `https://example.com` stays as text
  unless wrapped in an explicit `[text](url)`. pulldown-cmark 0.12 does not
  expose the GFM-autolink flag we'd need; we'll revisit when the pulled
  parser version makes this cheap.
- **Reference-style links across a streaming boundary.** If the reference
  `[1]: https://…` line is still mid-stream when the body `[foo][1]`
  already arrived, you'll get a moment where the link renders unresolved.
  Cosmetic only — no correctness issue.

## Related

- [Template cheat sheet](template-cheatsheet.md) — full tag index.
- [Streaming & real-time partial updates](streaming.md) — the full
  `stream_*` API and the `dj-stream` directive the example above uses.
- [Background work & loading states](loading-states.md) — the async
  `start_async` / `@background` background-work model. Note: background work
  alone does not stream; you still push each chunk with an explicit
  `stream_*` call (see the example above).
