# djust 2.0: deep analysis, round 2 — eight follow-up investigations

> **Scope note.** Measurements in this document were taken on commit `9a19cf95d` of the 2.0 branch before it was rewritten on 2026-10-28. Several round-1 items have since landed on `2.0` (a bounded fingerprint budget, a template-source cache, an opt-in hook `shouldUpdate()` and attribute-gated Chart.js updates; see [2026-10-28-djust-2-next-priorities.md](2026-10-28-djust-2-next-priorities.md)). Raw per-investigation reports, verification verdicts and harnesses are held in a private evidence archive; this public copy omits private downstream application code and unpatched security detail.

Date: 2026-10-28. Tree: branch `analysis/2.0-deep-dive` (cut from `origin/2.0` at `9a19cf95d`). Follows [2026-10-27-djust-2-deep-analysis.md](2026-10-27-djust-2-deep-analysis.md), whose "not verified" list this round works through. Raw reports, harnesses and result files are held in the private evidence archive. Each report was handed to an independent skeptic who re-derived its load-bearing claims from source and data; none was refuted, and every correction they made is applied below. Numbers come from commands recorded in the source reports.

## Summary

| # | Question | Answer in one line |
|---|---|---|
| 1 | Do the documented examples run? | 126 of 134 runnable doc snippets pass; 8 fail (5 phantom attributes, 1 public-API collision). The README "add to existing project" path gives a dead counter (static files never served). The demo project has 6 pages that 500 and 7 first-click server errors. Draft PR #3652 carries the executing test. |
| 2 | What does the client cost in a real browser? | 0.4 ms per turn at 100 nodes, 0.8 ms at 1,000, 7.4 to 8.7 ms at 10,000; nearly all in `_scanScopedElements`. The focus-steal is user-visible (blur/focusout on the field being typed in, every frame). The Chart.js adapter redraws on every frame by default. |
| 3 | What would deleting the catalog break? | 70 of 205 tags are used by one of 17 owner apps, 135 by none; 40 of the 45 tags in production use are on the proposed delete list. The real coupling is `theme_head` auto-linking the catalog CSS, not the CSS prefix rewriter. |
| 4 | What survives reconnect, worker restart, multiple workers? | Only `state(persist="server")` and the legacy `enable_state_snapshot` opt-in survive anything. `persist="client"` loses state on a plain reconnect because the shipped client never echoes the token. In-flight `start_async` results are lost after any reconnect or restart in every topology. Explicit mode is 1.8 to 2x legacy latency regardless of topology. |
| 5 | Should the engine delegate cold behaviour to Django? | Yes for 12 cold filters (about 1,260 lines now, up to ~3,000 with a wire-shape policy), but it buys maintenance, not speed: the template phase is 10% of a demo GET and the one template-bound djust.org page is bound by `for`/`if`/`include` over a large context, not filters. |
| 6 | Is the explicit path secure? | No high or medium finding across 54 empirical probes. Two LOW defects (HTTP fallback answers 200 for an unknown event name; `view` accepted as a state field name). The authorization re-check on every action and retrieval held on every transport. |
| 7 | Is the test suite load-bearing? | 39 of 43 gated tests are load-bearing (9.3% tautology). 64% of main pushes in the last 100 runs were red. 28 of 36 Playwright files never run in CI; 65 of 181 catalog component names are never instantiated by a test. A tiering proposal saves ~41% of CI minutes. |
| 8 | Does the canon still point at real code? | Of 801 `path:line` citations, ~25% exact, ~57% drifted, ~18% dead (dead over-counted by about a quarter). Every named symbol still resolves. 23 of 114 rules have no recurrence evidence; #1646 recurred 21 times. |

## 1. Documented examples, executed

Method: a new test (`tests/test_doc_snippets_execute.py`, draft PR [#3652](https://github.com/djust-org/djust/pull/3652), 23 checks green) executes all 224 fenced Python blocks under `docs/website/guides`, `QUICKSTART.md` and the getting-started pages that define a LiveView or LiveComponent, mounts the inline-template ones, resolves each `dj-*` binding to a decorated handler and fires every handler. Five gate-off mutations each turn a passing block red.

- 126 pass, 8 fail (pinned as strict xfails), 90 are not runnable in isolation (reader's app, model, template file, URL kwargs).
- The 8 failures: five phantom attributes or methods (`declarative-ux-attrs.md:710`, `error-codes.md:533`, `hooks.md:468`, `http-api.md:416` calls a `subscribe_presence` that exists nowhere, `tutorial-multi-step-wizard.md:117`); one inline template with no `dj-root` (`time-travel-debugging.md:25`); one deliberate raise; and one real public-API collision: `from djust import rate_limit` returns the `djust.rate_limit` module once `runtime.py` has imported it, so `@rate_limit(rate=0.1, burst=3)` raises `TypeError: 'module' object is not callable` (`tutorial-streaming-ai.md:78`; `djust/__init__.py:20-31` rebinds only `live_view`).
- **The README "add to an existing project" steps, applied verbatim, give a dead counter.** The README's `asgi.py` uses a bare `get_asgi_application()`, so under uvicorn `/static/djust/client.js` is 404, the client never loads, and the count stays at 0. Wrapping it in `ASGIStaticFilesHandler` (as `installation.md:355-364`, the scaffold and the demo do) makes it work with zero console errors. This is the second README defect in two days; the executing test could not catch it because it is a serving-path problem.
- `djust new` works end to end against the released PyPI 1.3.0rc14 (page connects, first control updates the DOM, no console errors).
- Demo project crawl (147 URLs, headless Chromium): 131 return 200, 6 return 500 (`markdown-stream`: unregistered `djust_markdown` tag; `model-form`: missing table; `no-template`: `render_full_template()` signature mismatch; three rentals portal views read an `error_message` that `mount()` never sets), 7 first-click server errors (undecorated `increment_counter` at `djust_homepage/views.py:326`, undecorated `change_period` at `djust_rentals/views/financials.py:124`, `@optimistic` without `@event_handler` at `optimistic_counter_demo.py:160`, and a mis-nesting at `djust_demos/views/demos.py:28-50` where five one-line subclasses leave the whole handler block indented under the last one, so `/demos/` has no `decrement`), 2 views outside `LIVEVIEW_ALLOWED_MODULES`, and 18 pages whose templates have no `dj-root` so clicks do nothing.

## 2. Client cost in a real browser

Method: 18 pages (100 / 1,000 / 10,000 nodes × plain, cell, chart, chart with attribute updates, editor hook, form with `[autofocus]`), uvicorn, Playwright Chromium, in-page instrumentation, CDP sampling profiler and a `querySelectorAll` counter; 50 unrelated-counter clicks per page.

| nodes | client work per turn (recv to task end, p50) | dominant frame |
|---|---|---|
| 126 | 0.40 ms | fixed scans |
| 1,026 | 0.80 to 1.10 ms | `_scanScopedElements` 0.39 ms |
| 10,026 | 7.4 to 8.7 ms (CDP task 15 to 18 ms) | `_scanScopedElements` 4 to 5 ms |

- Growth is mildly superlinear (0.45 µs/node from 100 to 1,000, 0.73 µs/node from 1,000 to 10,000). Recv-to-first-mutation is a flat 0.2 ms, so all page-proportional work is in `reinitAfterDOMUpdate` (`09-event-binding.js:2338`), and inside it `scanElement` at `09-event-binding.js:384` plus the per-attribute loop at `:452` and the native `querySelectorAll('*')` at `:453`.
- Fixed per-turn scans regardless of patch: 39 `querySelectorAll`, 7 `querySelector`, 17 `closest`, including seven model-binding scans (`20-model-binding.js:203-225`) and the `[autofocus]` lookup.
- `getNodeByPath` (the `SetText`-without-`dj-id` case from round 1) is now measured: 0.29 ms self on the 10,000-row cell page plus the child-filter closure at `12-vdom-patch.js:434`, and 2 ms of layout versus 0.07 on the plain page.
- **The Chart.js adapter calls `chart.update()` on every frame** because `shouldUpdate` (`ext/dj-chart.js:116-125`) defaults to true: 54 of 54 turns with unchanged data, +1.3 ms script, +1 ms rAF and 130 to 180 KB of garbage per turn. The `dj-chart-update="attributes"` opt-in reduces it to one update. Every hook's `updated()` fires on every frame. On every chart page load the console logs "No hook registered for Chart" because `djustInit` runs in a microtask before the deferred extension registers (`14-init.js:184-215`).
- **The focus-steal is user-visible.** On the form page every frame fires `blur` and `focusout` on the input being typed in and `focus` on the `[autofocus]` field, then restores (50 of 50 turns, 100 of 100 at 10,000 nodes). After a real mouse click on a button, `activeElement` ends on the autofocus field rather than the button. No characters or caret positions were lost.
- Input latency is not a problem below 10,000 nodes (keydown queueing p95 0.2 ms); at 10,000 nodes keystrokes queue behind the 7 to 9 ms task (max 2 to 6 ms).
- Transport: uvicorn plus the `websockets` library costs ~15 ms per loopback turn versus 1.4 ms through the in-process communicator; at 10,000 nodes the server turn (39 to 82 ms send-to-receive) dominates everything on the client.
- Shipped client transfer: 83,921 B gzip; Chart.js itself is 69,391 B gzip on top.

First load (run after the main pass; results in the private evidence archive):

| page | throttle | HTML | `load` | client.js download | WS open | mount received | mount ready | blocking time before ready |
|---|---|---|---|---|---|---|---|---|
| 100 nodes | none | 5 KB | 66 ms | 6 ms | 70 ms | 88 ms | 103 ms | 0 |
| 100 nodes | 4x CPU + 100 ms RTT | 5 KB | 298 ms | 109 ms | 393 ms | 442 ms | 459 ms | 0 |
| 10,000 nodes | none | 228 KB | 168 ms | 4 ms | 168 ms | 274 ms | 338 ms | 3 ms |
| 10,000 nodes | 4x CPU + 100 ms RTT | 228 KB | 568 ms | 111 ms | 582 ms | 1,004 ms | 1,319 ms | 298 ms |

Under a 100 ms RTT the WebSocket mount adds roughly 400 ms on top of page load on the large page (handshake plus mount round trips) before the page is interactive, and the 10,000-node mount blocks the main thread for ~300 ms on a slow CPU. The 84 KB client download is 109 ms of that at 100 ms RTT; the page's own HTML is 2.7x larger than the client on the big page.

## 3. Downstream usage before any catalog deletion

Method: static scan (grep plus AST over `.html`, `.py`, `.js`) of 17 owner-run apps: the seven named in round 1, the three heavy consumers (app A, app B and app C), the scaffold starter and the other owner-run apps.

- 70 of the 205 catalog tags are used at runtime by at least one app; 135 by none. Of the 198 catalog classes, exactly one (`CodeBlock`) is imported directly anywhere.
- Usage concentrates in three apps round 1 did not name: app A (24 catalog tags, 240 uses; its whole chrome is the `app_shell`, `app_sidebar`, `app_header` and `app_content` tags on one line of its base template), app B (23 tags, 275 uses, 135 of them `{% icon %}`) and app C (13 tags, 130 uses). Six of the seven apps named in round 1 use zero catalog tags in production templates; djustlive's use is a DEBUG-only style guide.
- **The round-1 keep/delete table would break these apps.** Of the 45 tags in production use, it keeps 2 (`dj_button`, `dj_select`; `dj_input` has no production site), demotes 3 (`code_block`, `command_palette`, `kanban_board`) and deletes 40. Most of the 40 are replaceable chrome (`card`, `badge`, `empty_state`, `stat_card`, `alert`, `kbd`, `dj_tag`), but `modal`, `sheet`, `tabs`, `combobox`, `toast_container`, `app_shell` and `icon` carry behaviour that app A and app B rely on. The round-1 table also misplaced the form tags: `dj_button`/`dj_input`/`dj_select` are registered in `djust_components.py`, not `_forms.py`, and the `_forms.py` tags it keeps (`dj_form`, `field_error`, `form_errors`) have zero downstream use.
- **The theming coupling is not what round 1 said.** `component_css_generator` parses theming's own `djust_theming/css/components.css`, and no app sets `css_prefix`, so the prefix rewriter has no downstream consumer. The real coupling is `{% theme_head %}` (`theme_tags.py:217-229`) auto-linking the catalog's 327 KB `djust_components/components.css` whenever `djust.components` is installed, which is 7 of 17 apps and every `djust new` project (`scaffolding/templates.py:210,609`); plus app A hand-writing 11 catalog BEM classes and overriding 19 selectors, app B 3 and 19.
- djust.org's public component pages are served from `djust.theming.gallery`, and both app A and app B wrote code to keep the ungated legacy gallery off production.
- Safe to delete now with no downstream user: all of `_advanced.py` (25 tags including tour, map_picker, pivot_table, cursors), all of `_dev_tools.py`, 5 of 7 chart tags, the AI-chat widgets, all 37 `theme_components`/`theme_form_tags`/`theme_pages` tags (7 of 51 theme tags are used anywhere), the duplicate `ui/`, descriptor and Rust layers, `react.py`, and the vendored xterm, Leaflet and highlight.js. About 25 chrome and behaviour tags move to a keep-or-ship-in-extra set. One caveat: private deployments by outside users are invisible to this scan.

## 4. Explicit mode under production topology

Method: 1 uvicorn process; 2 and 4 workers with the in-memory layer; 2 and 4 workers with Redis channel layer and Redis state backend; Postgres 16 sessions throughout; real `websockets` clients (50 and 250 views, one event every 2 s for 60 s) plus a Playwright pass; SIGTERM of a worker mid-run; a `start_async` job in flight.

**Latency.** Explicit with `persist="server"` is 1.8 to 2.0x legacy at p50 in every topology (10.0 vs 5.6 ms at 50 views; 7.3 vs 2.5 at 250), explicit with nothing persisted 1.5 to 1.8x. Adding workers does not lower p50 at these loads, and uvicorn on macOS put 80 to 98% of connections on one worker (201 of 250 on one of four), so "N workers" mostly measured one. p95 stays under 25 ms except Redis with one worker at 250 views (24 to 53 ms). Bytes per turn: legacy 220, explicit 290, explicit with `persist="client"` 623 (a ~330 B signed token rides every ack).

**What survives what.** Decided solely by whether declared state goes into the shared Django session.

| view kind | reconnect, same worker | reconnect, other worker | worker SIGTERM | in-flight `start_async` result |
|---|---|---|---|---|
| legacy, default | lost | lost | lost | lost |
| legacy, `enable_state_snapshot` | restored 10/10 (public and private attrs) | restored 10/10 | restored 10/10 | lost |
| explicit, `state()` only | lost | lost | lost | lost |
| explicit, `persist="server"` | restored 10/10 | restored 10/10 | restored 10/10 | lost, view stuck on `running` |
| explicit, `persist="client"` | lost | lost | lost | lost |

- `persist="client"` loses state on a plain reconnect because the shipped client never sends the `state_snapshot` token on a reconnect mount (`03-websocket.js:1484-1498`); only navigation does (`18-navigation.js:274,473`). When the token is echoed, the server restores 10 of 10. Browser run: count 3 became 0.
- **An in-flight `start_async` result is lost after any reconnect or restart in every topology**, and a view that persisted its loading flag comes back stuck on `result=running` forever: `websocket.py:1394` captures the view, `:1414-1420` drops the completed work on the identity guard, and `:2657` nulls `view_instance` on disconnect. This is the round-1 "background operations with retained result" capability, measured as absent.
- The Redis state backend changes no outcome: explicit views write zero keys to it; legacy views write a 1.1 KB diff baseline per view.
- Worker restart: uvicorn respawns in 0.5 to 1.1 s, clients re-mount in ~1.0 s, no events lost, and the first post-restore frame is a forced full `html_update` that also logs a DJE-053 warning every time (`runtime.py:3955-3975`).
- Per-view memory, clean runs: 3.2 to 3.3 MB per connected view of a 50 KB page at 1,000 views (legacy and explicit identical), 11.1 MB per view of a 167 KB page, consistent with round 1's slope. The 5,000-view runs ran with host swap full and are not trustworthy.
- Simulated RTT adds exactly the delay; the client has no per-event timeout, so nothing else happens.
- Not measured: a second node, actors, SSE, HTTP fallback, cross-process `push_to_view`.

## 5. Template-engine delegation

Method: Rust render phase versus whole turn on 82 demo pages and 32 to 45 djust.org pages over real GETs, mounts and events; crate LOC inventory; a prototype branch (`experiment/delegate-cold-filters`, cut from `origin/2.0` because the #3549 routing mechanism does not exist on `main`; three commits, not pushed, red on 73 Python tests).

- The template phase is 10% (p50) of a demo GET and 6% of a mount; 24% and 13% on djust.org. Two djust.org pages are template-bound: `/components/` spends 237 of 267 ms in Rust per mount (89%), and the cost is `for`/`if`/`include` over a large context, not filters.
- The crate is 39,673 lines, ~28,000 non-test. 18 of 57 built-in filters are used by any real template; 19 were already routed to Django on 2.0 (#3549 and the next commit); all library tags are already delegated (#2547).
- Prototype: routing 15 more unused filters and deleting their helpers removes 825 production lines and 435 test lines (1,260 total); Django conformance unchanged at 1032 of 1047 with an identical failure set; page render time unchanged within noise; crate lib tests 494 pass. But 73 Python tests fail: ~45 are source-shape count pins, ~28 are real behaviour.
- Three facts decide what is routable: a Python round-trip costs ~2 µs per filter call (`cut` 0.39 to 2.33 µs), so route by uses per render; the bridge lacks Django's `expects_localtime` step and the wire carries datetimes as strings, so `time`, `timesince`, `timeuntil`, `date` and `pprint` must stay native (routed `time` on the wire string renders empty); routing removes djust's own DoS guards (the `ljust` pad cap; routed `dictsort` tripped the differential harness's memory ceiling on the #2695 "liar" grid).
- Verdict: 12 filters (`cut`, `last`, `random`, `default_if_none`, `escape`, `force_escape`, `escapejs`, `dictsort`, `dictsortreversed`, `ljust`, `rjust`, `center`) are cold and routable with the caps re-added; 23 of 57 stay native. Delegation reduces maintenance, not turn time. The cold built-in tags total ~560 lines and were inventoried, not prototyped.

## 6. Security of the explicit path and HTTP fallback

Method: 54 empirical probes (real consumer over `WebsocketCommunicator`, real SSE views, Django test client with CSRF enforcement, DB sessions, `DEBUG=False`) against exposure bypass, snapshot tampering and replay, authorization re-check on every entry point, HTTP fallback parity, encoded-bypass variants, information leaks and unbounded inputs. Final run: 54 passed, where a pass is a control holding or a documented behaviour recorded.

- No high or medium finding.
- Two low-severity defects were found and are fixed in djust-org/djust#3661: the HTTP fallback accepted an event name that resolved to no handler on the page view (answering 200 with a render and a state save instead of the 400 the component branch and the socket transports return), and `view` was accepted as a `state()` field name although the explicit render context reserves it.
- Informational, by design: a same-principal older signed client snapshot replays within the 3,600 s max age (no nonce); the HTTP fallback never carries `persist="client"` fields between POSTs; two tabs are last-writer-wins on `persist="server"`; the SSE queue is unbounded (known); `DEBUG=True` error frames carry exception text (ADR-038 D-a).
- Held, each with a named probe: undeclared fields never reach the wire; validly signed envelopes with an extra key are rejected whole; params, `component_id` and `view_id` hops cannot write state; snapshots are bound to session, user, route and class, with tamper, expiry and key-rotation handling; fresh per-turn authorization on WS events, `url_change` and background results after session deletion, logout or permission revocation (socket closed 4403); object permission re-checked per event; SSE owner binding and Origin policy; HTTP CSRF and cross-origin rejection; rate-limit bucket shared across WS and HTTP; strict-parameter parity; 19 event-name, 14 mount-URL and 12 view-path encoded variants refused; value-free errors and logs in production; 70 KB tokens rejected before signature work and 30,000-deep params answered with an error frame while the socket stays alive.
- Not probed: browser, cache or file session backends, real multi-process restart, tenancy, explicit child persistence, `migrate_state`, actor views.

## 7. Test suite audit

- Gate-off of 50 tests (seed 20261028, 40 stratified random plus the 10 newest): 39 load-bearing, 4 tautological, 6 could not gate (Rust-backed, no rebuild), 1 baseline invalid. Tautology rate 9.3% of gated tests; the four are positive-path-only tests and one decorative pin shadowed by a second mechanism.
- Time: 49,923 collected ids; 22% of them in 8 parametrized theming and parity sweep files (`test_theming_contrast_all_presets_2060.py` alone has 4,648). The fast tier runs in 152 s wall on 12 cores (1,061 CPU-s); the 781 tests over 1 s carry 82% of the 5,600 recorded serial seconds.
- CI: a PR run is 38.7 job-minutes (6.0 min wall); a main push is 156 job-minutes (16.3 min wall) because the full suite runs on four interpreters × four shards. Of the last 33 main pushes, 21 were red (10 security-scan, 14 a python-tests shard, 3 overlapping) with no revert or release safety net. Pre-push selection answered FULL on 26 of the last 30 main commits.
- Untested surface: 28 of 36 Playwright files (all the #2985 catalog hook batches) never run in CI; the debug panel's 20 client modules have median 23% line coverage with 5 at 0%; 65 of 181 catalog component names (`DataTable`, `KanbanBoard`, `TreeView`, `PivotTable` among them) are never instantiated by a test, though sweeps execute 187 of 208 catalog modules to 80% or more of lines.
- Duplication is modest: 65 cross-file groups of identical assert sets (240 tests).
- Proposal: fast tier plus path-gated cargo and vitest at pre-push; PR CI cut to ~30 job-minutes; post-merge full suite on 3.12 only; interpreter matrix, benchmarks, Windows, Playwright and JS coverage nightly. Saves ~557 job-minutes per day (41%) without removing a check from a blocking tier.

## 8. Canon accuracy

- `CLAUDE.md` is byte-identical on 2.0 and main: 1,751 lines, 80% in 35 canonicalization sections holding 114 rule bullets. Its 19 `file:line` citations hand-checked on 2.0: 1 exact, 17 drifted, 1 dead (`websocket.py:2856` component routing moved to `runtime.py:6284`).
- Across all 801 `path:line` citations in scope on 2.0: ~25% exact, ~57% drifted, ~18% dead by classifier, with a manual calibration showing dead over-counted by about a quarter. `docs/SECURE_DEFAULTS.md`'s serialization "canonical site" block is off by 100 to 370 lines.
- Every one of 2,458 backticked identifiers still resolves (the 7 that do not are ones the text says were removed). One of seven bash blocks in `CLAUDE.md` is a verbatim syntax error (the `git ls-files --error-unmatch` block at L922-925). All 20 named make targets exist.
- 40 of 40 sampled issue and PR numbers exist; 36 titles match. The 4 mismatches are Action-Tracker rows, a private-repo PR and a Dependabot alert id colliding with unrelated public issues.
- Only 3 of 114 rules have instance tables in `docs/patterns/`. 23 rules have zero recurrence evidence since canonization; ~25 have explicit retro reinforcement, led by #1646 parallel-path drift (recurred 21 times) and Action #122 (eighth reinforcement).
- Recommendation: keep ~290 lines live (the 220 non-canon lines, a 25-line triage block pinned by symbol not line, a 20-line rule sheet) and move every canon section to `docs/patterns/` class pages with instance tables, demoting the 23 zero-evidence rules to wiki-only.

## What this changes

Round 1's conclusions stand; four of them get sharper and one gets reversed.

1. **The onboarding path is broken twice, not once.** After the `template_string` fix, the README's `asgi.py` still serves no static files, so the documented "add to an existing project" flow ends with a dead counter. Fix the README `asgi.py` (wrap in `ASGIStaticFilesHandler`), land PR #3652, and add a Playwright check of the README flow to CI.
2. **"Background operation with a retained result" is not a capability gap to add; it is a defect to fix.** An in-flight `start_async` result is lost on every reconnect and restart in every topology, and persisting views stay stuck on `running`. This belongs in the 2.0 acceptance matrix as a hard gate (round 1 item C5 and plan direction item 4).
3. **`persist="client"` does not do what it promises until the client echoes the token on reconnect.** One client change (`03-websocket.js:1484-1498`) turns a 0 of 10 into a 10 of 10.
4. **The catalog deletion plan needs a different keep list.** Three owner apps depend on ~25 chrome and behaviour tags the round-1 table deletes. Delete the 135 unused tags, the duplicate layers and the vendored bundles now; decide the 25 with app A and app B as the test cases; and stop `theme_head` from auto-linking 327 KB of catalog CSS into every scaffolded project.
5. **Reversed: engine delegation is a maintenance win, not a performance win.** Round 1 listed it under optimizations. The template phase is 6 to 24% of real turns and the template-bound page is bound by loop and include rendering over a large context. Route the 12 cold filters with the DoS caps re-added; leave speed work to the Python state walks from round 1.
6. **Client fixes have measured sizes now.** `_scanScopedElements` is the one page-proportional client cost (4 to 5 ms of 7.4 at 10,000 nodes); the Chart.js adapter should default to attribute-gated updates; the focus-steal should be fixed as a user-visible bug; the "No hook registered" race should be fixed by deferring init until extensions register.
7. **Explicit mode's 1.8 to 2x cost is topology-independent**, which confirms it is the per-turn ceremony from round 1 item 4, and it is the single biggest performance item for the future default.
8. **Process: 64% red main pushes and a FULL-on-everything selector mean the suite's cost is not buying stability.** The tiering proposal and the canon move are both mechanical.

## Verification record and cleanup

Eight reports, eight skeptics, zero refutations. Corrections applied above include: the browser-cost run actually completed all 18 pages (the agent reported it interrupted) and the 10,000-node slope is superlinear; the downstream keep list is `dj_button` and `dj_select` only; the security report's `csrf_exempt` count was wrong (nine sites in observability and the theming inspector, none on the HTTP fallback); the canon report's second "dead" citation was a drift; the engine report's LOC method undercounted production code after the first `#[cfg(test)]` and the #2695 case was a memory-ceiling trip, not a hang; the topology report's "re-mounted on the dying worker" note was a harness artefact.

Experiment branches and raw results are retained privately.
