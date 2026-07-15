---
title: "Bug Capture — Share a Broken Transition"
slug: bug-capture
section: guides
order: 33
level: intermediate
description: "Encode state_before + state_after + vdom_patches into a URL fragment a teammate can paste back to reproduce a broken djust transition without your codebase"
---

# Bug Capture — Share a Broken Transition

`djust.bug_capture` lets a developer encode the minimum information needed to reproduce a broken event transition — `state_before`, `state_after`, and the `vdom_patches` djust generated — into a single URL-safe string. A teammate (or a maintainer) decodes the string and sees exactly what the framework did with what state. No need to clone your repo; no template files to ship.

> **v1.1 status.** Iter A (this page) ships the data shape, encoder/decoder, and PII-scrub hook. Iter B ([#1562](https://github.com/djust-org/djust/issues/1562), shipped below) adds the read-only replay viewer at `/__djust__/replay/<blob>` and the debug panel's Share button. Iter C ([#1561](https://github.com/djust-org/djust/issues/1561)) will add a Redis store for large payloads, a `djust replay` CLI, and a framework-level `time_travel_excluded_fields` class attribute.

## When to use this

The use case that motivated this feature: a downstream consumer files a bug like ["VDOM diff appends new subtree instead of replacing when {% if %}/{% elif %} swaps between {% include %}d templates"](https://github.com/djust-org/djust/issues/1552). The reproducer is locked behind hundreds of private template/view/model files. Without bug-capture the maintainer's options are:

1. Ask for a sanitized minimal-repro (slow — hours to days for a complex template tree).
2. Guess from the description (risky — the previous fix attempt got the root cause wrong precisely because it tested a synthetic shape, not the real one).
3. Get screen-sharing access (expensive).

With bug-capture the reporter shares one URL fragment. The maintainer pastes the blob into the [replay viewer](#browser-based-replay-iter-b) or a local REPL and sees the real state + patches.

## Quick start

In the reporter's local dev REPL — at the moment the broken transition happens:

```python
from djust.bug_capture import encode_view_state, scrub_fields

# `my_view` is the LiveView instance with time_travel_enabled = True
# that just emitted the broken transition. Patches come straight from
# render_with_diff() — iter A intentionally does not couple to the
# render pipeline (iter B's debug-panel button will wire this up).
_html, patches, _version = my_view.render_with_diff()
blob = encode_view_state(
    my_view,
    patches=patches,
    scrub=scrub_fields("password", "ssn", "credit_card"),
)
# blob is now a string like:
#   "djbug1.eyJ2IjoiZGpidWcxIiwic3RhdGVfYmVmb3JlIjp7Li4ufX0..."
# Share this string with a teammate.
```

The maintainer pastes it back:

```python
from djust.bug_capture import BugCapture

capture = BugCapture.decode(blob)
print(capture.event_name)        # "next_step"
print(capture.state_before)      # {"step": "claimant", "filing_for": "self", ...}
print(capture.state_after)       # {"step": "vehicle", "filing_for": "self", ...}
print(capture.vdom_patches)      # [{"op": "insert", "path": [0, 2], "html": "..."}, ...]
print(capture.scrubbed_fields)   # ["password", "ssn"]  — names only, never values
```

Or open the blob directly in a browser — see [Browser-based replay](#browser-based-replay-iter-b) below.

## Security model

**Read this before sharing any encoded blob.** `bug_capture` is a power tool — used carelessly it leaks user PII.

### Captured state may contain user PII

`state_before` and `state_after` are the view's *public* state at the moment of an event. That includes anything the developer assigned to public attributes: form values, model field contents, user IDs, search queries, multi-tenant context. The encoded blob is the same data, URL-safely transcoded. Treat the URL fragment as sensitive data. Don't paste it into shared bug trackers, Slack channels, or email without reviewing what's inside.

### Always use the `scrub` hook for known-sensitive fields

```python
# Built-in helper — removes named fields from state_before AND state_after
blob = capture.encode(scrub=scrub_fields("password", "ssn", "credit_card"))

# Or supply your own callable for arbitrary redaction policies:
def redact_emails(cap: BugCapture) -> BugCapture:
    def mask(d):
        return {k: ("<redacted>" if "@" in str(v) else v) for k, v in d.items()}
    return BugCapture(
        state_before=mask(cap.state_before),
        state_after=mask(cap.state_after),
        vdom_patches=cap.vdom_patches,
        event_name=cap.event_name,
        scrubbed_fields=cap.scrubbed_fields + ["<email-pattern>"],
    )

blob = capture.encode(scrub=redact_emails)
```

The names of scrubbed fields are recorded on the wire (`scrubbed_fields`) so a reviewer reading the decoded capture knows what was held back. **Values are not.** Always scrub at the encoding boundary, never trust the recipient to scrub on receive.

### The encoded blob is NOT authenticated

Anyone can hand-craft a syntactically-valid `djbug1.<base64>` payload. Consumers that decode a `BugCapture` and render it MUST treat the resulting state as untrusted input:

- Escape on render (don't innerHTML a captured string).
- Don't dispatch handlers against captured state.
- Don't let a captured tenant context cross your multi-tenant boundary.

Iter B's replay viewer is purely read-only for this reason. If you write your own consumer of `BugCapture.decode()`, apply the same defenses.

### Default-off in production

`BugCapture.encode()` and `encode_view_state()` raise `RuntimeError` when `settings.DEBUG` is falsy. To opt in for production:

```python
# settings.py — deliberate, ugly opt-in
DJUST_BUG_CAPTURE_PROD_OPT_IN = True
```

The opt-in must be the literal Python value `True` — truthy-but-not-`True` (e.g. the string `"yes"`) is rejected. This is defensive against accidental-enable via config-loader workarounds. Decoding works regardless of `DEBUG` (a maintainer can paste a capture URL into any REPL and inspect it).

### Wire format is JSON, never pickle

Encoded blobs are URL-safe base64 of compact JSON. The decoder validates types and rejects malformed input with a clear `ValueError`. A regression test pins `not raw.startswith(b"\x80")` so a future maintainer reaching for pickle for "efficiency" trips an immediate test failure.

## API reference

### `BugCapture` (dataclass)

```python
@dataclass
class BugCapture:
    state_before: dict
    state_after: dict
    vdom_patches: list[dict]
    event_name: str = ""
    scrubbed_fields: list[str] = []
```

- `state_before` / `state_after`: the view's public state, JSON-safe.
- `vdom_patches`: list of patches as JSON-decoded dicts (already parsed from the `render_with_diff()` wire-format string).
- `event_name`: the handler that produced this transition (optional but recommended for context).
- `scrubbed_fields`: names of fields a `scrub` callable removed during encoding. Names only, never values.

### `BugCapture.encode(scrub=None) -> str`

Encode into a `djbug1.<base64url>` string. See the security model above.

### `BugCapture.decode(blob: str) -> BugCapture`

Decode a `djbug1.<base64url>` string. Raises `ValueError` on any malformed input (non-string, missing version prefix, unknown version, bad base64, bad JSON, missing required fields, wrong field types).

### `encode_view_state(view, patches, event_name="", scrub=None) -> str`

Convenience: pulls the most recent `EventSnapshot` from a view's time-travel buffer + the caller-supplied `patches`, builds a `BugCapture`, encodes it. Requires the view to have `time_travel_enabled = True` and at least one event captured.

`patches` is required and must be either the JSON string `render_with_diff()` returns or an already-decoded list of patch dicts. **Why caller-supplied:** iter A intentionally does not couple to the render pipeline — djust's `render_with_diff()` returns patches into the WebSocket / SSE / runtime frame paths without stashing them on the view, so there's no framework attribute to introspect. Iter B's debug-panel Share button (below) calls `render_with_diff()` + this function in one click.

Pass `event_name=...` to pick a specific past event rather than the latest.

### `scrub_fields(*names) -> Callable[[BugCapture], BugCapture]`

Ready-made scrub callable. Removes each named field from both `state_before` and `state_after`. Absent fields are silently ignored. Field names removed (but values held back) are appended to `scrubbed_fields` for wire-visible transparency.

## Browser-based replay (iter B)

`GET /__djust__/replay/<blob>` decodes a `djbug1.<base64>` blob and renders it as a page — no REPL required. Paste the blob a teammate sent you (or one your own debug panel produced, see below) straight into the URL bar:

```
http://localhost:8000/__djust__/replay/djbug1.eyJ2IjoiZGpidWcxIiwic3RhdGVfYmVmb3JlIjp7Li4ufX0
```

The page shows:

- **`event_name`** and **`scrubbed_fields`** headers.
- **`state_before` / `state_after`** side-by-side, plus a per-key diff table (`added` / `removed` / `changed` / `same`) so you don't have to eyeball two JSON blobs.
- **`vdom_patches`**, one block per patch (`op` + `path` + payload), rendered as escaped text inside `<pre><code>` — captured HTML is untrusted, so it is never parsed as markup in the page itself.
- A best-effort **captured-HTML preview** inside a fully `sandbox`-ed `<iframe srcdoc>` (no `allow-scripts`, no `allow-same-origin`) when any patch carries an `html` payload — a rough visual sanity-check that can't execute script or touch the parent page.
- A **"Copy as `djbug1.` URL"** button to re-share the exact blob you're looking at.

**Routing.** The route lives in `djust.urls` — include it from your project's URLconf:

```python
# urls.py
urlpatterns = [
    path("", include("djust.urls")),
    # ... your routes
]
```

**DEBUG-gated at two layers.** `djust.urls.urlpatterns` omits the route entirely when `DEBUG=False` (so a stray `include("djust.urls")` costs nothing in production), and the view itself re-checks the identical gate as defense in depth. Both read the same `DJUST_BUG_CAPTURE_PROD_OPT_IN` opt-in `bug_capture._enforce_prod_gate()` uses for encoding — "opted into bug-capture in prod" is one decision, not two:

| `DEBUG` | `DJUST_BUG_CAPTURE_PROD_OPT_IN` | Result |
|---|---|---|
| `True` | any | 200 for a valid blob |
| `False` | not set / not literal `True` | 404 |
| `False` | `True` | 200 for a valid blob |

A malformed blob returns `400` (as `text/plain`, never `text/html`, so a crafted blob can't get its error message parsed as markup by the browser).

**Strictly read-only.** The replay viewer is a plain Django view, not a `LiveView` — a deliberate choice: a `LiveView` mounts over a WebSocket and accepts `event` frames by design, and giving this route that machinery would mean actively disarming a dispatch surface a plain HTTP view never has in the first place. Nothing on the page can:

- Dispatch an event handler (`event_name` is display-only — never resolved to a callable).
- Mutate any application state (no writes anywhere in the view).
- Scope a database query by the captured `tenant_id` (the view issues zero database queries; a captured `tenant_id` is shown like any other state key and nothing more).

### Share button in the debug panel

When a view has `time_travel_enabled = True` and the app is running under `DEBUG=True`, the debug panel's **Time Travel** tab grows a **"📋 Share bug"** button next to the timeline header. Clicking it:

1. Sends a `bug_capture_share` frame over the existing djust WebSocket.
2. The server calls `render_with_diff()` (a side-effect-free re-diff, not a handler dispatch) to get the current patches, then `encode_view_state()` with the configured default scrub, and replies with the resulting blob.
3. The client copies the blob to the clipboard via `navigator.clipboard.writeText()` — the server never touches the clipboard itself.

Configure a default scrub list so the button never has to be told which fields to redact per-click:

```python
# settings.py
LIVEVIEW_CONFIG = {
    "bug_capture_default_scrub": ["password", "ssn", "credit_card"],
}
```

The Share button is dev-only plumbing on top of the same `encode_view_state()` documented above — it doesn't widen what can be captured or bypass the DEBUG / prod-opt-in gate on `BugCapture.encode()`.

## What's coming in iter C (v1.1.0)

- **Iter C** ([#1561](https://github.com/djust-org/djust/issues/1561)) — Redis-backed snapshot store for payloads too large to fit in a URL fragment (~2 KB inline limit); a `djust replay` CLI for terminal-first workflows; a framework-level `LiveView.time_travel_excluded_fields` class attribute that auto-scrubs sensitive fields without requiring per-encode `scrub_fields()` calls; a new `djust check` V012 system check that warns when `time_travel_enabled = True` and view fields match common-PII patterns without being excluded.

## Strategy connection

This feature lands as part of the v1.1.0 milestone after promotion from "Path D killer demo" status to load-bearing v1.1 capability. The promotion was triggered by the [#1552 reporter's data point](https://github.com/djust-org/djust/issues/1552) about upstream-bug-velocity friction — the reporter's own words: *"the gap between 'I see it broken' and 'you can see it broken' is the full source tree."* The [v1.1 readiness session](../../strategy-sessions/2026-05-19-v1.1-readiness.md) recommended Path E (defer the headline-path decision until launch-soak data exists, with the hedge *"refuse to commit before data exists"*); the #1552 filing supplied that data.
