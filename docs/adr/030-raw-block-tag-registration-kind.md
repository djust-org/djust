# ADR-030: Body-consuming template tags render through the bridge that already exists

**Status**: Proposed
**Date**: 2026-09-16
**Citations**: `file:line` pinned to `main` at `b3d5d5ac`; every one asserted against its expected token at write time (see §7 for what the two earlier drafts got wrong, and how)
**Deciders**: Project maintainers
**Related**:
- [ADR-001](001-tag-handler-registry.md) — the tag-handler registry this extends
- Issues: #2558 (the raw-body registration kind), #2547 (the `{% load %}` bridge that introduced the refusal)
- `docs/TEMPLATE_BACKEND.md:220` — "Refused, per tag" is the documented contract this ADR revises
- `ROADMAP.md:141` — #2558's i18n half, landed 2026-09-03 (#2597)
- `CHANGELOG.md:887` — the refusal's introduction; `CHANGELOG.md:919` — the raw-body kind as built

---

## Summary (plain language first)

When a Django template library is loaded with `{% load %}` on djust's Rust template engine, each of its tags is wired up by a *bridge*. The bridge handles every tag type Django offers except one: a hand-written `@register.tag` that wraps a body (`{% callout %}…{% endcallout %}`). Those are refused with an error that tells the author to "wait for the raw-body registration kind (#2558)".

Two earlier drafts of this ADR proposed remedies for that refusal. Both were wrong for the same reason: they did not notice that the bridge **already has a handler that renders body-wrapping tags correctly**. `LibraryBlockTagHandler` (`python/djust/template_libraries.py:1157`) exists for Django's `simple_block_tag`: the Rust engine renders the body, then hands Django's compile function a two-token stream, `[TEXT(rendered body), BLOCK(end<name>)]` (`:1207`), so the tag's own `parser.parse(("end<name>",))` and `delete_first_token()` consume the rendered body as one `TextNode`. A hand-written wrapper tag consumes exactly that stream. Fed through the handler **unchanged**, the five component tags the gallery reports as broken all render correctly (§Measured).

**Decision:** route a body-consuming raw tag through `LibraryBlockTagHandler` when a registration-time probe proves it is a plain wrapper; keep refusing, loudly, when it is not. The raw-body kind (`LibraryRawBlockTagHandler`, `:1442`) stays reserved for tags whose body is *data* (`blocktranslate`). No native handlers are added, no Rust changes, no opt-in flags.

What this means for a developer:

- **Size.** One new branch in `_bridge_tag` (`:755`), a ~20-line probe, tests, and two documentation lines. The handler doing the work is already in the tree and already exercised by every `simple_block_tag`.
- **Performance.** The probe runs once per tag when its library loads (microseconds; it compiles the tag against two tokens). Per render, the path is the same one every `simple_block_tag` and every native component handler already takes: Rust renders the body, one Python call wraps it. Nothing that works today gets slower.
- **Compatibility.** Additive only. The 52 native component handlers keep priority (`_may_override`, `:816`). `simple_tag`, `simple_block_tag`, `inclusion_tag` and `blocktranslate` are untouched. A refused tag either starts working (it passed the probe) or keeps failing with a clearer message. Nothing changes for projects on Django's own template engine.

## Context

### The refusal, and the rationale it states

`_classify` (`:840`) sorts bridged tags into `simple_tag` / `simple_block_tag` / `inclusion_tag` / `raw`. A raw tag is then tested by `_consumes_body` (`:872`), a source-text heuristic over `_BODY_CONSUMERS` (`:869`). On a match, `RefusedTagHandler` (`:1124`) is installed (`:791-799`) and the Rust parser raises Django's `TemplateSyntaxError` at the tag's first use — per tag, so the rest of the library still bridges (`CHANGELOG.md:887`). The rationale is written down (`docs/TEMPLATE_BACKEND.md:220`): the tag "has no token stream to consume here".

### The three mechanisms that already exist

The rationale is stale because the tree now holds three ways to give a body-consuming tag a token stream. Which one is right depends on what the tag does with its body:

| mechanism | body arrives as | who renders the body | used today by |
|---|---|---|---|
| `LibraryBlockTagHandler` (`:1157`) — the **rendered-body** bridge | `[TEXT(rendered), BLOCK(end)]` (`:1207`) | Rust, then Django's node wraps the result (`_render_node`, `:1030`) | every `simple_block_tag` |
| `LibraryRawBlockTagHandler` (`:1442`) — the **raw-body** kind | un-rendered source, re-lexed by Django's `Lexer` | Django's Python nodes | `blocktranslate` / `blocktrans` (`:172`) |
| native handlers (`components/rust_handlers.py:2005`, `BLOCK_HANDLERS`) | rendered string | Rust; a hand-written Python handler emits the wrapper | 52 `djust_components` tags, installed at app ready (`components/apps.py:10-13`, `rust_handlers.py:2039`) |

A wrapper tag — one whose node does `self.nodelist.render(context)` inside its own markup — wants the first row. `blocktranslate`, whose body is a msgid, needs the second. The third is the first row with the Python node replaced by a hand-maintained copy, which is what the component library did before the bridge existed.

### The population, measured

Enumerating every library `get_installed_libraries()` reports with `djust.components` installed, and keeping tags where `_classify` is `"raw"` and `_consumes_body` is true:

| | count |
|---|---|
| distinct raw body-consuming tags refused by the bridge | 67 |
| — in `djust_components` | 57 |
| — of those, **already served by a native handler** in `BLOCK_HANDLERS` | **52** (`accordion`, `card`, `modal`, `tabs`, `split_pane`, `wizard`, …) |
| — with no handler at all | **5**: `aspect_ratio`, `callout`, `scroll_area`, `sticky_header`, `tab` |
| — in `i18n` / `tz` / `l10n` / `cache` (already handled natively or bespoke) | 7 |
| — in `live_tags` / `djust_formsets` | 3 |

The five are exactly the five the component gallery reports as `TemplateSyntaxError`. So the first-party gap is five tags, not 57, and the bridge census must be intersected with native registration before it can support any decision (§7, item 1).

### The five, measured through the existing handler

Each of the five has the same compile shape (`djust_components.py:4205` for `callout`; `aspect_ratio`, `scroll_area`, `sticky_header` and `tab` are identical in form):

```python
kwargs = _parse_kv_args(bits, parser)
nodelist = parser.parse(("endcallout",))
parser.delete_first_token()
return CalloutNode(nodelist, kwargs)
```

Constructing `LibraryBlockTagHandler("djust_components", name, lib.tags[name])` and calling `.render(args, mark_safe("<b>BODY</b>"), {})` — no change to the handler — gives, for all five: the tag's own markup around the body, the body kept verbatim and unescaped. `callout` yields `<div class="dj-callout dj-callout--info">…<b>BODY</b>…</div>`; `tab` yields its body. The mechanism the refusal says to "wait for" has been serving this shape since `simple_block_tag` was bridged.

### What a wrapper is, and what is not one

Two shapes must **not** take the rendered-body route, and both are detectable:

1. **Intermediate separators.** `split_pane` (`djust_components.py:3045`, `:3047`) calls `parser.parse(("pane",))` and then `parser.parse(("endsplit_pane",))`. Its terminator follows the `end<name>` convention — as do all 65 tags in the census that have a literal end token — but it needs two body segments, and a single rendered `TextNode` cannot supply them. (`split_pane` has a native handler; the gallery's three `{% pane %}` failures are that handler not knowing the intermediate token, a separate small fix.)
2. **Nodelist introspection.** `TabsNode` selects its children by type: `tabs = [n for n in self.nodelist if isinstance(n, TabNode)]` (`:185`); there are 15 such sites in the library. Given a rendered `TextNode` body, such a node finds no children and renders wrongly — *silently*. (All 15 parents have native handlers, so none reaches the bridge today; the probe exists so that a third-party parent of this shape cannot either.)

### Nesting is already handled on this path

The rendered-body route enters the Rust parser as a block handler. `parse_block_custom_tag` (`crates/djust_templates/src/parser.rs:2630`) parses children by recursion (`:2649`), so an inner `{% card %}` consumes its own `{% endcard %}` before the outer one is looked for. The raw-body route does not have this: `collect_raw_source` (`:2537`) returns at the first end token it meets (`:2547`), which is correct for `blocktranslate` (Django forbids nesting it) and wrong for any content block. That asymmetry is one more reason content bodies belong on the rendered-body route.

## Decision Drivers

1. **Fail loud beats fail silent.** A tag djust cannot bridge must raise, naming itself and the reason. A probe that passes a nodelist-introspecting parent would convert a loud refusal into silent wrong output; the probe's second check exists for that.
2. **Use the mechanism already in the tree.** The rendered-body handler is exercised by every `simple_block_tag`; nothing new enters the engine.
3. **Keep rendering in Rust.** VDOM identity, native tags and native filters inside a block all depend on the Rust engine parsing the body. The raw-body kind loses all three; the rendered-body route and native handlers keep them.
4. **One source of truth per tag.** A native handler is a hand-written copy of a Django node (52 exist; each is a drift risk of the parallel-path class). Routing the Django node itself removes the need to write more.
5. **A census must be intersected across mechanisms** before it supports a decision.
6. **The documented contract is part of the change** (`docs/TEMPLATE_BACKEND.md:220`).

## Options Considered

**A. Port the affected tags to `@register.simple_block_tag`.** Rejected: it is a Django 5.2 API while `pyproject.toml` floors at 4.2; it cannot express nodelist-introspecting parents; and it pushes work onto every library author.

**B. Route every raw body-consuming tag through `LibraryRawBlockTagHandler`.** The first draft's choice. Rejected: the body would be rendered by Django's Python nodes, so native Rust tags and filters inside it stop working and the VDOM cannot stamp identity on elements it never parsed; nesting needs new depth tracking in `crates/`; and 52 of the 57 first-party tags never reach the bridge anyway.

**C. Keep the refusal; document it harder.** Rejected. The documentation asserts a rationale ("no token stream") that two handlers in the tree contradict, and five first-party tags would stay broken.

**D. Native handlers for the five in `rust_handlers.py`.** The second draft's choice. Workable, and it is how the other 52 were done. Rejected in favour of E because it adds five more hand-maintained copies of Python nodes, helps no third-party library, and is strictly more code than E for the same result.

**E. Route wrapper-shaped raw tags through `LibraryBlockTagHandler`, gated by a behavioural probe at registration.** *Chosen.* Closes the five gallery sites and every third-party wrapper tag in one change, adds no Rust, adds no handlers, keeps rendering in Rust, and inherits nesting from `parse_block_custom_tag`. Non-wrapper shapes stay refused, with the message naming which check failed.

**F. Native Rust scope nodes for the whole family.** Rejected: reimplementing component semantics in Rust is what the bridge exists to avoid.

## Decision

**D1 — A behavioural probe classifies a body-consuming raw tag at registration.** Before installing any handler, the tag's compile function is called with a counting `Parser` over `[TEXT("probe"), BLOCK("end<name>")]`. The tag is a *wrapper* when both hold:

1. it made exactly one `parser.parse` call, and its stop set was `("end<name>",)`;
2. the returned node's `render` output contains the probe text.

Measured on this tree: `callout` passes both (`[('endcallout',)]`, body kept). `split_pane` fails (1): its first call is `parse(("pane",))`, and the empty stream then raises. `tabs` and `accordion` pass (1) and fail (2): they introspect the nodelist for typed children and a `TextNode` is not one. The probe is behavioural, not textual — it does not read source, so `inspect.getsource` limitations do not apply to it.

**D2 — A wrapper is routed through `LibraryBlockTagHandler`**, registered with `register_block_tag_handler(name, "end<name>", handler)` exactly as a `simple_block_tag` is (`:789`). The end tag is the convention, which the probe has just verified for this tag; it is never derived from source.

**D3 — Everything else stays refused, loudly.** `RefusedTagHandler` narrows to tags that fail the probe, and its message names the failing check ("uses an intermediate token", "inspects its nodelist") and the remedy (a native handler, or `simple_block_tag` for a Django 5.2+ library). The raw-body kind is **not** widened: it remains the treatment for data bodies (`_RAW_BLOCK_TAGS`, `:172`) and gains no opt-in.

**D4 — Native registration keeps precedence.** `_may_override` (`:816`) is unchanged; the 52 component handlers, `cache` (`_BESPOKE_BLOCK_TAGS`, `:167`) and `blocktranslate` are not displaced.

**D5 — `docs/TEMPLATE_BACKEND.md:220` and the refusal message are updated in the same change.** The line stops saying "has no token stream to consume here" and instead states what is bridged (a wrapper) and what is refused (an intermediate-token or introspecting tag), with the remedy for each.

## Security

The rendered-body route is the `simple_block_tag` route, so its escaping story is inherited, not new: the body reaches the handler as a `SafeString` the Rust engine already escaped (#2379), Django's node renders through `_render_node` (`:1030`) with the active `autoescape`, and the node's own output is inserted as Django would insert it. The probe compiles and renders the tag once against a literal `"probe"` body and an empty context at registration; it executes only code the library author wrote, in the same process that would run it on the first render anyway. Two things to verify rather than assume in review: a wrapper node that assigns into the context (`as var`) must report bindings through the same channel `simple_block_tag` does; and the probe must run under the same `_may_override` guard, so a probe never runs for a name djust already owns.

## Consequences

**Positive.** The five gallery failures close, and so does the same shape in any third-party library, with no new handlers and no engine change. `docs/TEMPLATE_BACKEND.md:220` becomes true. The refusal message stops pointing at a mechanism as if it were future work.

**Negative, accepted.** Tags that fail the probe are still refused. That is the status quo, now with a precise reason. `{% pane %}` remains a separate fix in the native `split_pane` handler.

**Neutral.** The `end<name>` convention stays load-bearing, and the probe verifies it per tag instead of assuming it.

## Sequencing

1. **Probe and routing** (`_bridge_tag`, `:755`): the `elif kind == "raw" and _consumes_body(...)` branch (`:791`) runs the probe and installs `LibraryBlockTagHandler` or `RefusedTagHandler` accordingly. Tests first; the gallery's five sites are the oracle.
2. **Message and docs** (D3, D5), in the same PR.
3. **`{% pane %}` intermediate support** in the native `split_pane` handler — independent, can land before or after.

No Rust step.

## Verification

- **The gallery is the end-to-end oracle.** The five refused sites render; asserted by tag, not by spot-check.
- **The probe's second check is load-bearing.** `tabs` (or a synthetic tag that iterates its nodelist) must be refused; gating check (2) off must make that test fail. This is the gate-off that proves D1 is not decorative.
- **The probe's first check is load-bearing.** `split_pane` must be refused by the bridge (it is served natively regardless); a synthetic two-segment tag must be refused with the "intermediate token" message.
- **Nesting.** `{% callout %}` inside `{% callout %}` renders both bodies in place, on the bridge path, without any parser change.
- **Rust-native content survives.** A body containing a native djust tag and a `dj-*` attribute renders through the bridged wrapper with the attribute present and the tag resolved — the property the raw-body route would have lost.
- **Parity.** `make django-template-suite` does not regress; the path is the one `simple_block_tag` already takes.

## Non-goals

- **Not a Rust change.** `parse_block_custom_tag` already does what is needed.
- **Not a widening of the raw-body kind**, and not an opt-in mechanism for it. Data bodies keep it; content bodies never take it.
- **Not a `simple_block_tag` deprecation.** It remains the right shape for new libraries on Django 5.2+.
- **Not a replacement of the 52 native handlers.** They keep precedence. Whether wrapper-shaped ones could later be retired in favour of the bridge is a separate question with its own measurement.
- **Not a gallery change.** The gallery's theming defects were fixed independently.

## 7. Record of what the earlier drafts got wrong

Kept because the errors are instructive, in the style of ADR-029 §7.

1. **A census of one mechanism treated as a census of the system** (first draft). 57 bridge refusals was correct; "57 unusable" ignored the 52 native handlers registered at app ready.
2. **A regex took the first match where the last was the answer** (first draft). `split_pane`'s first `parser.parse` literal is an intermediate; the terminator follows the convention. The draft's derivation rule would have cut the body at the wrong token.
3. **A non-goal that was load-bearing** (first draft). "Not a Rust engine change" while the raw-body flip needed depth tracking in `crates/`.
4. **An options analysis that omitted the mechanism already in the tree** (first *and* second drafts). Both weighed `simple_block_tag`, the raw kind and native handlers; neither tried `LibraryBlockTagHandler` on the failing tags, which takes one line and settles the question. The lesson generalises: when the tree already has N handlers for a shape, run the failing input through each before designing an N+1th.
5. **Recommending hand-written copies over the original** (second draft). Native handlers work, but each is a parallel implementation of a Python node; the bridge renders the node itself. Prefer the single source when it is available.
6. **A self-contradiction** (first draft). "Adds no new failure mode" next to a correct note that `inspect.getsource` can fail. The behavioural probe removes the source dependency entirely.
7. **An unverified version number** (first draft). `simple_block_tag` is Django 5.2, not 5.0.
