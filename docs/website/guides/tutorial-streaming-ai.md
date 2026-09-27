---
title: "Tutorial: Stream an AI response token-by-token"
slug: tutorial-streaming-ai
section: guides
order: 65
level: intermediate
description: "Build a chat-style AI page that streams an LLM response into the DOM as the model generates it — using start_async to run the network call in the background, stream_to to push each chunk, and render_markdown to render in-flight markdown safely. Plus a Stop button that actually cancels the upstream request."
---

# Tutorial: Stream an AI response token-by-token

Most AI chat UIs do the same three things: take a prompt, stream
the model's response into the DOM as it's generated, and let the
user hit Stop mid-response. Doing this well usually means a
WebSocket between the browser and your backend, a streaming HTTP
client between your backend and the LLM provider, an
incremental Markdown renderer that handles unfinished tokens, and
a cancellation channel that ties the user's Stop click back to
the upstream request.

djust gives you the WebSocket, the streaming-safe Markdown render,
and the cancellation primitive — `start_async` for the background
network call, `stream_to` plus `render_markdown` (the renderer behind
`{% djust_markdown %}`) for in-flight rendering, and `cancel_async`
for Stop. The rest of the tutorial is ~80 lines of glue.

By the end you'll have a chat page that:

- Takes a prompt in a textarea, submits it, and **starts streaming
  the response immediately** — no full-render wait.
- Renders the partial response as **safely-rendered Markdown** —
  half-typed code fences and `<script>` injections are escaped.
- Shows a **Stop** button that actually aborts the upstream HTTP
  request (not just hides the spinner).
- Displays a clear **error** state if the API call fails, with a
  **Retry** that reuses the original prompt.

| You'll learn | Documented in |
|---|---|
| `start_async` for background work | [Loading States & Background Work](loading-states.md) |
| Pushing chunks with `stream_to` | [Streaming](streaming.md) |
| `render_markdown` / `{% djust_markdown %}` for streaming-safe rendering | [Streaming Markdown](streaming-markdown.md) |
| `cancel_async` for user-initiated cancellation | This tutorial |
| An async stream loop for true upstream cancel | This tutorial |

> **Prerequisites:** [Installation](../getting-started/installation.md), the [search-as-you-type
> tutorial](tutorial-search-as-you-type.md) (recommended — sets
> up the loading-state vocabulary), and an API key for any
> OpenAI-compatible streaming endpoint. The example uses OpenAI's
> SDK but any provider with an iterator-style streaming response
> works the same.

---

## What you're building

```
You: Explain Phoenix LiveView in 3 sentences.

AI: Phoenix LiveView is a server-driven UI library for the
    Elixir Phoenix framework. It keeps state on the server and
    pushes minimal HTML diffs to the client over a WebSocket so
    interactive features can be written without React or any
    JavaScript framework.█

    [Stop ⏵]
```

Each character of the AI's reply appears as the model emits it. The
`█` cursor blinks at the tail. The "Stop" button stops both the
visual stream AND the upstream API call (so you don't pay for
tokens you'll never display).

---

## Step 1 — The view: state + the prompt handler

```python
# myapp/views.py
from djust import LiveView, action, state

_STREAM = "reply"  # matches dj-stream="reply" in the template


class ChatView(LiveView):
    template_name = "chat.html"

    prompt = state("")
    response = state("")
    streaming = state(False)
    error = state("")

    @action
    def submit(self, prompt: str = "", **kwargs):
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Type a prompt first.")
        self.prompt = prompt
        self.response = ""
        self.error = ""
        self.streaming = True
        self.start_async(self._stream, prompt, name=_STREAM)

    @action
    def stop(self, **kwargs):
        self.cancel_async(_STREAM)  # name must match start_async(name=...)
        self.streaming = False      # the stream loop sees this and stops

    @action
    def retry(self, **kwargs):
        # Re-fire submit with the prompt that's already in state.
        self.submit(prompt=self.prompt)
```

Three things to call out:

1. **`start_async()` / `cancel_async()`** come with every `LiveView`
   (the base class includes `AsyncWorkMixin`, so don't list it again:
   `class ChatView(AsyncWorkMixin, LiveView)` fails with an MRO
   error). Pass the same `name` to both; `cancel_async` with a name
   that matches no task is a silent no-op.
2. **`self.streaming`** doubles as the stop flag. Because the
   callback in Step 2 is `async def`, it yields to the event loop
   between chunks, so a Stop click is dispatched mid-stream and the
   loop sees `streaming` flip to `False`. A synchronous callback
   would hold a worker thread and the Stop event would queue behind
   it.
3. **`@action`** wraps `submit` so the template can read
   `submit.error` (e.g. for the empty-prompt case) without
   per-handler error wiring.

---

## Step 2 — The streaming callback (background thread)

<!-- The openai package is a third-party dependency the doc checker
     does not install. -->
<!-- doc-snippet-check: skip -->
```python
from openai import AsyncOpenAI

from djust import render_markdown

client = AsyncOpenAI()  # picks up OPENAI_API_KEY


class ChatView(LiveView):
    # ... as above ...

    async def _stream(self, prompt: str):
        """Runs on the event loop. start_async awaits a coroutine function."""
        await self.stream_start(_STREAM)
        stream = None
        try:
            stream = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )
            async for chunk in stream:
                if not self.streaming:
                    break  # Stop was clicked
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    self.response += delta
                    await self.stream_to(
                        _STREAM,
                        html=render_markdown(self.response, provisional=True),
                    )
        except Exception as exc:
            self.error = str(exc)
        finally:
            if stream is not None:
                await stream.close()  # closes the upstream HTTP connection
            self.streaming = False
            # Settle the target once, without the provisional split.
            await self.stream_to(
                _STREAM,
                html=render_markdown(self.response, provisional=False),
            )
            await self.stream_done(_STREAM)
```

What happens at runtime:

- The handler returns immediately after `start_async`. The browser
  sees `streaming = True` in the first patch.
- The callback starts iterating the OpenAI stream. Each `delta` is
  appended to `self.response` and pushed with `stream_to`. Plain
  assignment alone would not stream: background work re-renders only
  after the callback returns, so the whole reply would appear in one
  frame at the end. `stream_to` batches rapid updates to about 60 per
  second.
- `render_markdown(..., provisional=True)` is the same Rust renderer
  `{% djust_markdown %}` uses. It keeps the unfinished trailing line
  escaped, so a half-typed code fence or `<script>` never renders as
  markup.
- If `streaming` is set to `False` (from the user clicking Stop), the
  loop breaks and `stream.close()` aborts the upstream HTTPS
  connection cleanly — no more billable tokens are generated.
- On any exception, `self.error` is set and the cursor clears.

---

## Step 3 — The template

```html
<!-- myapp/templates/chat.html -->
{% load live_tags %}

<div dj-root>
<form dj-submit="submit" class="chat">
  <label>
    Prompt
    <textarea name="prompt" rows="3" required dj-form-pending="disabled">{{ prompt }}</textarea>
  </label>

  {% if not streaming %}
    <button type="submit" dj-form-pending="disabled">
      <span dj-form-pending="hide">Send</span>
      <span dj-form-pending="show" hidden>Sending&hellip;</span>
    </button>
  {% else %}
    <button type="button" dj-click="stop">Stop&nbsp;&#x23F9;</button>
  {% endif %}

  {% if submit.error %}
    <p role="alert" class="err">{{ submit.error }}</p>
  {% endif %}
</form>

<article class="response prose" dj-stream="reply" dj-update="ignore">
  {% djust_markdown response %}
</article>
{% if streaming %}<span class="cursor" aria-hidden="true">█</span>{% endif %}

{% if error %}
  <section class="failure" role="alert">
    <p>The model returned an error: <strong>{{ error }}</strong></p>
    <button type="button" dj-click="retry">Retry</button>
  </section>
{% endif %}
</div>
```

`{% djust_markdown %}` needs djust's template backend
(`DjustTemplateBackend`), which the `djust new` scaffold configures.

The non-obvious pieces:

| Piece | Why |
|---|---|
| `dj-stream="reply"` | Marks the element `stream_to("reply", ...)` writes into. |
| `dj-update="ignore"` | Keeps the streamed region out of the main VDOM diff, so the stream ops and the render after each event don't both write it (visible duplicates or flicker). See [Streaming Markdown](streaming-markdown.md). |
| `{% djust_markdown response %}` | Renders `response` on the first page load. After that the stream ops own the region. Has built-in handling for partial / mid-stream Markdown (unterminated `**bold`, half-typed code fences) — see [Streaming Markdown](streaming-markdown.md) for the safety guarantees. No client JS, no DOMPurify pass. |
| `<span class="cursor">█</span>` | A blinking text cursor while the stream is in flight. Pure CSS animation. It sits outside the ignored region so the normal re-render can add and remove it. |

---

## Step 4 — Cursor animation

```css
.cursor {
  display: inline-block;
  margin-left: 2px;
  animation: cursor-blink 1s steps(2) infinite;
}
@keyframes cursor-blink {
  to { opacity: 0; }
}
```

Two-step animation gives the snappy "off / on" blink rather than a
slow pulse. Cosmetic — drop it if you find it distracting.

---

## What just happened, end to end

```
   Browser                Server (WebSocket thread)        Background thread        OpenAI
      │                          │                                 │                  │
      │ submit("Explain LV…")   │                                 │                  │
      │ ───────────────────────► │                                 │                  │
      │                          │ self.streaming = True           │                  │
      │                          │ self.response = ""              │                  │
      │                          │ self.start_async(_stream)       │                  │
      │ ◄ patch (Stop button,    │                                 │                  │
      │   cursor shown) ─────────│                                 │                  │
      │                          │                                 │                  │
      │                          │                                 │  POST /chat/...  │
      │                          │                                 │  stream=True     │
      │                          │                                 │ ───────────────► │
      │                          │ stream_to("reply", …)        ◄──│ chunk 1: "Phoenix"│
      │ ◄ stream op (1 chunk) ───│                                 │                  │
      │                          │ stream_to("reply", …)        ◄──│ chunk 2: ...     │
      │ ◄ stream op (1 chunk) ───│                                 │                  │
      │                          │  ... continues ~50 chunks ...   │                  │
      │                          │                                 │                  │
      │ click Stop               │                                 │                  │
      │ ───────────────────────► │  cancel_async("reply")          │                  │
      │                          │  self.streaming = False         │                  │
      │                          │                                 │ stream.close() ─►│ TCP RST
      │                          │ self.streaming = False          │                  │
      │ ◄ patch (cursor gone) ───│                                 │                  │
```

`stream_to` batches rapid updates to about 60 frames a second, so a
chatty model does not saturate the WebSocket.

---

## Where to go next

- **Multi-turn chat:** keep a `messages = state(default_factory=list)`
  history, append each user prompt + assistant response. Pass the
  full history to `client.chat.completions.create(messages=...)` so
  the model has context.
- **Tools / function calls:** when the model emits a tool call,
  pause streaming, run the tool server-side via another
  `start_async`, and resume the conversation. The same `_stream`
  pattern works recursively.
- **Throttle further:** `stream_to` already caps updates at about
  60 a second. To send fewer, buffer deltas in a local string and
  call `stream_to` every 10–50 ms.
- **Server-side caching:** wrap the prompt → response in
  `functools.lru_cache` keyed on the prompt string for demos /
  reproducible examples. Disable for real chat — caching trims
  variability the model intends.
- **Per-user cost control:** check `self.request.user.tokens_used`
  before calling `start_async` and refuse over-quota requests with
  a typed error in `self.error`.

The five-primitive recipe (`state`, `start_async`, `stream_to`,
`cancel_async`, `render_markdown`) is the same
shape every "long-running server-pushed UI" feature uses —
streaming AI completions, live transcription, slow imports with
progress, search-result re-ranking, etc. Once it clicks, dragging
in another LLM provider or replacing the model with a local
embedding pass is a few lines of `_stream` body.
