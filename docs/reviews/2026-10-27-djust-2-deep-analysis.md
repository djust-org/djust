# djust 2.0: deep analysis — missing optimizations, adoption, wrong assumptions, JS strategy

> **Scope note.** Measurements in this document were taken on commit `9a19cf95d` of the 2.0 branch before it was rewritten on 2026-10-28. Several round-1 items have since landed on `2.0` (a bounded fingerprint budget, a template-source cache, an opt-in hook `shouldUpdate()` and attribute-gated Chart.js updates; see [2026-10-28-djust-2-next-priorities.md](2026-10-28-djust-2-next-priorities.md)). Raw per-investigation reports, verification verdicts and harnesses are held in a private evidence archive; this public copy omits private downstream application code and unpatched security detail.

Date: 2026-10-27. Tree: branch `analysis/2.0-deep-dive`, cut from `origin/2.0` at `9a19cf95d`. Companion to [2026-10-27-djust-2-strategy.md](2026-10-27-djust-2-strategy.md), which this report extends with measurements, external evidence and an independent verification pass. Nothing in the tree was changed except this file; supporting scripts, raw results and the four source reports are held in the private evidence archive.

**Method.** Four independent investigations ran against this tree: a whole-turn profile over real WebSocket turns with a release-built extension, an external adoption and competitor study, an audit of stated assumptions against code and measurements, and a component-catalog and interop inventory. Twelve load-bearing claims were then handed to twelve separate skeptics instructed to refute them from source. None was refuted; five were corrected in number or mechanism, and the corrected figures are what appears below. Every number is from a command recorded in the source reports.

## 1. The finding that reorders everything

djust has no external feedback loop, and the one path a newcomer takes is broken.

| Signal | Value |
|---|---|
| GitHub stars / forks (repo created 2026-01-16) | 101 / 10 |
| Issues: by owner / by bots / by outside users | 1,623 / 9 / 14 (five people; eight from one user, all about bundled components not working) |
| PRs: by owner / Dependabot / outside | 1,754 / 223 / 15 |
| Repos outside the org importing djust | 4 (two are benchmarks) |
| Django Forum alpha thread | 1 like; one reply, from a reviewer who could not run the examples and received no reply |
| Repo views, last 14 days | 195 (148 unique) |
| PyPI downloads, last 30 days | ~20k, of which ~16k report no OS or Python version (CI, not users) |
| README hero example | Does not run: `template_string` is not a LiveView attribute; it raises `ValueError: Either template_name or template must be set` on GET and on WS mount. Present on both `main` and `2.0`. |

Nine months, 156 releases, 3,166 commits, 18,245 Python tests, and a 1,751-line process canon have produced roughly one engaged outside user and one public negative review. The project does not yet have an adoption-feature problem; it has a trust-loop problem, and every capability question below is conditioned on fixing that first. The cheapest moves are not code: make the README run, execute README snippets in CI, answer the open cotton discussion (unanswered since August), and recruit three Django teams to add one feature each.

The Django market has also already chosen. The 2026 State of Django survey (3,500 respondents) has htmx at 34% (from 5% in 2021), Alpine 14%, server-rendered templates 72%, and Django 6.0 shipped template partials, CSP and `django.tasks` in core. Every LiveView-for-Django attempt in six years (Unicorn, Reactor, Django LiveView, sockpuppet) stalled under 2.7k stars and 10k downloads/month; the Python-only UI winners (Streamlit, Reflex, NiceGUI) are not Django and sell to a different buyer. django-htmx does 1.3M downloads/month; django-cotton, a template-syntax package with no runtime, reached 276k/month in two years. Datastar, the closest competitor to djust's actual value (server-driven, real-time, small client), does it with zero server-side state, 11.9 KiB, and any backend.

## 2. Optimizations we are missing

Measured on real `LiveViewConsumer` to `ViewRuntime.dispatch_event` turns, release extension, `DEBUG=False`, memory state backend, DB sessions, 200 turns per cell. Medians; the laptop was shared, so ratios within a row are the evidence.

| One event turn | wall p50 | server turn | Rust render+diff | Other Rust (state convert + msgpack snapshot) | Python |
|---|---|---|---|---|---|
| counter, legacy | 1.72 ms | 1.38 ms | 0.02 ms | ~0.2 ms | ~1.2 ms |
| counter, explicit, no persist | 4.48 ms | 5.0 ms | 0.03 ms | ~0.2 ms | ~4.8 ms |
| counter, explicit, `persist="server"` | 4.74 ms | 6.1 ms | 0.03 ms | ~0.2 ms | ~5.9 ms (2.4 ms session write) |
| 1,000-row table, one cell changed | 20.1 ms | 25.0 ms | 3.1 ms (text fast path) | 4.6 ms | ~17 ms (67%) |
| 1,000-row table, cell + counter text | 31.0 ms | 37.9 ms | 11.3 ms (full parse + diff) | 4.7 ms | ~22 ms |
| 10-field form, keystroke validation | 4.28 ms | 4.4 ms | 0.34 ms | ~0.2 ms | ~3.4 ms (0.65 is user validation) |

Rust render and diff is 1 to 30% of a server turn depending on scenario; Python framework work is 60 to 95%. Wire bytes are not the problem (160 to 300 B per frame at any list size; msgpack would save 35% of ~0.02 ms encode). There are exactly 12 Python-to-Rust crossings per turn in every scenario; the count is fine, what crosses is not.

The prior review listed eight items (fingerprint budget, template-property invalidation, unscoped client re-init, legacy-only push path, full-HTML serialization, no-op persistence, template cache bound, SSE queue). None of the following is on that list.

1. **One container change is walked four times in Python, then re-converted whole in Rust (~17 ms of a 20 ms single-cell turn).** Pre-event snapshot (`runtime.py:4938`), post-event snapshot (`:5059`), fingerprint-and-convert in `_sync_state_to_rust` (`mixins/rust_bridge.py:946-962`, stored at `:1077-1081`), `_collect_sub_ids` (`:980`), then `update_state` (`:1331`, `lib.rs:525`) extracts every list item again. cProfile per turn: `_walk` 2,084 calls 8.9 ms, fused convert 1,001 calls 7.9 ms, `_collect_sub_ids` 6,002 calls 3.5 ms, Rust `update_state` 2.3 ms. Fix: reuse the post-snapshot fingerprints as the sync's new fingerprints when the context value `is` the attribute; skip `_collect_sub_ids` for plain containers; send item-level deltas to Rust for keyed lists. Expected 40 to 60% off list-page turns. Medium risk (#2664/#774 class; needs gate-off siblings).

2. **Context-processor output is re-sent to Rust and re-validated on every turn of every view (~0.45 ms).** `rust_bridge.py:1012` and `:1036` treat keys absent from `_prev_context_refs` as new, and `_prev_context_refs` deliberately excludes request-scoped keys (`:1066-1068`, #1786), so processor keys are new forever: 9 keys and 14,546 bytes (`theme_presets` is 14,445) go through `_is_json_serializable` (~480 calls), 71 converts and `update_state` on a bare counter, legacy and explicit alike, and `theme_context` recomputes `get_available_presets` each call. Fix: fingerprint request-scoped serializable values like any other key (keep them out of persistence, not out of change detection) and memoize the preset list per registry generation. Low risk.

3. **The legacy state backend msgpack-serializes template + full state + full VDOM after every render (2.1 to 2.4 ms, ~10% of a table turn, 283 KB per call).** `template.py:2075` to `rust_bridge.py:531` to `state_backends/memory.py:292-301` to `lib.rs:1323`. The only consumer is another socket of the same session. Fix: dirty flag, serialize lazily on the next cross-socket `get()`, debounce for Redis. Low to medium risk.

4. **Explicit views with nothing to persist still pay the full ceremony (~2.3 ms, making an explicit counter 2.6x a legacy one).** `commit_explicit_turn` 0.77 ms, `_explicit_event_snapshot` 0.71 ms (two `sync_to_async` hops when nothing is client-persisted, `runtime.py:7299-7305`), `fresh_socket_request` 0.51 ms loading the DB session per event (`_exposure_auth.py:85-101`), `aauthorize_event` 0.33 ms; `ExposureContract.from_view_class` is rebuilt twice per turn with no cache (`_exposure_sessions.py:170`, `_exposure_snapshots.py:28`). This is the future default, so it matters more than any legacy number. Fix: cache the contract per class; short-circuit commit and snapshot when no persistence is declared; reuse the turn's loaded session for the save. Low risk.

5. **No-op work still pays a thread hop.** `_legacy_event_snapshot` (`runtime.py:7255`) hops to return `{}` for views without `enable_state_snapshot` (0.10 to 0.37 ms per turn); the handler hop costs 0.11 ms for `count += 1`. Legacy turns make 4 hops, explicit 7 or more. Evaluate cheap gates on the loop before hopping; fold snapshot work into one hop (#3200 rule).

6. **The parameter-contract manifest is rebuilt on every render frame** (`runtime.py:7967` to `_parameter_metadata.py:379`, 103 `_resolve_method` calls) and when non-None rides every frame so the client re-freezes all handler maps (`08-event-parsing.js:457-498`). Compute at mount, resend on owner-set change.

7. **Two text changes in one turn lose both Rust fast paths (+9 ms, 31 vs 20 ms).** The fragment path needs every changed fragment tag-free and the whole-HTML path handles one text span (`lib.rs:5041`), so any list page where a cell edit also updates a counter or toast pays html5ever on 128 KB plus a full diff. Fix: an N-region text path or per-item loop fragments. Medium risk, contained in Rust.

8. **Client-side O(page) work per patch application beyond `reinitAfterDOMUpdate`:**
   - `12-vdom-patch.js:3321` (and a twin at `:3304`): `if (!focusState || !focusState.id)` is always true because `saveFocusState` (`:265-313`) never sets `id`, so `querySelector('[autofocus]')` runs on every patch application; when a form field is focused and an `[autofocus]` element exists elsewhere, focus is stolen and restored, emitting spurious blur and focusout events. A real bug, not just cost; the existing test for this case is tautological.
   - `09-event-binding.js:349-457` `_scanScopedElements`: `querySelectorAll('*')` times every attribute on every bind.
   - `02-response-handler.js:45-49, 404-419`: `clearOptimisticPending` two to three times per frame plus two document scans.
   - `12-vdom-patch.js:401-440` `getNodeByPath`: `SetText` patches carry no `dj-id` (`crates/djust_vdom/src/lib.rs:462`), so each text patch filters `childNodes` at every ancestor; ~2k nodes at a `<tbody>`. Fix: parent `dj-id` plus index.
   - `49-activity.js:147` listens for an event nothing dispatches.

9. **Per-view memory is linear in page size and about double what the Python heap shows.** Real WS views, 2,000 rows (99 KB page): 3.6 MB retained Python heap per view, of which 74% is *two* equal fingerprint tuple trees of the row data: `_prev_context_fingerprints` (`rust_bridge.py:1077`, 1.33 MB) and `_dirty_baseline` (`live_view.py:1094`, 1.33 MB), each 2.3x the size of the rows themselves (0.57 MB). The Rust view retains another ~3.2 MB per view (state, last VDOM, last HTML, node HTML cache, fragment map; `lib.rs:254-294`), invisible to tracemalloc. Total ~6.8 MB per connected view of a 100 KB page; the planning figures in `docs/website/guides/scaling.md:128-132` (0.4 to 1 MB) come from a small page. Fix: one bounded hashed digest instead of two retained tuple trees, or in explicit mode fingerprint only declared `state()` fields; publish per-view memory as a function of page size and add a ceiling to the 2.0 acceptance matrix.

Order of attack: 2 and 5 (small, every turn, low risk); 4 before any default flips to explicit; 1 and 3 for list pages; 7 in Rust; 8 starting with the focus bug; 9 as the 2.0 memory story.

## 3. Capabilities that would bring users

Ranked by external evidence, highest confidence first. The prior review's five hypotheses survive; the evidence reorders them and adds non-code moves that outrank most code.

**Capabilities**

1. **A guaranteed-working 15-minute path**: one scaffold, one tutorial, one table + form + detail app, browser-smoke-tested on every release, and the README executed in CI. Evidence: the broken hero example; the forum reviewer; the "will there be a beginner tutorial" discussion; all eight outside issues being first-contact failures on bundled components. Official docs are the number one learning resource for 67 to 79% of Django developers. Confidence high.
2. **Coexistence with the htmx + Alpine + cotton + Tailwind stack rather than replacement**: a `dj-root` island on a page that otherwise uses htmx; third-party tags (cotton, django-components, Django 6 partials) rendering through the Rust engine or falling back transparently, with a system check that says why a tag is unsupported and how to bridge it. Evidence: the one substantive outside question was "does this work with cotton?"; partials are now core syntax. Confidence high.
3. **HTTP-first default, WebSocket as the upgrade.** `pip install djust`, `INSTALLED_APPS`, a URL, and WSGI `runserver` should work; Channels, a channel layer, a hand-written `asgi.py` and Redis should be an opt-in guided by a system check. Evidence: r/django's answer to "why has LiveView never resonated with Django" is that Channels is second-class and the infrastructure is the barrier; Unicorn beat Reactor on simpler infra; Livewire does 10M/month on HTTP only. The 2.0 plan already settled this direction (item 3) but the README's step 1 is still "add channels and a channel layer", and today HTTP never runs `start_async` (`mixins/request.py:61-82`), SSE sessions are process-local (`sse.py:73`), and push, tick and db_notify are WebSocket-only. Confidence high.
4. **Reliable reconnect, restart and multi-worker state with a published topology table** ("one process / N workers / N nodes: what survives what"). Evidence: Phoenix LiveView's number one operational complaint is state loss on reconnect, spawning LiveStash; djust's own tracker has HTTP-fallback and snapshot bugs. Already 2.0 direction items 3 and 4; deliver through minimal Redis and DB adapters. Confidence medium.
5. **Background operations with progress, cancel, reconnect and a retained result, built on `django.tasks`.** Evidence: Django 6.0 shipped `django.tasks` with no worker; 33% use async and 40% plan to; the AI-app streaming use case is where SSE is growing. Adapter from `start_async` and `@background` to task backends plus one documented "LLM token stream with cancel and resume" example. Confidence medium.
6. **The Rust template backend as a standalone drop-in for ordinary Django views.** It is the one djust capability that no Django competitor offers and that a buyer can want without wanting LiveView at all; Django developers rewarded Rust when it was a transparent drop-in (ruff 43%, uv 43%). Needs its own install path, README and conformance badge, zero LiveView vocabulary, and the tag support in item 2. Confidence medium.
7. **AI-agent legibility as a measured gate**: keep `llms.txt`, `djust ai` and the MCP server, and turn the ADR-043 experiment with app A into a recurring check that a fresh agent starting from `pip install djust` builds a working app. 90% of Django developers use AI tools and AI is their second learning resource. Confidence medium.
8. **A "multiplayer in 20 lines" packaged demo** (presence, cursors, shared room clock) deployed and linked. Datastar grew on exactly these demos; djust already has `presence.py`, clocks and cursors; the client currently drops `presence_event` frames (#3501). Low build cost, high demo value. Confidence medium.

**Go-to-market (outranks most code above)**

- Reposition from "no JavaScript / Rust-fast / 80 KB client" to "interactive Django without leaving Django". Every current headline number loses to htmx (16 KB) and Datastar (12 KB) on the comparison a Django developer makes, and the latency benchmark that was posted to r/django was dismissed as "sounds like magic". The wedge the market rewards is "Django all the way down": forms, admin, auth, templates, partials, CSP nonces. Make that literally true and lead with it.
- Fewer releases and a visible stability pledge. 156 releases in nine months, 14 RCs for 1.3.0, and a 2.0 that removes APIs read as "not settled"; the only third-party coverage of djust on the open web is four CVE database entries. A security page that frames the audit as a strength, plus a monthly stable cadence.
- Answer every outside issue and discussion within 48 hours. The reactor author's public "I don't have time to maintain it" is how Django LiveView clones die.
- Only after the README runs: Django News, djangopackages grids, one podcast, one Show HN with the multiplayer demo.
- Recruit three to five Django teams to add one feature and publish time-to-first-feature.

## 4. Wrong assumptions

Severity: S1 contradicts something a new user hits on day one; S2 contradicts a plan premise; S3 real but bounded.

| # | Assumption | Stated at | Evidence against | Sev | Instead |
|---|---|---|---|---|---|
| 1 | Rust makes djust fast | `README.md:5,48,199-214`, `pyproject.toml:8` | Rust render+diff is 1 to 30% of a turn; a one-cell change runs ~1,074 distinct Python lines; 248,634 non-test Python LOC vs 79,201 Rust | S1 | Market the patch stream (160 B/frame at any list size) and whole-turn latency; make per-turn Python work the KPI |
| 2 | The 7 to 11x benchmark is "a typical page" | `README.md:195-210` | One `<ul>{% for %}` over a plain object, 10k rows, no extends/include/ORM/request (`benchmarks/benchmark.py:88-103`); static markup is 1.0x | S3 | Retitle it; put the whole-turn table beside it |
| 3 | "No JavaScript to write" | `README.md:7-9,37,46,49` and 19 places | 138 distinct `dj-*` client directives (42 documented), 130 `window.djust.*` names, hooks, JS commands, extension sockets; 41% of docs files discuss JS interop | S1 | "No frontend build, no duplicated business logic; a declarative client, hooks when needed"; reconcile the 96 undocumented directives |
| 4 | "Tiny client" | `README.md:49,236` | 81.9 KB gz is 5x htmx (16.4), 5x Alpine (16.2), 2.6x Phoenix LiveView (31.4), 1.9x Turbo (42.1); every one of 60 modules ships to every page (a counter downloads virtual-list, uploads, SSE, navigation, service-worker registration, tutorial bubble, cursor overlay, audio) | S2 | Drop "tiny"; lazy-load by `dj-*` presence (the init scan already knows); the plan's post-split 50 KB estimate is the earliest honest number |
| 5 | Users want a batteries-included catalog | ADR-043:9, `2.0.0-plan.md:91,109`, `ROADMAP.md:19` | 69,848 LOC (28% of non-test Python), 198 classes, 205 tags, 11,089-line Rust-engine twin, 2.1 MB static; zero `{% load djust_components %}` in examples; 90 of 211 modules never imported by any test; the only outside user's issues were catalog handlers that do not exist | S2 | See section 5 |
| 6 | Per-connection state scales at ~0.4 to 1 MB/session | `scaling.md:128-132`, `deployment.md:63-70` | ~6.8 MB retained per view of a 100 KB page (3.6 Python + 3.2 Rust), linear in page size | S1 | Bounded digest; publish memory as a function of page size; ceiling in the acceptance matrix |
| 7 | The fingerprint budget bounds work | `change_detection.py:27,43` | Time unbounded (prior review) and memory unbounded: two 1.3 MB tuple trees per view at 2,000 rows, well under budget | S1 | Same fix as 6 |
| 8 | Channels + WebSocket is the right default and HTTP is a fallback | `README.md:51,64-141`, `pyproject.toml:44` | Setup needs channels, ASGI, a channel layer, Redis twice for multi-process; HTTP drops `start_async`; SSE needs sticky routing; push/tick/db_notify WS-only | S2 | HTTP + SSE zero-config default, WS as upgrade; `channels` as an extra (#2560 already decided it) |
| 9 | The template engine must be a Django clone | `README.md:194`, `TEMPLATE_COMPATIBILITY_LIMITS.md:7` | 39,673 Rust LOC (half of all Rust) plus 11,089 + 3,653 + 1,623 Python for a phase that is 1 to 30% of the turn; the parity arc consumed v1.2.0 and produced its own bug class | S2 | Speed on the hot subset; delegate cold behaviour to Django (#3549 started this); stop quoting 98.57% as a feature |
| 10 | Process rigor scales the project | `CLAUDE.md` (1,751 lines, 80% retro canon, 122 rules, 357 issue refs) | 98.6% of issues are self-filed; 65 drain-bucket headings; canon documents its own fix-chains; yet the README does not run and 43% of components are untested | S2 | Move canon to `docs/patterns/`, keep under 300 live lines; add the one missing gate: "the README runs"; measure drains by outside bugs closed |
| 11 | The README example works | `README.md:20-42` | `template_string` is read by no LiveView mixin; raises on GET and WS mount; with `template =` fixed and `exposure_policy="explicit"`, `count` renders empty unless added in `get_context_data()` (`state()` alone does not expose it) | S1 | Fix now; rewrite in explicit form for 2.0; execute README snippets in CI |
| 12 | Explicit exposure is what users want, and 1.4 must bridge them | `2.0.0-plan.md:13,49`, delivery plan Phases I/B/M | Four outside repos import djust; all named downstream apps are owner-run | S2 | Do the flips; shrink the 1.4 ceremony to the inventory command and a migration note; drop the codemod and gate G1 unless a non-owner app appears |
| 13 | AI positioning is load-bearing | ADR-043 | `mcp` + `ai_discovery` are 2,702 decoupled LOC; the load-bearing fact is inverse: `docs/ai/` exists because 138 directives, 110 config keys, 133 checks and 23 LiveView bases are too much surface for a head | S3 | Keep it; treat "an agent needs 1,052 lines of hints" as a surface-area metric |
| 14 | Python 3.11+ / Django 4.2+ | `README.md:16-17` | CI tests only 3.12 to 3.15 and the one locked Django 5.2.17; code gates on `django.VERSION >= (5,1)` | S3 | Declare what CI tests |
| 15 | "Resilient transport" restores state on reconnect | `README.md:51` | Needs `enable_state_snapshot` (on the 2.0 removal list) or `persist="server"` plus shared sessions; component state is transient | S3 | "Reconnects, with declared state restored"; make R3 a hard gate |

Assumptions that held: wire traffic is small; the text fast path works on every text-only event; capacity *is* documented (just on a small page); the conformance number is honestly computed; `@optimistic` and `@client_state` are inert as stated; client size claims are mechanically pinned; the three 2.0 flips (explicit exposure, strict params, one transport core) are sound. The problems are sequencing and surrounding premises, not the flips.

## 5. Own JS components, or an interface to existing ones?

**Interface. Keep about nine native pieces, delete or demote the rest, finish the contract that already exists.** The prior review's direction survives the inventory and the competitor comparison, and is understated.

What exists: a 198-class, 69,848-LOC catalog in seven layers (widget classes, `ui/` simple + LiveComponent twins, descriptors, 205 Django tags, 194 Rust-engine `*Handler` twins of ~189 of those tags, a 3,899-LOC Rust crate used by only 16 `ui/*_simple.py` files, 40,824 LOC of theming that parses the catalog's CSS). A Button exists in seven artifacts, five or six of them independent render paths. The catalog already vendors the libraries an adapter strategy would target: a 545 KB Tiptap/ProseMirror bundle, Leaflet (150 KB), xterm (347 KB), highlight.js (165 KB). No shipped example loads a single catalog tag; the demo project imports 18 catalog classes.

What competitors did: every server-driven framework that grew shipped a small app-owned foundation plus a widget-ownership primitive and a hook API, and no first-party catalog (Phoenix hooks + `phx-update="ignore"` + `pushEvent` with reply; Livewire `wire:ignore` + bundled Alpine; Hotwire Stimulus; htmx nothing; Datastar `data-ignore-morph`). The only catalog that makes money, Flux UI ($149 to $799), is a separate product by the Livewire author sold after adoption. The two frameworks that wrap a JS catalog (NiceGUI on Quasar, Reflex on React) serve Python-only developers who never touch HTML, which is not djust's buyer.

What djust already has: the integration contract is ~80% present. `dj-hook` with `mounted/beforeUpdate/updated/destroyed/disconnected/reconnected`, `this.values.*` JSON-first props and `this.target()` (ADR-025, shipped), `this.pushEvent`, `this.handleEvent`, JS commands and `JS.ext`, colocated hooks with CSP nonce, `dj-update="ignore"`, `dj-ignore-attrs`, `dj-sticky-view`, and an official Chart.js adapter. ADR-020's `dj-island` was never implemented (zero hits in `python/` and `crates/`); `djust.react` matches only a Counter.

Four specific gaps, all verified from source:

1. `pushEvent` (`19-hooks.js:89-106`) builds its own frame with no `ref`, so hook events get no ack, no reply promise and no HTTP fallback, unlike the `handleEvent` to `sendEvent` path (`11-event-handler.js:348-399`). Worse: because the reply has no ref, `acknowledgeEventRequest` (`04-cache.js:122-131`) can attribute it to an unrelated pending `sendEvent` promise.
2. `dj-update="ignore"` has three semantics: Rust diffs host attributes and skips children, testing the new node (`diff.rs:225-233`); the JS full morph skips the whole element including attributes, testing the existing DOM node (`12-vdom-patch.js:1347-1350`). Props freeze on the natural reading; the Chart.js adapter works around it by never putting ignore on the host.
3. `destroyed()` fires after detach (post-patch sweep); `beforeUpdate` gets no `toEl`; a lazy `import()` resolving after `destroyed()` leaves a zombie.
4. Extension scripts are emitted without a nonce (`mixins/post_processing.py:478`), so strict-CSP pages reject adapters.

**Recommendation**

- Keep native: Django-form-bound widgets with error binding (`templatetags/_forms.py` and its 7 handlers), the FK/autocomplete select bound to a QuerySet, the QuerySet data table (make it URL-backed; `mixins/data_table.py` has no `handle_params`/`live_patch` today while `mixins/navigation.py` does), dialog/popover/disclosure with focus management, flash/toast, and the `Component`/`LiveComponent`/`@component`/`dj_suspense` base. About 7 to 8k LOC.
- Delete: cosmetic and layout widgets, all duplicate layers (`ui/*_simple`, `ui/*Component`, descriptors, matching `rust_handlers`, `crates/djust_components/src/simple/*`), `react.py` and `initReactCounters`. Demote gallery, tour, kanban, dashboard grid, file tree, log/json/diff viewers and wizards to examples or a `djust-components-extra` package carried for one release.
- Replace with adapters: rich-text and markdown editors to **Tiptap** (removes the 545 KB vendored bundle), map picker to **Leaflet**, charts to **Chart.js** (exists). Those three are the first-party set; AG Grid, Tabulator, SortableJS, xterm, signature pads and croppers become recipes. Cap at three; each is a maintenance track against an upstream cadence.
- The contract, as an evolution of `19-hooks.js`: `dj-hook` + `dj-hook-src` (lazy, same-origin, nonced `import()`) + `dj-hook-value-*` props down (diff as `SetAttr`, no JSON script blocks, so no #1848 issue) + `dj-hook-on="change:handler"` events up + `dj-update="ignore"` on an inner owned target, never the host. Route `pushEvent` through `sendEvent` and return the promise; let `@event_handler` return a dict that becomes the promise value (Phoenix `{:reply}` parity, pinned as a wire contract per #1448). Align the morph to Livewire semantics: sync host attributes, skip children. Call `destroyed()` before detach; add `beforeUpdate(toEl)`; discard a hook destroyed before its import resolves. A `djust.adapters.define()` sugar adds `emit()` with an echo guard and `updated(changed)` computed from a `beforeUpdate` snapshot; the Python side is a ~60-line `Adapter(Component)` that renders the host, renders in the Rust engine as plain HTML, and needs no `rust_handlers` twin.
- Web Components: support deliberately (a ~30-line property-mirror hook, since `<wa-input>` takes `value` as a property and the differ emits only `SetAttr`; shadow-DOM elements are already opaque to the differ, light-DOM ones are not), with one Web Awesome example. Do not build the native foundation on them.
- An Alpine-style companion: no. ADR-025's rejection stands and the evidence strengthens it; djust's hardest client bugs (#1724, #1988/9, #2033) are all client-owned-DOM-versus-morph, which is what Livewire can only afford because one maintainer co-develops morph look-ahead with Alpine. Alpine users wrap in `dj-update="ignore"` today and it works.
- Cost: delete ~60 to 65k Python LOC, 3.9k Rust, ~1.9 MB static; add ~1.5 to 2.5k LOC including tests. Theming parses `components.css` (`component_css_generator.py:43`), so define the surviving CSS contract before deleting; 44 tags are used in `docs/website`.
- Sequence: contract fixes ship in 1.x as bug fixes (ignore parity, `pushEvent` ack, pre-detach `destroyed`, `beforeUpdate(toEl)`, nonce); then Tiptap and Leaflet adapters and deletion of the vendored bundles; 2.0 removes the catalog layers.

## 6. What this changes about the 2.0 plan

The three flips stand. The plan around them should change in four ways.

1. **Add a day-one gate before any 2.0 work: the README runs, in CI, in a browser.** Everything in section 3 depends on it and it is the cheapest item in this report.
2. **Make per-turn Python work and per-view memory the 2.0 performance story**, not template parity. Items 1 to 5 and 9 in section 2 belong in the acceptance matrix with falsifiable targets (a one-cell change in a 1,000-row list under 10 ms server; under 2 MB retained per view of a 100 KB page). Benchmark explicit mode before flipping the default; today it is 2.6x legacy on the smallest turn.
3. **Shrink the 1.4 migration program** (Phases I, B, M; gate G1; the codemod) to the inventory command plus a migration note, and spend that effort on the client split, catalog triage and the HTTP-first default. The migration program is sized for downstream users the evidence does not show.
4. **Rewrite the positioning before 2.0.0, not after**: "no JavaScript", "tiny client", "powered by Rust" and "Channels first" are each contradicted by a measurement a reviewer can reproduce in minutes, and a reviewer is the only kind of outside user djust currently has.

## 7. Not verified

No real browser measurements (client findings are static reads at the cited lines); no network RTT, concurrency, Redis or Postgres; no actors or SSE in the profile; `WebsocketCommunicator` adds ~0.35 ms to wall figures; cProfile inflates Python attribution 2 to 2.6x (used for counts, not costs); Rust phase timings are self-reported. The per-view RSS high-water figure in the audit's raw output (13.3 MB) is a `ru_maxrss` artefact and should not be quoted; current RSS per view is ~21 MB including temporaries. External adoption numbers were pulled live on 2026-10-27 and move daily; the trigger for the April 2026 star cluster and the DjangoCon US 2025 program were not identified. The "no outside users" finding does not cover private deployments.
