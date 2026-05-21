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

> **v1.1 status.** Iter A (this page) ships the data shape, encoder/decoder, and PII-scrub hook. Iter B ([#1561](https://github.com/djust-org/djust/issues/1561)) will add the read-only replay viewer at `/__djust__/replay/<blob>`. Iter C ([#1562](https://github.com/djust-org/djust/issues/1562)) will add a Redis store for large payloads, a `djust replay` CLI, and a framework-level `time_travel_excluded_fields` class attribute.

## When to use this

The use case that motivated this feature: a downstream consumer files a bug like ["VDOM diff appends new subtree instead of replacing when {% if %}/{% elif %} swaps between {% include %}d templates"](https://github.com/djust-org/djust/issues/1552). The reproducer is locked behind hundreds of private template/view/model files. Without bug-capture the maintainer's options are:

1. Ask for a sanitized minimal-repro (slow — hours to days for a complex template tree).
2. Guess from the description (risky — the previous fix attempt got the root cause wrong precisely because it tested a synthetic shape, not the real one).
3. Get screen-sharing access (expensive).

With bug-capture the reporter shares one URL fragment. The maintainer pastes it into a viewer (iter B) or a local REPL and sees the real state + patches.

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

Iter B will add a browser-based replay viewer; for now, programmatic inspection like the above is the consumption path.

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

`patches` is required and must be either the JSON string `render_with_diff()` returns or an already-decoded list of patch dicts. **Why caller-supplied:** iter A intentionally does not couple to the render pipeline — djust's `render_with_diff()` returns patches into the WebSocket / SSE / runtime frame paths without stashing them on the view, so there's no framework attribute to introspect. Iter B (#1561) will add a debug-panel button that calls `render_with_diff()` + this function in one click.

Pass `event_name=...` to pick a specific past event rather than the latest.

### `scrub_fields(*names) -> Callable[[BugCapture], BugCapture]`

Ready-made scrub callable. Removes each named field from both `state_before` and `state_after`. Absent fields are silently ignored. Field names removed (but values held back) are appended to `scrubbed_fields` for wire-visible transparency.

## What's coming in iter B and C (v1.1.0)

- **Iter B** ([#1561](https://github.com/djust-org/djust/issues/1561)) — Read-only replay viewer at `/__djust__/replay/<blob>`. Open the URL in a browser; see the captured state side-by-side with the patches, scrub through state diffs, inspect handler params. DEBUG-gated. Share button in the existing debug panel.
- **Iter C** ([#1562](https://github.com/djust-org/djust/issues/1562)) — Redis-backed snapshot store for payloads too large to fit in a URL fragment (~2 KB inline limit); a `djust replay` CLI for terminal-first workflows; a framework-level `LiveView.time_travel_excluded_fields` class attribute that auto-scrubs sensitive fields without requiring per-encode `scrub_fields()` calls; a new `djust check` V012 system check that warns when `time_travel_enabled = True` and view fields match common-PII patterns without being excluded.

## Strategy connection

This feature lands as part of the v1.1.0 milestone after promotion from "Path D killer demo" status to load-bearing v1.1 capability. The promotion was triggered by the [#1552 reporter's data point](https://github.com/djust-org/djust/issues/1552) about upstream-bug-velocity friction — the reporter's own words: *"the gap between 'I see it broken' and 'you can see it broken' is the full source tree."* The [v1.1 readiness session](../../strategy-sessions/2026-05-19-v1.1-readiness.md) recommended Path E (defer the headline-path decision until launch-soak data exists, with the hedge *"refuse to commit before data exists"*); the #1552 filing supplied that data.
