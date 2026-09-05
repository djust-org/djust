# Performance Brainstorm — Beyond the Rust Template Engine

**Date:** 2026-08-23 (revised same day after a verification pass)
**Status:** Brainstorm. Nothing here is approved, scoped, or scheduled.
**Question:** The Rust template engine won a large speedup. Where is the *next*
order-of-magnitude in djust, and which Django bottlenecks remain?

> **Revision note.** The first draft ranked its ideas on reasoning about the
> code rather than reading it. A verification pass falsified three of the
> premises that drove the ranking — §2.1's example, Idea 2's "this does not
> exist yet", and the benchmark §2.2 cited as evidence. Two of the three
> top-ranked ideas were partly re-proposals of shipped machinery that §1's
> table had omitted, which is precisely the failure that table exists to
> prevent. Corrected below; §10 logs what was checked and what was not.
>
> **Second pass (same day).** §9 extends the brainstorm past the render
> path to the rest of the Django stack. That sweep surfaced two confirmed
> correctness bugs — every timestamp renders in the wrong timezone, and the
> session engine is hardcoded — which now outrank every optimization here.
> Filed as #2209 (reproduced at runtime) and #2210 (static evidence only —
> labelled accordingly, and the issue says so).
>
> **Citation caveat.** Line numbers are against the working tree on the date
> above. `crates/djust_core/src/context.rs` has uncommitted #2203 changes, so
> its numbers sit ~3 lines off a clean `HEAD` checkout — symbol names are
> given alongside and are the durable reference (#1197).

---

## 1. Baseline — what is already shipped

Recorded here so future readers do not re-propose solved work. The first draft
of this table omitted rows 8–11, and that omission is what let Ideas 1 and 2
be mis-ranked.

| Area | Status |
|---|---|
| Template lexer / parser / renderer / filters | Rust (`crates/djust_templates`) |
| VDOM diff, keyed loops, LIS optimization | Rust (`crates/djust_vdom`) |
| Static/dynamic fragment split (Phoenix-style) | Shipped — `crates/djust_live/src/lib.rs` (`clear_fragment_cache`, `build_fragment_text_map`) |
| Component rendering | Rust (`crates/djust_components`) |
| Actor system / session actors | Rust + Tokio (`crates/djust_live/src/actors`) |
| State serialization | msgpack (`rmp-serde`), zstd on Redis |
| WebSocket compression | `permessage-deflate` **advisory flag only** — negotiation happens in the ASGI server (`config.py:60-70` says so explicitly) |
| **Eager model→dict serialization into the Rust context** | **Shipped** — `rust_bridge.py:86` `_normalize_db_values` (Model→dict, QuerySet→list[dict]), `serialization.py:369` `_serialize_model_safely` (recurses into *prefetched* FKs at `:446`) |
| **Dependency-driven partial render** | **Shipped** — `renderer.rs:379` `render_nodes_partial`: nodes whose deps are disjoint from `changed_keys` reuse cached HTML |
| **Parse-and-diff bypass for text-only changes** | **Shipped** — `djust_live/src/lib.rs:552` text fast path: `changed_indices` × `fragment_text_map` → `SetText` patches, skipping html5ever *and* the tree diff |
| **Persistent per-item loop render cache** | **Shipped** — `crates/djust_templates/src/loop_cache.rs` (#1967): content-hash → rendered fragment, survives across `render_with_diff` calls |
| Template variable-path extraction | Rust `extract_template_variables` (`_rust.pyi:181`), cached at `jit.py:46` |
| Path-manifest consumers | **Two**: query planning (`query_optimizer.py`) and serializer codegen (`codegen.py:15` `generate_serializer_code`) |
| Temporary assigns (memory) | `LiveView.temporary_assigns` |
| Incremental state sync / changed-key tracking | `set_changed_keys`, `optimization/fingerprint.py` |
| Free-threaded (no-GIL) safety | `#[pymodule(gil_used = false)]`, `RwLock` registries, `frozen` pyclasses, `python3.14t` CI leg (#1432, #1534) |
| psycopg3 | **Already a hard dependency** — `pyproject.toml:77` `psycopg[binary]>=3.1,<4`; the old psycopg2 blocker is closed (ROADMAP:814) |
| `sync_to_async` overhead | Measured at ~0% (#1434, `scripts/bench_sync_to_async_overhead.py`) — **the async-ORM rewrite is not the win** |

The obvious wins are gone, and several of the non-obvious ones are gone too.
What follows targets what the above does *not* cover.

---

## 2. Three findings that reframe the problem

### 2.1 The getattr sidecar is a fallback, not the hot path

`crates/djust_core/src/context.rs:289` (`Context::resolve`) tries the pure-Rust
value stack **first**, and reaches Python only on a miss:

```rust
pub fn resolve(&self, key: &str) -> crate::Result<Option<Value>> {
    if let Some(v) = self.get(key) { return Ok(Some(v.clone())); }
    // ... only now: raw_py_objects sidecar, getattr one segment at a time
```

And models are bulk-serialized into that stack *before* the render starts:
`_normalize_db_values` (`rust_bridge.py:86`) turns `Model`→dict and
`QuerySet`→`list[dict]`, and `_serialize_model_safely` (`serialization.py:369`)
**recurses into FK relations that were `select_related`'d** (`:446`,
`result[field_name] = self._serialize_model_safely(related)`). `rust_bridge.py:692`
states the contract verbatim:

> The eager dict stays the fast path (and wins every hit), but reverse
> relations, managers and non-`get_`-prefixed methods are absent from the dict
> — resolvable only via the sidecar getattr walk.

**Consequence for the first draft's example:** a 50-row × 6-column table with a
`select_related` FK traversal per row costs approximately **zero** GIL
re-entries, not 300–600. That example was the entire empirical case for
ranking Idea 1 first, and it does not hold. (Measured 2026-09-02: exactly
zero, and so is every other list-row shape below — §7.1.)

**What the sidecar surface actually is** — narrower, and a different shape.
The middle column is what this section originally reasoned from the source;
the right column is what the #2532 benchmark measured on a **`list[Model]`
row** (§7.1), and it is different: a list never reaches the sidecar at all.
`ContextMixin.get_context_data` JIT-serialises a `list[Model]` in Python by
the paths the template extracts (a re-query with `select_related` /
`prefetch_related` derived from those paths, then per-row codegen), and
`rust_bridge.py` never admits a `list` to the sidecar. The middle column
still holds for an object the sidecar *does* receive — a presenter-shaped
container (`Page(rows)`) or a path the extractor misses.

| Path shape | Resolves where (object reaching the sidecar) | Measured on a `list[Model]` row (#2532, §7.1) |
|---|---|---|
| Concrete model fields | Eager dict (Rust) | JIT-eager, Python; 0 crossings |
| FK/O2O traversal **with** `select_related`/prefetch | Eager dict (Rust), recursively | JIT-eager; 0 crossings (`list_control`) |
| FK **without** `select_related` | Only `<field>_id` is serialized; the traversal misses the dict | JIT re-queries **with** `select_related` it derived from the template; 0 crossings, same query count as the control (`list_fk_nosel`) |
| `@property` | Sidecar — `_meta.get_fields()` never yields properties | JIT codegen reads it in Python; 0 crossings — **not** the sidecar (`list_property`) |
| Reverse relations, managers, querysets | Sidecar (`{{ workspace.memberships.count }}`, #1985) | JIT `prefetch_related` + one extra query per event; 0 crossings (`list_reverse`). On a presenter row: 302 crossings + 50 `COUNT(*)` per full render (`presenter_reverse`) |
| Non-`get_`-prefixed methods | Sidecar | Not measured (no variant) |

Two second-order costs live on that fallback path and are worth measuring
before designing anything around it:

1. `protect_sidecar` (`context.rs:376`) runs `py.import("djust.serialization")`
   → `getattr("_protect_sidecar_value")` → `call1(...)` **per segment**, with
   no cached handle. Three Python operations per segment, not one.
2. Each segment also runs the Django-parity auto-call probe (`maybe_call`).

Both are only worth attacking if §7's benchmark shows the sidecar path carries
real traffic. It does — for the presenter shape, and only there (§7.1:
`presenter_control` pays 52 direct crossings + 850 transitive re-wraps for
the one-off `Value` extraction of the row list, ~3.9 ms of Python per full
render — the earlier "~5 ms" summed the re-wraps' time on top of the
crossings that already contained them, #2545 — before a single sidecar
segment is resolved). It is a
correctness-critical path (#1986 serialization floor) —
`_protect_sidecar_value` must survive any optimization, not be bypassed.

### 2.2 The state-clone cost is real; the number cited for it is not

`mixins/rust_bridge.py:18-37` documents a deliberate cost:

> `InMemoryStateBackend.get()` now returns an isolated clone of the cached view
> (`serialize_msgpack` → `deserialize_msgpack`) — mirroring how
> `RedisStateBackend.get` already behaves.

Every `get` msgpack round-trips the entire `RustLiveView` to dodge a `RefCell`
borrow race (#1353, `state_backends/memory.py:62`). Correct, but it makes state
access O(total state) rather than O(touched state).

The first draft attached this to the wrong benchmark:

```
websocket_mount_counter  median 11.89 ms  target 100 ms  (12%)
```

`test_websocket_mount_counter` (`tests/benchmarks/test_request_path.py:188`)
opens a fresh `WebsocketCommunicator` per iteration. That is a cache **miss** —
`get()` returns `None` and no clone happens. **The clone is not inside the
11.89 ms.** The clone rides the *event* path, whose benchmark
(`test_event_dispatch_increment`, `:280`) is not cited — and being a counter,
it would not expose the O(total state) scaling either, because there is
almost no state to copy.

So: the mechanism claim stands on the source, and has **no supporting
measurement anywhere in the suite**. The 11.89 ms mount remains interesting on
its own (it is what a user feels at first paint) but its composition is
unknown — see §8.

### 2.3 Consequence: the benchmark suite cannot see any of this

Every fixture in `tests/benchmarks/` uses plain dicts and lists
(`conftest.py:115`, `test_e2e.py`); no `models.Model` subclass exists anywhere
in that directory. So §2.1 is invisible to CI, and §2.2 is unmeasured.

The first draft drew the right conclusion — *any ranking here is provisional
until a model-backed benchmark exists* — but would have built the wrong
benchmark. A fixture using `select_related` throughout measures ~0 sidecar
cost and would "prove" the sidecar is free. See §7 for what the fixture
actually has to contain.

**Measured 2026-09-02 (#2532).** The benchmark exists and §7.1 holds its
table. The premise of §6's ranking is now data rather than reasoning; §6 is
left as written here and re-ranked separately.

---

## 3. Tier 1 — highest impact, grounded in §2

### Idea 1 — Close the sidecar's remaining surface (re-scoped)

**Bottleneck:** §2.1 — but only the fallback rows of that table, not model
field access generally.

**What is already done:** the bulk Python→Rust transfer the first draft
proposed *is* `_normalize_db_values` + `_serialize_model_safely`. The path
manifest it proposed reusing *is* `extract_template_variables` (already Rust,
already cached at `jit.py:46`), and it already has two consumers — query
planning and `codegen.py:15 generate_serializer_code`, which is literally
"generate the extractor, do not interpret it."

**What is left, in increasing order of payoff-uncertainty:**

1. **Cache the `_protect_sidecar_value` handle.** Hoist the module import +
   `getattr` out of the per-segment loop (`context.rs:376`). Pure win, hours of
   work, no semantics touched. Do this regardless of what §7 shows.
2. **Extend the eager manifest to `@property` and safe methods.** The path set
   is known per template; `_serialize_model_safely` iterates `_meta.get_fields()`
   and so structurally cannot see properties. Feeding it the template's actual
   path list would let it include `{{ order.total_price }}` in the eager dict.
   Must route through the same `_field_is_serializable` floor — a property is
   exactly the shape that could bypass a field denylist (#1868).
3. **`queryset.values(*paths)` projection.** The genuinely novel stage: skip
   `ModelIterable.__iter__` so Django never instantiates model objects on
   read-only render paths. `DB rows → Rust context → Rust render`.
   Note `serialization.py:860` already refuses `.values()` projections *in the
   sidecar* deliberately (fail-closed: a projection has no model identity, so
   the per-field floor cannot apply to it). That docstring also names the
   sanctioned alternative — *"Precompute projected rows in `get_context_data()`,
   where the eager serialization floor applies"* — which is exactly where this
   item would put them. The constraint is a design pointer, not a blocker.

**Risks:** auto-call semantics, lazy translation objects, and the #1986
serialization floor, which the manifest must preserve rather than route around.
Over-extraction of `{% if %}`-guarded paths is still an open question (§8).

**Effort:** Small (item 1) / Medium (items 2–3).
**Confidence in payoff:** High for item 1, **unknown** for 2–3 until §7 —
downgraded from the first draft's "High", which rested on the falsified example.

---

### Idea 2 — Extend the parse-and-diff bypass to attribute slots (re-scoped)

**What the first draft claimed:** "Every event re-renders the whole template to
HTML, re-parses it, and diffs it — even when one integer changed", and "No
LiveView-family framework does this."

**Both are false.** djust already does it, for text:

- `renderer.rs:379 render_nodes_partial` — *"only re-render nodes whose deps
  overlap `changed_keys`… Nodes whose deps are disjoint reuse their cached
  HTML."* That is the compile-time dependency map, at top-level-node
  granularity.
- `djust_live/src/lib.rs:552` — the text fast path takes `changed_indices` ×
  `fragment_text_map`, walks to the VDOM node by path, and emits `SetText`
  patches directly, **skipping html5ever and the tree diff entirely**.

So the Solid.js-style mechanism is shipped for pure interpolations. What
remains genuinely unbuilt:

1. **Attribute slots.** The fast path bails the moment a changed fragment
   contains `<` — so `dj-class`, `value`, `style`, `disabled` and every other
   attribute binding still pays a full parse + diff. This is the concrete gap
   and probably the best-value item in this document.
2. **Sub-node granularity.** A top-level node mixing text and markup can never
   take the fast path, however small the change.
3. **Eliminating string construction.** Fragments are still rendered to strings
   and concatenated into `full_output` even when every one is a cache hit.

**Risks:** attribute patches have a correctness surface text patches do not —
boolean attributes, `class` merging, and the escaping rules that
`safe_output_filters` governs. The existing differential discipline applies:
fast-path output must equal full-render output for every template in the suite,
and the gate-off must show the fast path is actually being taken (#1468) rather
than silently falling through to the slow path in every test.

**Effort:** Medium — down from "Large". It extends a shipped mechanism rather
than introducing one.
**Confidence in payoff:** Medium-high, and **not gated behind Idea 1** the way
the first draft claimed — the fast path never touches the sidecar.

---

### Idea 3 — Copy-on-write state instead of whole-view cloning

**Bottleneck:** §2.2 — msgpack round-trip of the entire `RustLiveView` per
state `get`, and full-state persistence per event.

**Mechanism:** Replace serialize/deserialize isolation with structural sharing
(persistent/immutable maps, e.g. `im` or `rpds`) or `RwLock` + cheap snapshot,
so concurrent readers share unchanged structure instead of copying it. Persist
only the state **delta** to Redis rather than the whole view.

**Why still interesting:** #1353 was fixed for correctness under time pressure
and the fix was never revisited for cost. Unchanged from the first draft.

**What changed:** the evidence. This must be re-based onto the event path
(`test_event_dispatch_increment`) with a state payload large enough to matter —
the mount benchmark does not exercise it at all (§2.2).

**Composes with:** Idea 9 (a single-process deployment removes the Redis hop
entirely, making the in-memory path the only path).

**Risks:** Must preserve the #1353 invariant exactly — no two threads holding a
Rust `&mut self` borrow across a GIL-yielding render. Note the yield in
question is *the sidecar walk itself* (`memory.py:62` names it), so Ideas 1 and
3 interact: shrinking the sidecar surface shrinks the race window this
isolation exists to protect. Reintroducing the race is a panic, not a
slowdown. Gate on the #1353 concurrency regression tests and
`free_threaded_safety.rs`.

**Effort:** Medium. **Confidence in payoff:** Medium — downgraded from
"Medium-high" pending any measurement at all.

---

## 4. Tier 2 — Django-layer wins, larger scope

### Idea 4 — Per-event query batching over psycopg3 pipeline mode

**Bottleneck:** Network round trips. A typical `mount()` issues 3–8 independent
queries, serially.

**Mechanism:** Coalesce all queries issued during one event/mount into a single
round trip — DataLoader semantics on top of psycopg3 pipeline mode. Invisible to
application code.

**Why this and not the async-ORM rewrite:** #1434 already measured
`sync_to_async` overhead at ~0%. That finding says concurrency is not the
problem — **round-trip count** is. On a remote/managed Postgres (e.g. the
NoteWizard Hetzner k3s → managed PG hop), `RTT × N → RTT × 1` is plausibly the
largest wall-clock win available anywhere in this document.

**Read first:** ROADMAP has a whole `#1434 native-async-ORM audit` section
(~:810) that scoped this territory and chose to audit rather than migrate.
Idea 4 is a different bet than #1434 — batching, not async — but it inherits
that audit's findings.

**Risks:** ~~psycopg3 requirement~~ — **already satisfied**: `pyproject.toml:77`
pins `psycopg[binary]>=3.1,<4` and ROADMAP:814 records the blocker closed. The
first draft's risk note was stale in the idea's favour. Real risks remain:
query independence must be provable, or batching changes semantics;
transactions and `select_for_update` need explicit opt-out.

**Effort:** Large. **Confidence in payoff:** High for remote DBs, low for
localhost.

---

### Idea 5 — SQL fingerprint cache + server-side PREPARE

**Bottleneck:** Django rebuilds SQL from the `Query` tree on every evaluation;
Postgres re-plans every statement.

**Mechanism:** Fingerprint `(model, filter shape, ordering, slice)` → cached SQL
string + a code-generated parameter binder (the same trick `codegen.py` already
uses for serializers). Then `PREPARE` the statement so the planner is skipped
too. Two compilers removed per query.

**Risks:** Fingerprint must capture everything that affects SQL, or it silently
serves the wrong query — the highest-severity failure mode in this document.
Prepared statements interact badly with connection pooling in transaction mode
(PgBouncer).

**Effort:** Medium. **Confidence in payoff:** Medium.

---

### Idea 6 — Rust field validation for live forms

**Bottleneck:** Live forms validate per keystroke, so `Form.full_clean` sits on
the event hot path — a place Django's form layer was never designed to be.

**Mechanism:** Implement `required`, `max_length`/`min_length`, regex, email,
URL, integer/decimal parsing, and date parsing in Rust. Fall back to Python only
for custom `clean_<field>` / `clean()`.

**Composes with:** `forms.py`, `formsets.py`, `wizard.py`, and the existing
`FormMixin` live-validation path.

**Risks:** Error-message parity (i18n, `error_messages` overrides) must be
exact, or validation UX silently changes. Locale-aware number/date parsing is
the hard part.

**Effort:** Medium. **Confidence in payoff:** Medium — high for form-heavy apps,
near zero otherwise.

---

## 5. Tier 3 — novel, speculative, cheap to prototype

### Idea 7 — Speculative patch precomputation

The set of possible events is statically knowable from the template
(`dj-click="increment"`, `dj-change=...`). During idle time, precompute patches
for likely next events against the current state and cache them keyed by state
fingerprint. On the real event, a fingerprint match ships a cached patch at
~0 ms of compute.

The aggressive version: push the precomputed patch to the client *before* the
click, and have the server merely confirm. The interaction becomes instant.

**Why novel:** No LiveView-family framework does this. It trades idle CPU for
p99 latency — a good trade on any server that is not saturated.

**Risks:** Wasted work under high load (needs a load-aware kill switch);
combinatorial explosion for parameterized events (`dj-click="f(1)"`); and
security — a precomputed patch must never be delivered for an event the user is
not authorized to fire. Cache keying must include identity, not just state.

**Effort:** Medium. **Confidence in payoff:** Speculative — but a prototype is
small and the measurement is decisive.

---

### Idea 8 — Shared-dictionary compression for patches

Preload the template's static string table as a deflate dictionary on both ends.
Patches are largely recombinations of text the client already has, so a shared
dictionary would compress far better than stock `permessage-deflate` starting
from an empty window.

**Feasibility problem the first draft missed — this is not "Small".** RFC 7692
`permessage-deflate` has **no custom-dictionary negotiation**, and no browser
API exposes one (`DecompressionStream` included; Chrome's compression-dictionary
transport is HTTP-only). Compression also does not happen in djust — `config.py`
says so explicitly: the flag is advisory and *"negotiation actually happens in
the ASGI server."* djust does not own the deflate context and cannot seed it.

The only remaining route is application-layer compression **inside** the message
payload plus a JS decompressor on the client. That means:
- double compression (app-layer output is then re-deflated by the transport),
- a decompressor added to a client budget CLAUDE.md pins at **~58 KB gzipped**,
- and a wire-format version negotiation for older deployed clients.

**Effort:** Medium-to-large, not Small. **Confidence:** Low as specified.
Worth keeping only as a mobile/high-latency item, and only after someone
confirms wire size is a bottleneck at all — it currently is not.

---

### Idea 9 — Single-process free-threaded deployment mode

The hard part is done (`gil_used = false`, `RwLock` registries, `frozen`
pyclasses, `python3.14t` CI). The unclaimed payoff is a **deployment mode**: one
process × N threads instead of N prefork workers.

That gives one shared template cache, one shared in-memory state backend, and
one shared actor system — which means **Redis disappears** for small and mid-size
deployments, taking the serialization round-trip of §2.2 with it.

Adjacent: compile templates to a serialized artifact at build time and `mmap` it
read-only, so workers stop re-parsing on every cold start. Ties into
`deploy_cli.py`.

**Risks:** Free-threaded ecosystem gaps are real and already bit this project
(`orjson` has no free-threaded build; `uv` re-managed the 3.14t venv — see
`RETRO.md:1952`). Any third-party C extension in the dependency tree can
re-enable the GIL process-wide and silently erase the benefit. Needs a startup
assertion that the GIL is actually still disabled.

**Effort:** Medium (mostly packaging, docs, and ecosystem verification).
**Confidence in payoff:** High operationally, unproven on latency.

---

## 6. Ranking

Changed from the first draft: Idea 2 rises (it extends shipped machinery rather
than introducing an architecture), Idea 1 splits (one free item, one unproven
remainder), Idea 8 falls out of "cheap parallel win".

| # | Idea | Effort | Payoff confidence | Blast radius |
|---|---|---|---|---|
| 1a | Cache the `_protect_sidecar_value` handle | Small | High (bounded) | Contained |
| 2 | Attribute slots in the parse/diff bypass | Medium | Medium-high | Contained — extends `render_nodes_partial` |
| 3 | Copy-on-write state | Medium | Medium (unmeasured) | Contained, but touches #1353 race |
| 1b | Property/method manifest + `.values()` projection | Medium | Unknown until §7 | Serialization floor |
| 4 | Query batching / pipeline mode | Large | High (remote DB only) | ORM semantics |
| 9 | Free-threaded single-process mode | Medium | High operationally | Deployment |
| 7 | Speculative patches | Medium | Speculative | Contained + security surface |
| 5 | SQL fingerprint + PREPARE | Medium | Medium | Correctness-critical |
| 6 | Rust form validation | Medium | Medium | Contained |
| 8 | Shared-dictionary compression | Medium-large | Low as specified | Client budget + wire format |

**Recommended order:** §7 benchmark → 1a (free, do it anyway) → 2 → 3, with 4
gated on whether production Postgres is remote, and 1b gated on what §7 shows
about sidecar traffic.

**See also §9.4** — the Django-subsystem sweep adds Ideas 10–15, two of which
(10, 14a) are correctness fixes that outrank this entire table.

Rationale: Idea 2 is now the strongest item — the mechanism is proven in
production for text, the gap (attributes) is well-defined, and the blast radius
is one function. Idea 1a is unconditionally worth doing. Everything below the
line needs §7 first, because the first draft's ordering came from reasoning
about code rather than reading it, and three of its premises did not survive
being read.

---

## 7. Prerequisite: a model-backed benchmark

Per §2.3, every ranking above is provisional. Before designing any of these:

Add a benchmark on a realistic model-backed list page — 50 rows, 6 columns,
rendered through the real WebSocket mount path **and** the event path — and
profile it into five buckets:

1. Rust render proper
2. Python `getattr` boundary crossings, incl. the per-segment
   `protect_sidecar` import (§2.1)
3. ORM (SQL compile + round trip + model instantiation)
4. State serialization on the **event** path (§2.2) — the mount path does not
   exercise it
5. HTML parse + VDOM diff, split by whether the text fast path was taken

**The fixture requirements are the point.** The obvious fixture — a
`select_related` model list — measures ~0 in bucket 2 and would "prove" the
sidecar is free. To be informative it must include, in separate variants:

- a `select_related` FK traversal (expected: bucket 2 ≈ 0 — this is the control)
- a `@property` column (expected: sidecar)
- a reverse-relation or manager call, e.g. `{{ row.comments.count }}` (sidecar)
- an FK traversal **without** `select_related` (misses the eager dict)
- an **attribute** change per event as well as a text change, so bucket 5
  separates fast-path from full parse+diff

Instrument whether the text fast path was taken; a benchmark that silently falls
through to the slow path measures the slow path while appearing to measure the
fast one.

That measurement decides the order of Ideas 1b, 3 and 4, and tells us whether
Idea 2's attribute work is worth Medium effort. Without it we are ranking on
reasoning, not data — which is exactly how the first draft went wrong.

### 7.1 Measured (2026-09-02, #2532)

`tests/benchmarks/test_model_backed_render_2532.py` (`make benchmark-model`)
is the benchmark above: a 50-row × 6-column model list through the real
WebSocket mount and event paths (`WebsocketCommunicator` → `LiveViewConsumer`
→ `ViewRuntime.dispatch_mount` / `dispatch_event`), seven variants, three
events each, split into the five buckets. Every variant the list above asked
for is present — `list_control` (the `select_related` control),
`list_property`, `list_reverse` (`{{ row.comments.count }}`), `list_fk_nosel`
— plus two the list did not know it needed, `presenter_control` and
`presenter_reverse` (the same rows behind a plain `Page(rows)` object), and
`snapshot` (`list_control` with `enable_state_snapshot = True`, the one
variant on which bucket 4's `persist` column is non-zero). The differ now
reports which parse-skipping path fired (`RenderTiming.fast_path`), so the
`fast` column is an instrument, not an inference.

Release build (`maturin develop --release`), medians of 3 rounds after a
warm-up, sqlite `:memory:`, ms, taken on a quiet machine (load ~6 on 12
cores, no other suite running). `list_control`'s mount row is the first
benchmark in the process and includes its cold start. The counts
(crossings, queries, serializer calls) are deterministic and were identical
across every run taken for this section, loaded or quiet; only timings move
with load, which is why nothing here asserts on a duration.

```
variant            phase            total  1 rust  2 xings  2 proxy  2 xing_ms  py_calls  3 q  3 sql_ms  3 list_ms  4 state  4 jit  4 persist  5 parse  5 diff  5 ser  5 fast
-----------------  ---------------  -----  ------  -------  -------  ---------  --------  ---  --------  ---------  -------  -----  ---------  -------  ------  -----  ------
list_control       mount             8.43    0.39        0        0       0.00       976    2      0.04       0.40     4.54   0.67       0.00     0.23    0.00   0.13       -
list_control       text_change       3.08    0.00        0        0       0.00       497    1      0.03       0.00     1.30   0.75       0.00     0.00    0.00   0.13    frag
list_control       attr_change       3.77    0.39        0        0       0.00       497    1      0.04       0.00     1.24   0.68       0.00     0.25    0.19   0.12    full
list_control       row_text_change   4.11    0.38        0        0       0.00       947    2      0.04       0.00     1.89   0.65       0.00     0.06    0.00   0.12  region
list_property      mount             8.71    0.38        0        0       0.00       976    2      0.04       0.41     4.55   0.72       0.00     0.25    0.00   0.12       -
list_property      text_change       3.16    0.01        0        0       0.00       497    1      0.03       0.00     1.31   0.75       0.00     0.00    0.00   0.12    frag
list_property      attr_change       3.82    0.38        0        0       0.00       497    1      0.03       0.00     1.27   0.70       0.00     0.26    0.19   0.12    full
list_property      row_text_change   4.33    0.40        0        0       0.00       947    2      0.04       0.00     1.99   0.72       0.00     0.05    0.00   0.12  region
list_reverse       mount            11.17    0.47        0        0       0.00      1026    3      0.07       0.43     4.79   2.08       0.00     0.24    0.00   0.13       -
list_reverse       text_change       4.86    0.01        0        0       0.00       497    2      0.06       0.00     1.43   2.09       0.00     0.00    0.00   0.12    frag
list_reverse       attr_change       5.51    0.39        0        0       0.00       497    2      0.05       0.00     1.32   2.09       0.00     0.27    0.19   0.11    full
list_reverse       row_text_change   5.81    0.39        0        0       0.00       997    3      0.07       0.00     2.06   2.02       0.00     0.06    0.00   0.11  region
list_fk_nosel      mount             9.10    0.43        0        0       0.00       976    2      0.06       0.31     4.87   0.75       0.00     0.26    0.00   0.12       -
list_fk_nosel      text_change       3.55    0.01        0        0       0.00       497    1      0.04       0.00     1.39   0.87       0.00     0.00    0.00   0.11    frag
list_fk_nosel      attr_change       4.16    0.41        0        0       0.00       497    1      0.04       0.00     1.36   0.77       0.00     0.24    0.18   0.11    full
list_fk_nosel      row_text_change   4.54    0.39        0        0       0.00       947    2      0.06       0.00     2.06   0.75       0.00     0.04    0.00   0.10  region
presenter_control  mount            11.67    0.00       52      850       5.11       526    1      0.02       0.44     4.05   0.09       0.00     0.24    0.00   0.13       -
presenter_control  text_change       2.58    0.01        0        0       0.00       498    0      0.00       0.00     1.35   0.10       0.00     0.00    0.00   0.12    frag
presenter_control  attr_change       7.46    0.00       52      850       5.15       498    0      0.00       0.00     1.33   0.09       0.00     0.24    0.18   0.11    full
presenter_control  row_text_change   7.14    0.00       52      850       5.08       498    1      0.02       0.00     1.32   0.09       0.00     0.05    0.00   0.12  region
presenter_reverse  mount            18.98    6.25      302      950       5.37       526   51      0.28       0.42     4.06   0.09       0.00     0.24    0.00   0.11       -
presenter_reverse  text_change       2.51    0.00        0        0       0.00       498    0      0.00       0.00     1.28   0.10       0.00     0.00    0.00   0.11    frag
presenter_reverse  attr_change      14.20    6.13      302      950       5.25       498   50      0.26       0.00     1.25   0.09       0.00     0.24    0.18   0.12    full
presenter_reverse  row_text_change  14.18    6.12      302      950       5.40       498   51      0.28       0.00     1.33   0.10       0.00     0.05    0.00   0.11  region
snapshot           mount            12.68    0.38        0        0       0.00       976    2      0.04       0.40     4.67   0.65       0.00     0.23    0.00   0.11       -
snapshot           text_change       5.61    0.00        0        0       0.00       987    5      0.10       0.00     1.30   0.72       2.40     0.00    0.00   0.12    frag
snapshot           attr_change       5.83    0.37        0        0       0.00       987    4      0.08       0.00     1.28   0.71       1.95     0.23    0.18   0.12    full
snapshot           row_text_change   6.24    0.36        0        0       0.00      1437    5      0.09       0.00     2.04   0.65       1.97     0.04    0.00   0.12  region
```

Column notes: `2 xing_ms` is the Python time inside the direct crossings and
includes the counting wrappers' own `perf_counter` / `_getframe` overhead
(~900 wrapped calls on the presenter variants), so on those rows bucket 1 is
under- and bucket 2 over-attributed, and `1 rust` can floor at 0. `4 state`
is `_sync_state_to_rust` minus `get_context_data`; `4 jit` is
`get_context_data` alone.

**Three findings.**

1. **Lists never cross the sidecar; the presenter shape is the sidecar
   path.** All five list-shaped variants (`list_*` and `snapshot`) make 0
   Rust→Python crossings in every phase, including `list_property` and
   `list_reverse`, which §7 above (and #2532's own fixture table) predicted
   would be sidecar traffic. The mechanism per path shape is in §2.1's
   right-hand column: the JIT resolves everything on a `list[Model]` row in
   Python before Rust runs. The sidecar carries traffic only for a container
   the JIT skips: `presenter_control` pays 52 direct crossings + 850
   transitive re-wraps (~3.9 ms of Python; the table's `xing_ms` column was
   cut before #2545 removed the nested-re-wrap double count and reads high
   on every `presenter_*` row) for the one-off `Value` extraction
   of the row list, and `presenter_reverse` pays 302 + 950 plus 50–51
   `COUNT(*)` queries per full render — an N+1 on every attribute-change
   event, ~6 ms in the Rust-side walk. The four `list_*` variants' zero
   crossings are the invariant ADR-027's flip (#2539) is held to; the
   presenter contrast is what the flip is meant to change. The spike's
   original 357 decomposes as 252 direct `_protect_sidecar_value` calls +
   100 proxy re-wraps + 5 Python-side.

2. **The JIT re-serialises every row on every event.** `py_calls` sits at
   ~497 serializer calls per event on every list variant, and `3 q` reads
   one query per event even for `text_change` (one label outside the loop;
   the presenter variants read 0 there). `list_reverse` pays a second query
   per event for the `prefetch_related` the JIT derived. That is #2536: a
   static list costs a query plus 50 instantiations plus serialisation per
   event, and an in-memory row mutation is silently discarded by the
   re-query. Measurement only here (#1079).

3. **A list mount is state sync, not render.** On the quiet-machine run a
   list mount spends ~4.3 ms in `_sync_state_to_rust` (after `get_context_data`)
   against ~0.4 ms of Rust render and ~0.2 ms of parse; under load the same
   ratio holds (9–16 ms vs 0.4–0.5 ms above). `snapshot` adds the persistence
   path §2.2 wanted measured: 1.8–2.3 ms per event in
   `_persist_state_after_event` (the signed-snapshot session write), and 4–5
   queries per event where the other list variants issue 1–2 — the session
   store's own writes. Every other variant reads 0 in `4 persist` by design,
   since `_persist_state_after_event` runs only under the `enable_state_snapshot`
   opt-in.

What this does to §6 is deliberately not decided here (#1079): Idea 1a's
handle cache addresses the presenter shape only; Idea 1b's manifest was
premised on `@property` reaching the sidecar, which it does not on a list; and
finding 3 puts state serialization, not the boundary, at the top of the list
mount. Re-ranking is its own pass.

---

## 8. Open questions

- ~~Is the authenticated user re-fetched per WebSocket event?~~ **Closed — no.**
  `scope["user"]` (`websocket.py:3324`, `runtime.py:3993`) is a Channels
  `LazyObject` resolved once per connection. A per-event re-fetch exists only
  behind opt-in `LIVEVIEW_CONFIG['reauth_on_event']` (`runtime.py:1253`, which
  calls `await get_user(consumer.scope)` at `:1266`), gated on
  `login_required`/`permission_required`. No free win here; where it does fire
  it is a deliberate security tradeoff.
- How much of the 11.89 ms mount is Django request/auth/session setup, how much
  is the WebSocket handshake the benchmark includes per iteration, and how much
  is djust's own work? Still unknown — and note §2.2: whatever it is, it is not
  the state clone.
- What fraction of real template paths land on the sidecar rather than the
  eager dict? This is the single number that decides whether Idea 1b exists.
  A downstream corpus (djust.org, djustlive, the demo project) would answer it
  faster than synthetic reasoning. **Answered for the synthetic corpus
  (§7.1):** none of them on a `list[Model]` row, all of them on a
  presenter-shaped container. The downstream-corpus fraction is still open.
- Is production Postgres remote or co-located? This alone decides whether Idea 4
  is the biggest item in this document or the smallest.
- Does over-extraction in Idea 1b (pulling paths guarded by `{% if %}` that
  never render) cost more than the getattr crossings it eliminates?
  Template-shape dependent; needs the §7 benchmark to answer.

---

## 9. The rest of Django — a subsystem sweep

§1–§8 are all about the render path. This section asks the broader question
directly: **which other Django subsystems sit on djust's hot paths, and which
have been ported to Rust, bridged back to Python, or silently skipped?**

Method: an import-frequency survey of `python/djust/`, a trace of what actually
runs per WS mount and per WS event, and — for anything the Rust engine claims to
implement — a **differential probe against Django's own template engine**, since
"implemented" and "matches Django" are different claims (#1516).

The differential probes found two things this document did not go looking for:
**two confirmed correctness bugs, both shipping wrong output today.** They are
recorded here rather than filed away silently because in both cases the *fix
design is a performance decision*, which is what makes them belong in a
performance brainstorm rather than only in the tracker.

### 9.1 Subsystem status

| Django subsystem | On a djust hot path? | Status |
|---|---|---|
| Template lexer / parser / renderer / filters | Yes | **Ported to Rust** |
| HTML escaping | Yes, per interpolation | **Ported** — `filters.rs:701 html_escape` |
| `{% static %}` / `{% csrf_token %}` / `{% url %}` | Yes | Parsed in Rust (`parser.rs:625-641`) |
| Custom tags / filters | Yes | **Bridged to Python** — one GIL crossing per call (`registry.rs`) |
| **i18n — `{% trans %}` / `{% blocktrans %}`** | Yes, for i18n apps | **Not implemented — hard render error** (§9.2 C) |
| **l10n — locale number formatting** | Yes, per numeric filter | **Not implemented — silently wrong output** (§9.2 D) |
| **Timezone — `USE_TZ` → `settings.TIME_ZONE`** | Yes, per datetime | **Not implemented — silently wrong output** (§9.2 A) |
| ORM query compilation | Yes, per mount | Untouched — Idea 5 |
| Model instantiation / row unpacking | Yes, per row | Untouched — Idea 1b |
| Forms / `full_clean` | Yes, per keystroke on live forms | Untouched — Idea 6 |
| Sessions | Per mount, per save | Used — but **engine hardcoded** (§9.2 B) |
| Context processors | **Per event** | Re-run every event; only theming is memoized |
| Auth / user resolution | Once per connection | Fine — see §8 |
| URL resolution (`resolve`) | Only on `url_change` / `live_redirect` | Fine — not per event |
| Django signals | Per full-HTML update | djust-owned, low volume |
| Middleware chain | HTTP GET only | WS path bypasses it entirely |
| Cache framework / pickle | No | djust uses msgpack + zstd instead |

### 9.2 What the differential probes found

**A. Every timestamp renders in UTC, not `settings.TIME_ZONE`. Confirmed.**
*Filed as [#2209](https://github.com/djust-org/djust/issues/2209).*

There is **no timezone conversion anywhere** in `python/djust/` or `crates/` —
the only `chrono::Local` reference is `renderer.rs:1208` (`{% now %}`).
`normalize_django_value` emits `.isoformat()`, preserving the UTC offset the ORM
returns under `USE_TZ=True`, and the Rust `date` filter formats whatever offset
it is handed. Django applies `timezone.localtime()` first; djust does not.

Reproduced through the real `LiveView.render()` path with
`USE_TZ=True, TIME_ZONE="America/New_York"`:

```
stored (UTC):           2026-08-22T23:30:00+00:00
Django renders:         2026-08-22 19:30
djust LiveView renders: 2026-08-22 23:30      <-- 4 hours off
```

Both value shapes are affected (`.isoformat()` via the eager dict, `str()` via
the sidecar). The scaffold sets `USE_TZ = True` (`scaffolding/templates.py:171`),
so this is the default configuration. This is adjacent to code that shipped
*yesterday* (#2203 fixed `date`/`time` parsing for datetimes) — the parse was
fixed, the timezone was never in scope.

**B. The session engine is hardcoded to the DB backend. Confirmed by grep.**
*Filed as [#2210](https://github.com/djust-org/djust/issues/2210).*

`runtime.py:3978` and `websocket.py:3313` both do
`from django.contrib.sessions.backends.db import SessionStore` when synthesizing
a request for `live_redirect` / `url_change`. Nothing in the package reads
`settings.SESSION_ENGINE`. A project on
`SESSION_ENGINE = "…backends.cache"` (or `cached_db`, or `signed_cookies`) gets a
DB-backed store on that path — reading a row that may not exist, so the
synthesized request can see an **empty session**, and paying a DB round trip the
project configured its way out of. Correctness first, performance second.

(The Channels `SessionMiddlewareStack` puts a correct, engine-honouring session
in `scope["session"]`; only the synthesized-request path is wrong.)

**C. `{% trans %}` / `{% blocktrans %}` are unsupported.** Confirmed — both raise
`Unsupported template tag`. The error is explicit and actionable, so this is a
feature gap, not a silent bug. But it means **no i18n app can use the Rust
engine**, which is a large excluded population for a framework whose pitch is
that the fast path is the default path.

**D. Locale-aware number formatting is not implemented.** Confirmed with
`USE_L10N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="de"`:

```
{{ n|floatformat:2 }} with n=1234.5   Django='1.234,50'   djust='1234.50'
```

Same class as A: the Rust filters are locale-blind and fail *silently*. Month
and day names in `date` formats are almost certainly the same story.

**E. `{% url %}` fails soft to empty string** where Django raises. Consistent
with djust's general silent-empty policy for bad template paths, but worth a
deliberate decision rather than an inherited one — a typo'd URL name renders as
a dead link instead of an error.

### 9.3 Ideas

---

#### Idea 10 — A localization layer in Rust (tz + l10n) — #2209

**This is the highest-priority item in the whole document, because it is a
correctness fix, not an optimization.** §9.2 A and D are wrong output shipping
today.

**Why it belongs in a performance brainstorm:** the naive fix is a per-value
Python round trip — `timezone.localtime(dt)` and `formats.number_format(n)` per
datetime and per number, which is a GIL crossing per rendered cell and would
reintroduce §2.1's cost in the one place it does not currently exist. The
performant fix resolves the active timezone and locale **once per render** and
passes them into Rust, where `chrono-tz` does the conversion and a small locale
table does the separators.

**Mechanism:**
1. Resolve `timezone.get_current_timezone_name()` and the active locale once in
   `_sync_state_to_rust`, alongside the existing per-render flag plumbing
   (`_apply_loop_render_cache_flag` / `_apply_template_auto_call_flag` are the
   pattern to mirror — #168/#1143).
2. Set them on the `RustLiveView` via a `set_render_locale(tz_name, locale)`
   setter following the same idempotent, `hasattr`-guarded shape.
3. `date`/`time` convert with `chrono-tz` before formatting; `floatformat` and
   friends consult a separator table.

**Risks:** `chrono-tz` adds a compiled tzdata table to the wheel (~hundreds of
KB — measure it). Django's l10n format definitions are per-locale Python modules
(`django/conf/locale/*/formats.py`); a full port is large, so scope this to the
separator/grouping subset first and fall back to the current behaviour for the
rest, loudly. Django's `date` format characters already have a Rust
implementation to extend, so month/day name localization rides the same table.

**Effort:** Medium. **Confidence in payoff:** N/A — this is correctness. The
performance question is only whether the fix costs anything, and done this way
it should cost approximately nothing per value.

---

#### Idea 11 — i18n catalogs in Rust

**Bottleneck:** §9.2 C — `{% trans %}` is a hard error, so i18n apps cannot use
the Rust engine at all.

**Mechanism:** two options with very different cost profiles, and the choice is
exactly the kind of decision this document exists to frame:

- **Bridge it.** Register `trans`/`blocktrans` handlers through the existing
  Python tag registry. Small, correct immediately, and costs one GIL crossing
  per translated string — which on a nav-heavy page is dozens per render.
- **Port it.** Parse the compiled `.mo` catalogs into a Rust-side map at startup
  and resolve translations in-engine. `gettext` `.mo` is a simple, stable binary
  format. Zero crossings; plural forms and context (`pgettext`) are the fiddly
  parts.

**Recommendation:** bridge first to unblock the population, port second if the
§7 benchmark shows the crossings matter. That sequencing follows #1077
(lift the working thing first, generalize second).

**Effort:** Small (bridge) / Medium (port). **Confidence:** High that it unblocks
users; unknown that it is fast.

---

#### Idea 12 — Memoize the whole context-processor chain, not just theming

**Bottleneck:** `_apply_context_processors` (`mixins/context.py:446`) is called
from `_sync_state_to_rust` on **every WebSocket event**, where Django's HTTP path
runs the chain once per request. The processor *list* is cached
(`_context_processors_cache`); each `processor(request)` **call** is not.

For a keystroke-driven live form that is the whole chain — `auth` (building
`SimpleLazyObject` + `PermWrapper`), `messages` (touching session storage),
`i18n`, `static`, `tz`, plus every third-party processor — per keystroke.

**Why the chain cannot simply be gated off:** canon #1722/#1726 covers exactly
this. Change-detection only forwards *changed* vars, so processors must re-run
each event for a live theme switch to be detected. The correct cost reduction is
**request-scoped memoization of the expensive sub-renders**, not skipping the
application. #1727 already did this for theming
(`theming/context_processors.py:42 _render_theme_outputs`, an `lru_cache` with a
`cache_clear`) — this idea is generalizing that proven pattern to the rest of the
chain.

**Mechanism:** a per-connection memo keyed on the inputs a processor actually
depends on, with explicit invalidation on the events that can change them
(login/logout, locale switch, a new message queued). Processors that are pure
functions of `request.user` + locale are trivially memoizable; `messages` is not
and must stay live.

**Risks:** the failure mode is stale UI, which is worse than slow UI. Any
processor whose output can change without an event djust observes must be
excluded. Start with an allowlist, not a denylist.

**Effort:** Medium. **Confidence in payoff:** Medium-high for chatty views,
near zero for click-driven ones.

---

#### Idea 13 — Stop building the context twice on the state-save path

**Bottleneck:** `_persist_state_after_event` (`runtime.py:3310`) runs per event
when `enable_state_snapshot` is on, and calls **`get_context_data()` a second
time** — the render already called it — then re-runs `normalize_django_value`
over the entire result, then does a session write.

That means every ORM query, every property, and every computation inside a
user's `get_context_data()` happens **twice per event**, and the full state is
serialized twice. The 150ms `asyncio.wait_for` guard around it
(`EVENT_STATE_SAVE_TIMEOUT_S`) is itself evidence that this was already known to
be slow enough to need backpressure protection.

**Mechanism:** reuse the context the render already built. The render path and
the save path run inside the same event turn, so the context is available —
this is plumbing, not architecture. Combine with persisting the **delta** rather
than the whole state (Idea 3's second half) and the save becomes proportional to
what changed.

**Note on scope:** `enable_state_snapshot` defaults to `False`
(`live_view.py:401`), so this is not on the default hot path. But it is the flag
users must enable for WS-reconnect state continuity, so "opt-in" here means
"opt-in to a feature people want", not "rare".

**Effort:** Small-to-medium. **Confidence in payoff:** High *for views that
enable it* — a doubled `get_context_data()` is a doubled query budget.

---

#### Idea 14 — Honour `SESSION_ENGINE`, and make session I/O async-native — #2210

**Bottleneck:** §9.2 B — a hardcoded DB session backend on the synthesized-request
path.

**Mechanism:** the correctness half is small and should just be done:

```python
from django.utils.module_loading import import_module
SessionStore = import_module(settings.SESSION_ENGINE).SessionStore
```

which is precisely what `django.contrib.sessions.middleware` does. The
performance half is the interesting part: a project on
`SESSION_ENGINE = "…backends.cache"` with Redis removes a DB round trip from
every mount and every save, and that is a bigger absolute win than most of
Tier 1 — for free, by respecting a setting the user already set.

Second stage: djust already `await`s `save_session.aset` / `asave`, so the async
session API is in use; check whether the *read* path on mount is equally async or
whether it blocks a thread.

**Risks:** none for the correctness fix — it is strictly closer to Django's own
behaviour. `signed_cookies` sessions cannot be written from a WebSocket at all
(no response to set a cookie on), so that engine needs an explicit, documented
refusal rather than a silent no-op.

**Effort:** Small. **Confidence in payoff:** High, and it fixes a bug on the way.

---

#### Idea 15 — Batch the custom tag/filter bridge

**Bottleneck:** every `{% custom_tag %}` and `|custom_filter` crosses into Python
individually through the registry (`registry.rs`,
`_ensure_custom_filters_bridged`). A template using a handful of custom filters
inside a 50-row loop pays that per cell — structurally the same cost as §2.1, on
a path §2.1 does not cover.

**Mechanism:** collect all bridge calls for one render into a batch and cross
once, or codegen a Rust-side shim for the common pure-function shapes
(`@register.filter` on a function with no `request` dependency is the majority).
The `codegen.py` serializer-generation trick is the existing precedent.

**Risks:** custom tags can have side effects and can depend on render order; only
declared-pure filters are safely batchable. Needs an opt-in marker or a
conservative purity analysis, not a guess.

**Effort:** Medium. **Confidence in payoff:** Unknown — depends entirely on how
many custom filters real templates use per render. §7's benchmark should
instrument bridge-crossing count so this can be answered with a number.

---

### 9.4 What this section changes about §6

Idea 10 goes to the top of the whole document — above §7's benchmark, above
everything — because it is not an optimization at all: djust renders every
timestamp in the wrong timezone and every localized number in the wrong format,
by default, today. Idea 14's correctness half is the same kind of item at
smaller scale.

The rest slot in by evidence: Idea 13 is a concrete doubled cost with a known
fix; Idea 12 has a proven in-repo pattern (#1727) to generalize; Ideas 11 and 15
need §7 to instrument crossing counts before they can be ranked at all.

| # | Idea | Effort | Payoff confidence | Kind |
|---|---|---|---|---|
| 10 | Localization layer in Rust (tz + l10n) — **#2209** | Medium | **Correctness — not optional** | Bug fix |
| 14a | Honour `SESSION_ENGINE` — **#2210** | Small | **Correctness** + removes a DB hit | Bug fix |
| 13 | Stop double-building context on save | Small-med | High (for snapshot views) | Waste removal |
| 12 | Memoize the context-processor chain | Medium | Medium-high (chatty views) | Waste removal |
| 11 | i18n — bridge, then maybe port | Small / Medium | Unblocks a population | Feature gap |
| 15 | Batch the custom tag/filter bridge | Medium | Unknown until §7 | Optimization |

---

## 10. Verification log

What the revision pass actually checked, so the next reader knows which claims
carry weight (#1516 — active falsification, not inspection).

**Verified by reading source:**

- `Context::resolve` tries the value stack before the sidecar
  (`context.rs:289-292`) — falsifies §2.1's original example.
- `_serialize_model_safely` recurses into prefetched FKs (`serialization.py:446`)
  and skips reverse relations / M2M; `_meta.get_fields()` never yields
  properties — establishes the sidecar surface table.
- `rust_bridge.py:692` "the eager dict stays the fast path (and wins every hit)".
- `render_nodes_partial` (`renderer.rs:379`) and the text fast path
  (`djust_live/src/lib.rs:552`) exist and do what Idea 2 proposed.
- `test_websocket_mount_counter` (`test_request_path.py:188`) constructs a fresh
  communicator per iteration → cache miss → no clone.
- No `models.Model` subclass anywhere under `tests/benchmarks/`.
- `extract_template_variables` is Rust (`_rust.pyi:181`); `query_optimizer`
  *consumes* a path list rather than producing one; `codegen.py:15` is a second
  consumer.
- `scope["user"]` / `reauth_on_event` (`runtime.py:1253,1266`) — closes Q1.
- `psycopg[binary]>=3.1,<4` (`pyproject.toml:77`), ROADMAP:814.

**Second pass — §9's Django-subsystem sweep.**

*Executed, not merely read* (`.venv/bin/python`, probes under the session
scratchpad — the first thing in this document that was actually run):

- **Timezone bug (§9.2 A) — reproduced twice.** Once against the `date` filter
  directly, once end-to-end through `LiveView.render()` with
  `USE_TZ=True, TIME_ZONE="America/New_York"`. Django emits
  `2026-08-22 19:30`; djust emits `2026-08-22 23:30` for the same UTC input,
  on both the `.isoformat()` and `str()` value shapes.
- **l10n number formatting (§9.2 D).** With `USE_L10N=True,
  USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="de"`,
  `{{ n|floatformat:2 }}` on `1234.5` → Django `'1.234,50'`, djust `'1234.50'`.
- **i18n (§9.2 C).** `{% trans %}` and `{% blocktrans %}` both raise
  `Unsupported template tag` from the Rust parser.
- **`{% url %}` (§9.2 E)** returns `''` where Django raises.

*Verified by reading source:*

- No timezone conversion exists anywhere in `python/djust/` or `crates/` — the
  sole `chrono::Local` is `renderer.rs:1208` (`{% now %}`). This is what makes
  the tz bug structural rather than a probe artifact.
- Session engine hardcoded at `runtime.py:3978` and `websocket.py:3313`; no
  reference to `settings.SESSION_ENGINE` anywhere in the package (§9.2 B).
- `_apply_context_processors` (`mixins/context.py:446`) caches the processor
  *list* but calls each processor per event; `_render_theme_outputs`
  (`theming/context_processors.py:42`) is the one memoized case (#1727).
- `_persist_state_after_event` (`runtime.py:3310`) calls `get_context_data()` a
  second time and re-normalizes the full state, under a 150ms timeout;
  `enable_state_snapshot` defaults `False` (`live_view.py:401`).
- HTML escaping is already Rust (`filters.rs:701`); `{% static %}` /
  `{% csrf_token %}` are parsed in Rust (`parser.rs:625-641`); `resolve()` runs
  only on the `url_change` / `live_redirect` paths, not per event.

**Not verified — treat as reasoning, not evidence:**

- Nothing here was run or profiled. Every performance claim in this document is
  still unmeasured, including the ones the revision pass corrected.
- Ideas 5, 6, 7 and 9 were read but their premises were not independently
  checked the way §2's were.
- Idea 8's browser-API constraint (no custom-dictionary negotiation in RFC 7692
  or `DecompressionStream`) is from knowledge of the specs, not tested against a
  browser here. Worth a 30-minute empirical check before anyone acts on it.
- The probes in the second pass confirm **wrong output**; none of them measured
  **cost**. Ideas 12, 13 and 15 rest on reading the call sites, not on timing
  them — §7's benchmark is still the gate for all three.
- Ideas 10 and 11 assert that `chrono-tz` and `.mo` catalog parsing are the
  right Rust-side mechanisms. Neither was prototyped; the wheel-size cost of
  bundling tzdata in particular is unmeasured and could change the design.
- The l10n probe covered `floatformat` only. Localized month/day names in
  `date` formats are *presumed* affected by the same locale-blindness but were
  not probed.
