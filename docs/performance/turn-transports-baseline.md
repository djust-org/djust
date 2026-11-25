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
| `push` | an async handler that calls `push_state()` and then finishes | two renders, two patch frames |
| `editor` | a ~4 KB text area, a derived word count | sends the whole text back (as `dj-input` does) |

Save modes: `save` is the shipped default (each turn saves the view's state in
the server-side session); `nosave` is `automatic_state = False`. With `nosave`,
the HTTP fallback runs `mount()` on every POST and answers with the whole page,
as documented; that row is a different behaviour, not a cheaper one.

Simulated RTT: the client holds each frame it sends for RTT/2 before the server
gets it, and each answer for RTT/2 after the server sends it, on a kept-alive
connection. No TCP or TLS handshake and no bandwidth limit is modelled, so a
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
Browser-side patch and input time is not measured here: that needs a browser
and is #4001's measurement.

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

Each transport costs one round trip per turn on a warm connection: latency is
RTT plus the server cost above, within noise. Past 20 ms of RTT the network is
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
   must still see in-place mutation).
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

## Reproduce

```sh
make benchmark-transports                       # full run, ~3.5 min here
make benchmark-transports JSON=/tmp/run.json COMPARE=docs/performance/turn-transports-baseline.json
python scripts/bench_turn_transports.py --shapes table --transports ws,http --rtt 0
python scripts/bench_turn_transports.py --quick  # seconds; what the CI test runs
```

`--compare` prints the p50 and CPU ratio for every cell both runs have. The CI
test `python/djust/tests/test_bench_turn_transports_3657.py` runs the script at
`--quick` size and checks its output and that the committed JSON has every
cell; it asserts nothing about durations (#2156).
