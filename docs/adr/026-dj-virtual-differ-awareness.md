# ADR-026: `dj-virtual` differ awareness — reconciling a server-rendered list against a client-windowed DOM

**Status**: Accepted — Option A taken. Iterations 1-2 shipped in v1.1.0rc9 (PRs [#2126](https://github.com/djust-org/djust/pull/2126), [#2135](https://github.com/djust-org/djust/pull/2135), fix [#2146](https://github.com/djust-org/djust/pull/2146)); iteration 3 — the flag flipped ON by default — shipped in 1.1.1 once #2185 and #2194 gave the browser gate a working control arm.
**Date**: 2026-07-25
**Deciders**: Project maintainers
**Related**:
- [#2017](https://github.com/djust-org/djust/issues/2017) — items 2, 3, 4. This ADR is the design deliverable for them; items 1 and 5 shipped separately.
- [ADR-025](025-js-extension-sockets.md) — extension sockets; the adapters milestone that rides on them
- #1988 / #1989 — the client-side self-heal this builds on
- #2113 — path-fallback landing a patch on the wrong node
- #1646 — parallel-path drift, the failure class this design must not create

## Context

`dj-virtual` renders only a window of a large list. Off-window items are
**detached** from the DOM and held in the client's `state.items`.

The server does not know that. It renders the **whole** list and diffs the
whole list. `grep -rn "dj-virtual" crates/` returns **zero** — the Rust differ
has no concept of a client-windowed subtree.

So the two sides disagree about what the DOM contains:

| | server's model | client's DOM |
|---|---|---|
| 10 000-row feed | 10 000 nodes | ~20 nodes (window + overscan) |

Every patch the differ emits for that subtree addresses a node the client may
not have attached. Three consequences, which are #2017 items 2–4:

1. **Patches for off-window items cannot land.** The node is not in the DOM.
2. **Positional paths are meaningless inside the window.** `path: [0, 7]` means
   "the 8th child" to the differ and "the 8th *visible* child" to the DOM.
3. **Insert position is lost.** The client-side absorb (#1989) appends loose
   rows at the tail, which is right for an append-only feed and wrong for an
   insert in the middle.

### What already mitigates this

Shipped, and deliberately *not* superseded by this ADR:

- **#1989** — the client self-heals a clobbered shell and absorbs loose rows.
- **#2017 item 5** — a patch miss on an off-window item now names `dj-virtual`
  as the cause instead of listing three wrong ones.
- **#2113** — a patch whose `dj-id` is held detached no longer falls back to a
  positional path and silently mutates the wrong row.
- **#2017 item 1** — stream ops route through the item pool, so `prepend` lands
  at the front and `prune` trims the pool rather than the window.

Together these make the failure **loud and safe** rather than silent and
corrupting. What they do not do is make a mid-window update *land*.

## Decision

**Option A — keyed splice ops for `[dj-virtual]` subtrees.**

This section read "**Not yet.** This ADR records the options and recommends
one; it does not authorise implementation" until v1.1.0rc9, by which point two
of its three iterations had shipped. Recorded plainly because that is the
#1867 class — a prose invariant nobody had run against the code — and
`make check-adr-status` did not catch it: it audits status/version-line
*consistency*, not whether a status matches reality.

Where the three iterations from Consequences actually stand:

| iteration | state |
|---|---|
| 1. differ emits keyed splice ops, flag default OFF | shipped, PR #2126 |
| 2. client applies them to the pool | shipped, PR #2135 |
| 3. flag flips ON after a soak | **shipped, 1.1.1** — gate passed once #2185 + #2194 gave a valid control arm |

Both halves of iteration 3 have now shipped. `virtual_keyed_ops` reaches the
differ via `DjustConfig.ready()` (a module-level PyO3 function, because
`VIRTUAL_KEYED_OPS` in `crates/djust_vdom/src/diff.rs` is a process global, not
per-view state), and the Python default flipped to `True` in 1.1.1.

The Rust static itself stays `false` deliberately: `ready()` applies the Python
value on every startup, so Python is the single source of truth, and leaving the
static off means an embedder that never runs Django fails safe rather than
fail-open.

The browser gate is why this took as long as it did, though not for the reason
first recorded here.

Measured against a 60-row list, inserting at server position 5 with `k0`
scrolled out of the window:

| | flag OFF | flag ON |
|---|---|---|
| pool index of the new row | 60 (tail) | 60 (tail) |
| expected | 5 | 5 |

> **SUPERSEDED 2026-08-11 — this table and the paragraphs that followed it
> were wrong, twice over.** The measurement itself was taken while the flag
> never actually reached Rust (the config read returned a default — #2164 /
> #2166, fixed in PR #2167), so BOTH arms above are really the OFF arm. The
> conclusion drawn from it — "iteration 2's client applier does not honour
> `before_key`" — was #2164's second diagnosis and is withdrawn.
>
> Re-measured in a browser with the flag genuinely ON and the list confirmed
> initialised before acting:
>
> | | flag OFF | flag ON |
> |---|---|---|
> | pool index of the new row | n/a (ordinary patch path) | **5** |
> | remove at 3 | — | k3 dropped, no duplicates |
> | reverse | — | exact reversal, no lost keys |
> | edit row 0 | — | lands on `k0` only |
>
> Every ON case reported `Patches applied successfully` with no recovery
> round-trip. **Iterations 1 and 2 are both correct.**
>
> Iteration 3 WAS blocked on **#2185** (fixed in PR #2195) and **#2194** (PR #2196): `[dj-virtual]` initialisation is
> intermittently lost on page load (the #1610 mount morph re-creates the
> `dj-root` subtree and nothing re-runs `initVirtualLists`). On an affected
> load the flag OFF degrades silently — the server sends an ordinary
> `InsertChild`, which applies — while ON sends `VirtualInsert` at an
> uninitialised container, failing the patch and forcing a full HTML
> recovery. (#2185 and #2194 were both fixed — PRs #2195 and #2196 — and the
> default flipped ON in 1.1.1.)
>
> **The superseded text, verbatim, for the record:**
>
> > The OFF arm landing at the tail is exactly what this ADR predicts, so the
> > A/B is capable of distinguishing — and it shows the flag does not change
> > the outcome.
> >
> > The differ is NOT at fault. With the flag on it emits
> > `VirtualInsert { key: "ins0", before_key: "k5" }` — the keyed positioning
> > this ADR designed. Iteration 1 is correct. **Iteration 2's client applier
> > is what does not honour it**, appending at the tail instead of splicing
> > before `k5`. Tracked as #2164.
>
> (The A/B table above is retained in place rather than moved; the paragraph
> below this block concerns a still-earlier withdrawn diagnosis and is
> unrelated to this supersession.)

A first pass recorded the cause as "the list is not windowed at patch
time". That was wrong: it came from an A/B whose CONTROL arm also failed,
which cannot distinguish "the flag does nothing" from "a shared upstream
failure masks it" — and the control failure was an artifact of the test's
own scroll step. Recorded because a permanent ADR asserting a cause the
experiment could not support is worse than one that says "inconclusive".

Gating it: flipping changes VDOM behaviour for every `[dj-virtual]` user, so
per #1122 it is taken only on evidence from real browser verification — the
#1988/#1989 failure class was DOM state that unit tests structurally cannot
see. #2136 (PR #2146), the other recorded blocker, is now cleared.

## Options considered

### Option A — differ emits keyed splice ops for a `[dj-virtual]` subtree

Teach the differ that a subtree marked `dj-virtual` is client-windowed, and for
its children emit **key-addressed** operations (`InsertBefore(key)`,
`Move(key)`, `Remove(key)`, `Update(key)`) instead of positional patches.

The client applies them to `state.items`, and the window re-renders from the
pool. The DOM is never addressed directly.

- **Pro** — the only option that makes a mid-window update *correct* rather
  than merely safe. It also subsumes items 3 and 4: an off-window update
  mutates the pool entry (item 3), and a keyed insert lands at its key position
  (item 4).
- **Pro** — the machinery mostly exists. `diff.rs` already has keyed child
  reconciliation with an LIS pass (`lis.rs`) for minimal moves.
- **Con** — a new wire-protocol shape. Per #1448 it needs snapshot pinning, and
  per #1541 the serde field ordering needs checking for both encodings.
- **Con** — the differ must learn a client-rendering concern. That is a real
  layering cost and the main argument against.

### Option B — client-side patch buffering

Keep emitting ordinary patches. The client buffers those it cannot land and
replays them when the item scrolls in.

- **Pro** — no protocol change, no differ change.
- **Con** — unbounded buffer on a long-lived feed, and replay ordering against
  later patches for the same node is genuinely hard.
- **Con** — does not fix item 4 at all: a buffered insert still has no position.
- **Verdict** — rejected. It converts a correctness problem into a memory and
  ordering problem.

### Option C — server-side windowing

The server renders only the visible window and diffs that.

- **Pro** — the two sides would agree exactly.
- **Con** — the server must track each client's scroll offset, making render
  stateful per connection. That is contrary to the framework's model and would
  put scroll position on the wire at scroll frequency.
- **Verdict** — rejected.

### Option D — status quo plus the shipped mitigations

Accept that mid-window updates to off-window items do not land, and rely on the
diagnostic (#2017 item 5) to explain why.

- **Pro** — zero cost; already in place.
- **Con** — the documented `dj-virtual` + streams pairing remains partly
  aspirational for anything other than append-only feeds.
- **Verdict** — this is the current state, and an acceptable one to hold while
  the demand for Option A is unproven.

## Why defer

Three reasons, in order of weight:

1. **No confirmed demand.** The reported pain (#1988/#1989, snake-arena, the
   #1724 chart case) was **append-only feeds and teardown**, all of which the
   shipped mitigations now cover. Nobody has yet reported a mid-window keyed
   update failing.
2. **High blast radius.** This touches the Rust differ, the wire protocol, and
   the client patch applier at once. Per the split-foundation rule (#1122) it
   wants to be sequenced behind a soak of the pieces that just landed — four
   PRs touched `12-vdom-patch.js` in the last day.
3. **The cheap half is done.** Items 1 and 5 delivered most of the practical
   value. What remains is the expensive half.

## Non-goals

- Server-side scroll tracking (Option C).
- Changing `dj-virtual`'s client-side windowing model.
- Superseding the #1989 self-heal — it remains the safety net regardless.

## Consequences

**If Option A is taken later**, it should be split per #1122:

1. Differ emits keyed splice ops for `[dj-virtual]` subtrees, behind a config
   flag, default OFF. Wire-format snapshot tests first (#1448), with the serde
   field-position check from #1541.
2. Client applies them to the pool. Reuses `_virtualInsert` / `_virtualPrune`
   from #2017 item 1 rather than adding a second path (#1646).
3. Flag flips ON after a soak.

**Testing** this would require, per repo canon: wire-format pinning for every
new op shape; gate-off siblings for each behavioural test; and — the specific
trap here — assertions on **order and position**, not counts. #2017 item 1's
first tests counted pool size and passed with the fix disabled, because the
#1989 absorb already produced the right count for the wrong reason.

**If it is never taken**, `dj-virtual` remains documented as suited to
append-only and replace-whole-list workloads, which is what it is good at
today. That should be stated in `large-lists.md` rather than left implicit.
