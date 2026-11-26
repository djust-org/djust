---
title: "Wire Protocol"
slug: wire-protocol
section: advanced
order: 5
level: advanced
description: "The frames djust's server sends to the browser: one render frame per turn, with its side effects in an ordered effects list, and the protocol version that keeps a cached bundle in step."
---

# Wire Protocol

Most applications never read a frame: `push_event()` reaches `dj-hook`
`handleEvent`, `put_flash()` reaches `{% dj_flash %}`, and so on. This page is
for custom client code, test harnesses and anyone reading the network tab.

## One `render` frame per turn

Every turn result reaches the client as one `render` frame. The WebSocket and
SSE send it as a frame, and the HTTP fallback (the page POST) returns it as the
JSON body of its answer:

```json
{"type": "render", "source": "event", "ref": 7, "version": 12,
 "patches": [...],
 "effects": [{"kind": "push_event", "event": "saved", "payload": {"id": 3}},
             {"kind": "flash", "action": "put", "level": "success", "message": "Saved"},
             {"kind": "navigation", "action": "live_patch", "params": {"page": 2}}]}
```

**The DOM part** is at most one of:

| Field | Meaning |
| --- | --- |
| `patches` | VDOM patches against the page's current version (`html` may ride alongside, as the fallback for a client that cannot apply them) |
| `html` | The whole view's HTML, morphed in place |
| `view_id` + `html` | An embedded child (`{% live_render %}`), replaced in its container |
| none | The turn changed nothing: an acknowledgement |

The client applies the DOM part first, then the effects, in list order.

**The effects** are what the handler queued. Their order is fixed, whatever
order the handler queued them in:

| `kind` | From | Fields |
| --- | --- | --- |
| `push_event` | `push_event()`, `push_commands()` | `event`, `payload` |
| `flash` | `put_flash()`, `clear_flash()` | `action`, `level`, `message` |
| `page_metadata` | `page_title`, `page_meta` | `action`, `value` (or `name`, `content`) |
| `layout` | `set_layout()` | `path`, `html` |
| `navigation` | `live_patch()`, `live_redirect()` | `action`, `path`, `params`, `replace` |
| `navigate` | an auth or refusal redirect | `to` |

Within one kind, effects keep the order they were queued in. A client skips an
effect `kind` it does not know.

**Other fields** say what the frame answers: `ref` and `event_name` for an
event, `version` for a DOM part, `source` for where the turn came from
(`event`, `tick`, `broadcast`, `async`, `push`, `notify`, `url_change`,
`mount`, `refusal`, `push_state`, ...), and the snapshot and parameter-contract
fields the client stores.

**Deferred callbacks.** A `defer()` callback runs after the turn's frame is
sent. Whatever it queues goes out in the same turn, in one effects-only frame
with `source: "deferred"`. Over the HTTP fallback those effects are appended to
the answer's own `effects`.

Frames that are not turn results keep their own types: `connect`,
`sse_connect`, `mount`, `mount_batch`, `error`, `html_recovery`,
`view_refused`, `presence_event`, `reload`, `child_update`, `sticky_update`
and the upload frames.

## What it replaced

Before 2.0 a turn could send several frames: `patch`, `html_update`, `noop` or
`embedded_update` for the DOM, then one frame each for `push_event`, `flash`,
`page_metadata`, `layout`, `navigation` and `navigate`. The HTTP answer carried
the side effects as `_push_events`, `_flash`, `_page_metadata`, `_layout` and
`_navigation` keys. The old frame types and keys are gone; an effect object is
the old frame with `type` renamed to `kind`.

## The protocol version

The server and the client bundle name one integer, the protocol version
(`djust._protocol.PROTOCOL_VERSION`, `window.djust.PROTOCOL`). It changes when a
change to the frames would be misread by an older client. Adding an optional
field or a new effect kind does not change it.

The client sends its version as `"protocol": N` in every `mount`, `mount_batch`
and `live_redirect_mount` frame, as `&protocol=N` on the SSE stream GET, and as
the `X-Djust-Protocol` header on the HTTP and SSE-message POSTs. The server
names its own on `connect`, `sse_connect`, `mount` and the `X-Djust-Protocol`
header of an HTTP answer.

A tab whose bundle speaks another version (a page left open across an upgrade)
reloads itself once:

| Wire | The server answers |
| --- | --- |
| WebSocket | `{"type": "reload", "reason": "protocol", "protocol": N}`, then close code `4409` |
| SSE stream GET | the same `reload` frame, then the stream ends |
| HTTP POST, SSE message POST | status `409`, `{"type": "error", "code": "protocol_mismatch", ...}` |

The client also reloads when the server's version differs from its own. If a
second mismatch comes within 30 seconds, the client does not reload again: it
stops the live transport, dispatches `djust:error` with
`code: "protocol_mismatch"`, and the server-rendered page stays usable. The
bundle URLs carry `?v=<djust version>`, so the reload fetches the new bundle.

`settings.DJUST_ALLOW_MISSING_PROTOCOL = True` accepts a request that names no
version (a non-browser client, or a test that builds frames by hand). A version
that is present and different is refused regardless.

## Reading frames in tests

`djust.testing` live pages (`LivePage.connect()`, `connect_sse()`) send the
protocol version for you. Match `"render"` and read `effects`:

```python
answer = await socket.send_event("save")
assert answer["type"] == "render"
assert "patches" in answer
assert [e["event"] for e in answer["effects"] if e["kind"] == "push_event"] == ["saved"]
```

`LivePage.receive(("patch",))` and `receive(("push_event",))` still work: a
`render` frame also answers to the old name of its DOM part (`patch`,
`html_update`, `embedded_update`, `noop`) and to the `kind` of each effect it
carries.
