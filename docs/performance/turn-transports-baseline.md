# Complete turns over WebSocket, SSE and HTTP: the 2.0 baseline (#3657)

The baseline that later performance work measures against: #3998 (per-view
memory), #3999 (Python list walks) and #4001 (client patch work). Rerun it with
`make benchmark-transports` and compare with
`make benchmark-transports COMPARE=docs/performance/turn-transports-baseline.json`.
The run below is committed as [`turn-transports-baseline.json`](turn-transports-baseline.json).

## Machine and versions

Measured on 2026-11-25 in a shared cloud container: Intel Xeon @ 2.30 GHz, 4 vCPUs,
15 GB RAM, Linux 6.18. Python 3.12.3, Django 5.2.17, channels 4.3.2, djust
2.0.0rc5 at `e61ce7677` (after the legacy removal in #4308), release `_rust`
build. SQLite on a local disk holds the database-backed sessions. Run under
`nice -n 10`, serially, in one process. The container was shared with other
work, so treat single cells as noisy and look at the pattern.

## Method

`scripts/bench_turn_transports.py` runs one Django project in-process. Each page
is loaded as a browser loads it (`djust.testing.open_live_page`: a GET through
`SessionMiddleware`, `CsrfViewMiddleware` and `AuthenticationMiddleware` and the
URLconf), then driven over one transport:

| Transport | What one turn is |
| --- | --- |
| `ws` | an `event` frame into `LiveViewConsumer` (a `WebsocketCommunicator`, no ASGI middleware); the timer stops when the frame carrying the event's `ref` comes back |
| `sse` | a POST to the SSE message URL; the answer arrives on the SSE stream the page opened |
| `http` | the page-POST fallback: one JSON POST with the render digest, as the client sends it |

Workloads, all explicit exposure (`state()` fields) with strict parameters:

| Shape | Page | Each turn |
| --- | --- | --- |
| `form` | six fields with error spans and a computed `invalid` | edits one field and re-validates it |
| `table` | 1,000 rows of dicts, five cells each | changes one cell of one row |
| `dashboard` | 24 scalars, a 20-item list, a dict, two `LiveComponent`s | changes one scalar |
| `push` | an async handler that calls `push_state()` and then finishes | two renders, two patch frames; the turn ends when both have arrived |
| `editor` | a ~4 KB text area, a derived word count | sends the whole text back (as `dj-input` does) |

Save modes: `save` is the shipped default (each turn saves the view's state in
the server-side session); `nosave` is `automatic_state = False`. With `nosave`,
the HTTP fallback runs `mount()` on every POST and answers with the whole page,
as documented; that row is a different behaviour, not a cheaper one.

Simulated RTT: the client does `asyncio.sleep(RTT/2)` before it sends each
event and `asyncio.sleep(RTT/2)` after the answer is back, on a kept-alive
connection. That is a fixed delay around the turn, so the 20/100/250 ms columns
are the 0 ms column plus the RTT by construction; they show where a delay that
size puts the server cost, not any interaction between latency and the
transports. No TCP or TLS handshake and no bandwidth limit is modelled, so a
cold HTTPS fallback request pays one or two more round trips than shown.

Columns: p50 / p95 turn latency; CPU per turn (`time.process_time`, client and
server in one process, so it includes the in-process test client's work);
event-loop lag (a 2 ms ticker on the loop, p95 and max overshoot); median wire
bytes per turn (the event's JSON body up, the answer's JSON down, no headers).
Retained memory is a separate `tracemalloc` pass: five pages per cell, each
after three turns, a warm-up page first so caches are not charged to them.

At RTT 0: 40 turns per cell after 3 warm-up turns, rounds rotating over every
transport and mode, so drift lands on every cell alike. RTT 20/100/250 ms: 10
turns per cell, `save` mode only, so p95 there is the second-largest sample.
Total run time on this machine: 3.5 minutes.

The app does no database or API work of its own. Everything measured is
framework cost, including the session save (one SQLite read and write per turn).
The browser's side of the same turns (input to send, applying the answer, long
tasks) is measured separately in a real browser: see
[Browser side of a turn](#browser-side-of-a-turn-4314) below (#4314).

## Results at RTT 0 (server cost)

| Shape | Transport | save p50 / p95 ms | nosave p50 / p95 ms | save CPU ms | down bytes (save) |
| --- | --- | ---: | ---: | ---: | ---: |
| form | ws | 8.9 / 11.7 | 4.8 / 6.7 | 7.4 | 342 |
| form | sse | 12.6 / 18.4 | 5.9 / 7.5 | 9.6 | 342 |
| form | http | 11.6 / 16.2 | 3.5 / 4.8 (html) | 9.9 | 1,014 |
| table | ws | 45.7 / 97.8 | 27.9 / 49.9 | 48.6 | 314 |
| table | sse | 50.3 / 90.6 | 30.2 / 59.9 | 47.9 | 314 |
| table | http | 164.6 / 244.5 | 58.8 / 108.0 (html) | 161.0 | 869 |
| dashboard | ws | 8.7 / 12.5 | 3.9 / 7.9 | 7.1 | 314 |
| dashboard | sse | 12.5 / 21.3 | 4.3 / 7.9 | 9.6 | 314 |
| dashboard | http | 15.8 / 24.2 | 4.0 / 7.6 (html) | 10.8 | 905 |
| push | ws | 8.7 / 15.1 | 4.6 / 7.7 | 8.3 | 542 |
| push | sse | 11.9 / 19.3 | 5.4 / 8.2 | 8.9 | 542 |
| editor | ws | 8.1 / 17.4 | 4.3 / 6.3 | 6.7 | 4,522 |
| editor | sse | 12.2 / 24.0 | 4.6 / 9.2 | 8.1 | 4,522 |
| editor | http | 13.0 / 27.8 | 3.8 / 6.8 (html) | 9.7 | 5,090 |

"(html)": the `nosave` HTTP fallback re-mounts and answers with the whole page
(193 KB for the table, against 869 bytes of patches with `save`). Event-loop lag
p95 stayed under 3.2 ms everywhere except the table (5 to 7 ms; a max of 30 to
57 ms on several cells, which is the 1,000-row render holding the GIL).

## Results with simulated RTT (`save` mode, p50 ms)

| Shape | ws 20 | sse 20 | http 20 | ws 100 | sse 100 | http 100 | ws 250 | sse 250 | http 250 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| form | 33.8 | 36.2 | 34.1 | 112.6 | 116.5 | 114.1 | 261.7 | 265.1 | 264.0 |
| table | 65.7 | 68.0 | 181.3 | 146.2 | 153.8 | 260.2 | 292.9 | 300.0 | 403.8 |
| dashboard | 33.4 | 36.2 | 39.3 | 113.1 | 115.3 | 117.1 | 264.4 | 266.7 | 268.0 |
| push | 31.2 | 34.7 | n/a | 113.8 | 116.3 | n/a | 262.8 | 265.8 | n/a |
| editor | 31.3 | 33.6 | 34.1 | 111.6 | 116.7 | 115.0 | 261.4 | 264.8 | 266.3 |

Each transport costs one round trip per turn on a warm connection, so latency
is RTT plus the server cost above, within noise. That is how the delay is
simulated (see Method), not a finding. Past 20 ms of RTT the network is
most of the turn for every shape except the 1,000-row table, and the transports
are within 5 ms of each other except the table over HTTP. Two cells have one
outlier sample that sets their p95 (editor/sse at 20 ms: 308 ms; push/sse at
20 ms: 82 ms); the JSON has every cell.

## Retained memory per open page (KB, after three turns)

| Shape | ws save | ws nosave | sse save | sse nosave | http save | http nosave |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| form | 109 | 94 | 121 | 110 | 37 | 30 |
| table | 3,015 | 1,420 | 2,423 | 1,258 | 773 | 285 |
| dashboard | 192 | 140 | 194 | 153 | 228 | 216 |
| push | 106 | 94 | 126 | 109 | n/a | n/a |
| editor | 169 | 149 | 187 | 171 | 54 | 47 |

An HTTP page holds no view between requests; its figure is the session row and
process caches the POSTs left behind.

## What this says to do next (ranked)

1. **The session save is about half of a small turn.** `save` against `nosave`
   on the same socket: +4 to +5 ms for form, dashboard, push and editor
   (roughly 2x) and +18 ms for the table. Over SSE the same save costs
   3 to 4 ms more than over the socket (+6.5 to +8 ms for the small shapes). It is the
   largest fixed per-turn cost. Candidate: #4159/#4160/#4161 (deferred and
   coalesced saves). Correctness risk: medium (what survives a crash between
   turns).
2. **The HTTP fallback with a save is 3.6x the socket on a large page**
   (165 ms against 46 ms for the table; CPU 161 ms): every POST restores the
   state from the session, mounts the view and saves again. Candidate: make
   the restore and the save cost proportional to the change (the #4164 /
   #4229 area). Risk: medium.
3. **A 1,000-row table costs 28 ms a turn without any save, to change one
   cell,** and stalls the loop up to 57 ms. That is the Python walks over the
   whole list (change detection, Rust sync): #3999. Risk: high (the shortcuts
   must still see in-place mutation). First slice measured below.
4. **A 1,000-row table page holds 1.4 MB per socket without the save and
   3.0 MB with it**, against 0.1 to 0.2 MB for the small shapes: the
   retained fingerprint trees and copies that grow with the state, #3998. Risk: medium.
5. SSE adds 3 to 5 ms over WebSocket per turn with the save and under
   2.5 ms without it (item 1 covers most of it); not a priority on its own. Wire bytes
   are small for every patch answer (310 to 540 bytes); the editor sends the
   whole text up each turn (4 KB), which a text-diff input mode would remove
   but which no measured latency here depends on.

At any real-world RTT the first, third and fourth items matter more for
capacity (CPU and memory per page) than for the latency one user sees.

## #3999, first slice: one changed cell in a 1,000-row list

Measured 2026-11-25 on the same machine, at the commit that added it, with
`scripts/bench_turn_transports.py --shapes table --transports ws --modes nosave
--rounds 100` and a timer around each phase of the turn (two runs each,
interleaved; the shortcut switched off through `djust._per_turn` for "before").
Milliseconds per turn, `nosave` over the socket:

| | before | after |
| --- | ---: | ---: |
| turn p50 / p95 | 23.6-24.1 / 46-48 | 20.8 / 27-28 |
| CPU per turn | 26.2-26.7 | 21.8-22.2 |
| the two event snapshots (two walks) | 4.4 | 4.1 |
| the Rust sync, in Python | 7.8 | 4.5 |
| of which the sync's walk of the list | 3.8 (fingerprint + convert) | 0.5 (convert only) |
| Rust render and diff | 9.4-10.0 | 9.2-9.3 |

Walks of the list in Python per turn: four before #3999 (two snapshots, the
sync's fingerprint-and-convert, `_collect_sub_ids`); three after its first
shortcut (`plain_sub_ids`); now two fingerprint walks and one conversion walk
that builds no fingerprint. The sync takes the post-event snapshot's walk of
the list (its fingerprint, and whether it is plain data) when nothing but the
framework runs between the two: no `get_context_data` or
`get_render_only_context` override, no template property, no class-level
component, no `sync_turn_wrapper`. Otherwise it walks again, as before.
`test_per_turn_counts.py` pins the counts, `test_per_turn_invalidation.py` the
cases where the walk must not be reused.

The issue's provisional target, under 10 ms of server time for this turn, is
not met: Python's share fell from about 12 ms to about 9 ms, and what is left
is the two snapshot walks (4 ms), `update_state` converting the whole list in
Rust (about 3 ms) and the Rust render and diff (about 9 ms). Item-level deltas
for keyed lists, a Rust fast path for a turn that changes two text regions,
and the per-render serialization on the state backend are the follow-up
(v2.1.0).

## Browser side of a turn (#4314)

`make benchmark-client-turns` (`scripts/bench_client_turns.py`) serves the same
Django project and the same five views as the server bench (it imports them),
with uvicorn in-process and `LiveViewConsumer` on `/ws/live/`, and drives each
page in headless Chromium through the shipped client with real DOM events: a
typed value on a `dj-input` (form, editor), a click on a `dj-click` button
(table, dashboard, push). Committed as
[`client-turns-baseline.json`](client-turns-baseline.json).

Measured 2026-11-25 on the machine above, Chromium 158.0.8127.36 headless,
after #4001 / #4328 and the incremental shape below. 30 measured turns per cell after 3 warm-up
turns, one cell at a time, under `nice -n 10`: p95 is the second-largest sample
of 30, so read it as noisy. Total run time about one minute.

Two changes to the server bench's pages, so a browser can drive them: the
table, dashboard and push pages get one `<button dj-click>`, and the form and
editor inputs carry `dj-debounce="0"`. The shipped default debounces a typed
`dj-input` by 300 ms, a fixed timer by design: with it, "send" below is that
300 ms plus the figure shown. The page loads the unminified `client-core.js`
(the same source as `client-core.min.js`) with a timer inserted around the
response handler.

Columns, from `performance.now()` in the page, p50 / p95 ms:

- **send**: input event dispatched to the event frame handed to
  `WebSocket.send` / `fetch` (binding, rate-limit wrapper, parameters,
  serialization, and for HTTP the render digest of the page).
- **apply**: time inside the client's response handler: `applyPatches`, or the
  whole-page morph, plus `reinitAfterDOMUpdate`; summed over a push turn's two
  answers.
- **total**: input to the last answer applied. It includes the server's turn
  and the loopback network, so it is context, not a client figure.
- **long**: main-thread long tasks (50 ms or more) during the 30 turns, with
  the longest.
- **down**: the answer's bytes (WebSocket message; `fetch` body).

Cells: `ws` (the default, state saved), `http` (the page-POST fallback with the
save: patches), `http-nosave` (`automatic_state = False`: each POST re-mounts
and answers with the whole page).

| Shape | Elements | Cell | send | apply | total | long | down bytes |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| form | 35 | ws | 0.4 / 2.7 | 0.5 / 1.0 | 8.1 / 15.7 | 0 | 404 |
| form | 35 | http | 0.8 / 2.6 | 0.5 / 1.1 | 61.0 / 73.9 | 0 | 264 |
| form | 35 | http-nosave | 0.7 / 2.0 | 0.9 / 1.2 | 15.3 / 20.7 | 0 | 1,277 |
| table | 6,019 | ws | 0.2 / 0.3 | 1.0 / 1.8 | 35.1 / 71.8 | 0 | 407 |
| table | 6,019 | http | 1.3 / 2.6 | 0.8 / 3.8 | 165.9 / 391.1 | 0 | 250 |
| table | 6,019 | http-nosave | 0.8 / 1.3 | 32.6 / 54.3 | 112.8 / 200.2 | 3 (62 ms) | 192,807 |
| dashboard | 66 | ws | 0.2 / 0.5 | 0.5 / 0.9 | 7.9 / 12.2 | 0 | 403 |
| dashboard | 66 | http | 0.5 / 0.8 | 0.5 / 1.3 | 28.3 / 47.4 | 0 | 243 |
| dashboard | 66 | http-nosave | 0.4 / 0.6 | 0.7 / 1.2 | 15.3 / 20.1 | 0 | 1,849 |
| push | 18 | ws | 0.2 / 0.5 | 0.6 / 1.0 | 9.8 / 17.4 | 0 | 719 |
| editor | 18 | ws | 0.6 / 2.7 | 0.4 / 1.0 | 9.6 / 13.5 | 0 | 4,642 |
| editor | 18 | http | 0.9 / 3.2 | 0.5 / 1.3 | 29.2 / 71.2 | 0 | 4,456 |
| editor | 18 | http-nosave | 0.7 / 2.5 | 0.6 / 1.1 | 14.6 / 20.2 | 0 | 4,925 |

Before, p50 / p95 ms, from separate runs of the same command: the client
from before #4001 (`--client` with `client-core.js` from `6c9526298`), and
the one before the incremental shape (`88a801617`):

| Table cell | before #4001 | before the incremental shape | now |
| --- | ---: | ---: | ---: |
| ws, apply | 3.6 / 6.3 | 1.1 / 2.0 | 1.0 / 1.8 |
| http, send | 9.6 / 14.4 | 9.5 / 20.3 | 1.3 / 2.6 |
| http, apply | 12.9 / 26.2 | 9.5 / 18.3 | 0.8 / 3.8 |
| http-nosave, apply | 31.5 / 45.2 | 34.5 / 49.4 | 32.6 / 54.3 |

Every other cell moved by under 0.3 ms between the three. The whole-page morph
is not scoped, so its spread is run-to-run noise. #4001's own by-size bench
(`scripts/bench_client_patch_turn.py`, an unrelated counter update with no
server) went from 0.2 / 0.6 / 6.0 ms to 0.2 / 0.2 / 0.2 ms p50 at 100 / 1,000 /
10,000 elements.

What this says:

1. **Over the socket the browser's part is about a millisecond** for every
   shape, the 1,000-row table included (6,000 elements): the turn is the
   server's (compare the server table above). Input to send is under 0.5 ms.
2. **The HTTP fallback's render digest now follows the change.** It cost
   about 10 ms each way on the table: the shape (`domShape`, #4108) was a walk
   of the whole LiveView root, before the POST and again before the answer's
   patches were applied. Each element's piece is now kept with its CRC-32 and
   Adler-32 and combined (zlib's `crc32_combine` / `adler32_combine`), and a
   MutationObserver forgets the pieces a change makes stale, so a turn
   rehashes the changed path: send 9.5 → 1.3 ms, apply 9.5 → 0.8 ms p50. The
   value is byte-identical to the server's: seeded random pages and edits
   (`python/djust/tests/test_dom_shape_random_4001.py` writes the server's
   shapes, `tests/js/dom_shape_incremental_4001.test.js` checks the client's,
   kept and from scratch).
3. **The whole-page answer** (`http-nosave`, 193 KB for the table) takes
   33 ms p50 to morph, with three long tasks (up to 62 ms) in 30 turns. The small
   shapes' whole-page answers cost under 1 ms more than their patches.

The server's `total` figures here are not comparable with the server table's:
uvicorn shares this process with the Playwright driver, and the server bench's
HTTP figures do not pay the HTTP server.

## #4325: item-level list deltas, multi-span text path, state backend

Measured 2026-11-25 on the same machine, release build, same benchmark
(`--shapes table --transports ws --modes nosave,save --rtt 0 --rounds 80`,
two interleaved runs per side; "before" is the new `list_item_deltas`
shortcut switched off through `djust._per_turn`). One changed cell in a
1,000-row list, milliseconds per turn:

| | before | after |
| --- | ---: | ---: |
| turn p50, `nosave` | 22.0-22.4 | 20.1-20.8 |
| turn p50, `save` | 23.6-23.7 | 21.3-21.8 |
| CPU per turn, `nosave` | 23.0-23.9 | 21.6-23.0 |
| the Rust sync, in Python | 5.1 | 2.3 |
| of which `update_state` (whole list) | 2.4 | 0.05 (`update_state_items`, one row) |
| Rust render and diff | 11.6 | 9.6 |

**Item-level deltas (`list_item_deltas`).** When the two event snapshots show
that a handler changed only a few rows of a list in place (the same object, the
same length, at least 16 rows, at most a quarter of them changed, and the
fingerprint Rust's copy was built from equals the pre-event walk), the sync
converts just those rows and replaces them in Rust's state with
`RustLiveView.update_state_items`. Anything else, including a refusal from
Rust, takes the whole-list path. The render still renders every row: a loop
body that holds `{% if %}` carries index-numbered `dj-if` markers, so the loop
render cache cannot reuse its rows, and rendering only the changed item needs a
per-item fragment store in the renderer (not done; see below).

**Multi-span text path.** A render whose HTML differs from the previous one
only in the text of up to eight text nodes (two cells of one row, a heading and
a footer) now skips the parse and diff like a one-span change does. A
two-cell change in the same 1,000-row table, measured on `RustLiveView` alone
(300 renders): `render_with_diff` p50 14.8-16.4 ms through the full parse,
8.2-8.4 ms on the new path (parse plus diff 6.5-7.2 ms down to about 1 ms).
The bench's own turn changes one cell, so its numbers above do not move with
this.

**State-backend serialization per render.** Not there any more: over 360 turns
(table, form and dashboard; WebSocket, SSE and HTTP; `save` and `nosave`) the
state backend's `set` and `refresh` ran 0 times, and `serialize_msgpack` is not
called on a turn. `refresh()` has no production caller. The `save` mode's extra
1-2 ms is the session handling, not the backend.

**Still over the 10 ms target.** What is left for this turn, of about 20 ms:
the Rust render of all 1,000 rows (about 6 ms), the text fast path itself
(about 1 ms, most of it cloning the VDOM), and the two event snapshot walks
(about 4 ms). Rendering only the changed loop item, and replacing the pre-event walk
with the previous turn's fingerprints, are what remain.

**Per-item loop render (follow-up).** Rust now keeps each item's HTML of an
outermost `{% for %}` and reuses it when the item hashes the same at the same
index (switch `loop_item_render`; conditions in `changelog.d/4325-loop-items.changed.md`).
Same machine, table shape, WebSocket, RTT 0, 80 rounds, two runs each, on vs
off: turn p50 14.8-15.5 ms vs 19.6 ms (`nosave`), 16.0-16.2 ms vs 20.8-21.2 ms
(`save`). The two-cell microbenchmark above: `render_with_diff` p50 3.6-3.8 ms
vs 8.1-8.4 ms. The pre-event walk is what remains of the large items.

## Reproduce

```sh
make benchmark-transports                       # full run, ~3.5 min here
make benchmark-transports JSON=/tmp/run.json COMPARE=docs/performance/turn-transports-baseline.json
python scripts/bench_turn_transports.py --shapes table --transports ws,http --rtt 0
python scripts/bench_turn_transports.py --quick  # seconds; what the CI test runs
make benchmark-client-turns                     # the browser side (#4314), ~1 min here
python scripts/bench_client_turns.py --shapes table --cells ws,http-nosave --turns 10
```

`--compare` prints the p50 and CPU ratio for every cell both runs have. The CI
test `python/djust/tests/test_bench_turn_transports_3657.py` runs the script at
`--quick` size and checks its output and that the committed JSON has every
cell; it asserts nothing about durations (#2156). The browser bench needs Chromium
and is not run in CI; `python/djust/tests/test_bench_client_turns_4314.py`
checks what can rot without a browser (the hook it inserts into the client,
the triggers it adds to the pages, every cell in the committed JSON).
