# ADR-030: Raw block-consuming template tags render through the library's own node

**Status**: Proposed
**Date**: 2026-09-16
**Citations**: `file:line` pinned to `main` at `7cd93724`; every one asserted against its expected token at write time
**Deciders**: Project maintainers
**Related**:
- [ADR-001](001-tag-handler-registry.md) — the tag-handler registry this extends
- Issues: #2558 (the raw-body registration kind), #2547 (the `{% load %}` bridge that introduced the refusal)
- `docs/TEMPLATE_BACKEND.md:220` — "Refused, per tag" is the documented contract this ADR changes
- `ROADMAP.md:141` — #2558's i18n half, landed 2026-09-03 (#2597)
- `CHANGELOG.md:887` — the refusal's introduction; `CHANGELOG.md:919` — the raw-body kind as built

---

## Summary

An app's `@register.tag` whose compile function reads past its own token is refused at parse time, per tag, with a message pointing at "the raw-body registration kind (#2558)" (`python/djust/template_libraries.py:1140-1146`). **That kind is already built and already ships.** `LibraryRawBlockTagHandler` (`:1442`) synthesizes a token stream from the body, calls the library's own compile function on a synthetic `Parser`, and renders the resulting real Django node against a real `Context` (`:1476-1478`, `:1505-1508`). It is installed for exactly two tags, `blocktranslate` and `blocktrans` (`:172`).

The refusal's stated rationale — a body-consuming raw tag "has no token stream to consume here" (`docs/TEMPLATE_BACKEND.md:220`) — is therefore not true of the machinery djust now owns. This ADR decides to route **every** raw body-consuming tag through the handler that already exists, resolving the one real obstacle (the end-tag name) by **static derivation from the compile function's source, corroborated against the `end<name>` convention, and refused loudly when neither yields an answer** — never guessed. Measured on this tree, that converts 67 refusals into 67 working tags, and it is what makes djust's own component library usable on the Rust engine at all: 57 of those 67 are `djust_components`, and the gallery surfaces five of them as `TemplateSyntaxError` today.

## Context

### The refusal, and the rationale it states

`_classify` (`template_libraries.py:840`) sorts every bridged tag into `simple_tag` / `simple_block_tag` / `inclusion_tag` / `raw`. A raw tag is then tested by `_consumes_body` (`:872`), a source-text heuristic over `_BODY_CONSUMERS = ("parser.parse(", ".parse((", "next_token(", "skip_past(", "parse_until")` (`:869`). If it matches, `RefusedTagHandler` (`:1124`) is installed instead of a working handler, and the Rust parser raises Django's `TemplateSyntaxError` the moment a template uses that tag — per tag, so the rest of the library still bridges (`CHANGELOG.md:887`).

The rationale is written down (`docs/TEMPLATE_BACKEND.md:220`): the tag "has no token stream to consume here". Two remedies are offered — port the tag to `@register.simple_block_tag`, or wait for the raw-body kind.

### The machinery the rationale overlooks

Both remedies are already partly satisfied.

`LibraryRawBlockTagHandler` (`:1442`) **is** the raw-body kind. It re-lexes the body with Django's own `Lexer`, appends the end token, and calls the library's own compile function on a synthetic `Parser` (`:1476-1478`) — so arguments, `as var`, escaping and inclusion rendering are Django's byte for byte. Its `render` builds a real `Context` and calls the real node's `.render(ctx)` (`:1505-1508`), then diffs the context to recover bindings.

The docstring frames it for `blocktranslate`, "the body is DATA — the msgid" (`:1443-1451`), which is why the generalisation was not obvious: a *data* body must cross un-rendered, and a *content* body must be rendered. But the handler does not choose — it hands the body to Django's lexer and the node's own `render` decides. `BlockTranslateNode` treats its body as data; `CalloutNode` would render its nodelist. **One handler serves both shapes**, which is the fact this ADR turns on.

Note also that `_BESPOKE_BLOCK_TAGS = {"cache": "endcache"}` (`:167`) already exists: a hand-maintained `{name: end_tag}` map is an established pattern in this file, and `_end_name` (`:881`) already derives an end name for the `simple_block_tag` case with the same `f"end{name}"` default the raw handler hardcodes (`:1467`).

### The population, measured

Enumerating every library `get_installed_libraries()` reports, classifying each tag, and testing `_consumes_body` (this tree, `7cd93724`):

| | |
|---|---|
| **Distinct raw body-consuming tags refused today** | **67** |
| — in `djust_components` (djust's own component library) | 57 |
| — in `i18n` / `tz` / `l10n` / `cache` (already native or already handled) | 7 |
| — in `live_tags` / `djust_formsets` (djust's own) | 3 |

The refusal is therefore not an edge case for exotic libraries; it is the reason **57 of djust's own component tags are unusable on the Rust engine**, and by extension why the component gallery — which renders 188 components through these tags — cannot work there.

### End-tag resolution: the one real obstacle, measured

`LibraryRawBlockTagHandler.__init__` sets `self.end_name = "end" + name` (`:1467`) — an assumption, not a derivation. Extracting each of the 67 compile functions' literal end token from source (`parser.parse(("<end>",))`):

| Shape | Count | End tag |
|---|---|---|
| `parser.parse(("end<name>",))` | **64** | Convention holds |
| `parser.parse(("pane",))` — `split_pane` (`djust_components.py:3040`, parse at `:3045`) | **1** | Literal in source, **differs** from convention |
| `parser.next_token()` loop — `blocktrans` / `blocktranslate` | **2** | Non-literal; already handled by the existing raw kind |

So the convention covers 95.5%, one tag needs the literal read from source, and the two that resist both approaches are the two that already work. **Every one of the 67 is resolvable** — but only by a rule that derives rather than assumes, because `split_pane` is proof that the assumption is not sound.

### Composition — the axis the refusal hides

`TabNode` is introspected by its parent: `tabs = [n for n in self.nodelist if isinstance(n, TabNode)]` (`djust_components.py:185`). There are **15** such `isinstance(<node>, *Node)` sites in the component library — `TabNode`, `AccordionItemNode`, `SidebarItemNode`, `NavItemNode`, `AppSidebarNode`, `AppHeaderNode`, `AppContentNode`, `ToolbarSeparatorNode`, `FilterSelectNode` and others.

This is decisive for *how* the routing is done, and it excludes the obvious workaround:

- **A parent and its children must be routed by the same mechanism.** Routing `callout` through the raw handler while leaving `tabs` refused is safe (they are unrelated). Routing `callout` while routing `tabs` through a *different* mechanism would break composition.
- **`simple_block_tag` cannot express the composed family at all.** It returns a rendered string; a parent that introspects its nodelist for typed children would find none. The error message's first suggested remedy is therefore structurally unavailable for 15 relationships — which is exactly the shape of the gallery's `{% pane %}` failures, whose real cause is `split_pane` consuming a body (`:3045`), not a missing tag.
- The raw handler *does* satisfy composition, because it builds a real `Parser` and a real nodelist: a `TabsNode` constructed through it contains genuine `TabNode` children.

### The gallery, measured

The component gallery renders each example through the project's template backend. Against a `DjustTemplateBackend`-only project, **245 render sites failed** — the gallery's whole population of fallbacks; routing snippets through the configured backend recovered 237, and **the 8 that remain are this ADR's subject** — `callout`, `aspect_ratio`, `tab`, `sticky_header`, `scroll_area` (refusals) and the `{% pane %}` body of `split_pane`.

## Decision Drivers

1. **Fail loud beats fail silent.** The refusal is correct in kind: a tag djust cannot compile should raise, naming itself, rather than render nothing. Any widening must preserve that property — a *guessed* end tag would silently mis-parse a body, which is worse than refusing.
2. **The machinery exists; the gap is policy, not capability.** `LibraryRawBlockTagHandler` is the raw-body kind the error message tells users to wait for.
3. **djust's own components are the largest consumer.** 57 of 67 are `djust_components`; the gallery is the visible symptom.
4. **Composition is a hard constraint.** 15 introspection sites, and `simple_block_tag` cannot serve them.
5. **Django's own reference must stay the reference.** The handler already runs Django's compile functions and nodes; extending the routing must not introduce a second rendering path.
6. **The refusal is documented.** `docs/TEMPLATE_BACKEND.md:220` is the public contract; changing it is part of the change.

## Options Considered

**A. Port the affected tags to `@register.simple_block_tag`.** The message's first remedy. Rejected: `simple_block_tag` is Django 5.0+ while `pyproject.toml` floors at `Django>=4.2`; it cannot express the 15 composed relationships; and it pushes the work onto every library author, including the 57 in djust's own component library.

**B. Keep the refusal; document it harder.** Rejected. It is already documented; the documentation is the problem — it asserts a rationale ("no token stream") that the handler already in the tree contradicts, and it makes 57 first-party tags unusable on the flagship engine.

**C. Route every raw body-consuming tag through `LibraryRawBlockTagHandler`, end tag by convention `end<name>`.** Cheap and covers 64/67. **Rejected as insufficient**: `split_pane` uses `{% pane %}`, so this would build a `SplitPaneNode` whose parser never sees its expected token and mis-parse the body — a silent-wrong-output failure, the class this repo has paid for repeatedly.

**D. Route them through `LibraryRawBlockTagHandler`, deriving the end tag from the compile function's source via `inspect.getsource`, corroborated against the convention, refused loudly when undeterminable.** *Chosen.* `inspect.getsource` is already the mechanism `_consumes_body` uses (`:872`), so this adds no new dependency and no new failure mode; it recovers the literal for 66 of 67 and correctly declines the `next_token` pair, which the existing raw kind already handles.

**E. Native Rust scope nodes, as ADR-022's convergence did for `{% language %}`/`{% localize %}`.** Rejected for this population. That pattern works for a small fixed set with simple children; reproducing 57 component tags — with their kwargs, `as var`, escaping and composition — as Rust nodes would be a reimplementation of Django's node semantics, which is precisely what the bridge exists to avoid.

## Decision

**D1 — The end tag is derived, corroborated, or refused — never assumed.** For a raw body-consuming tag, the end tag is read from the compile function's source (a literal passed to `parser.parse`/`parse_until`). It is then corroborated against `end<name>`; a disagreement is not an error (it is `split_pane`'s legitimate shape) but a divergence worth recording. **When no literal can be read, the tag stays refused** — the loud failure is retained rather than converted into a guess.

**D2 — Raw body-consuming tags route to `LibraryRawBlockTagHandler`.** `RefusedTagHandler` narrows to the genuinely undeterminable case instead of the whole shape. The handler's docstring and `_RAW_BLOCK_TAGS` (`:172`), which currently read as a `blocktranslate`-specific allowlist, become general.

**D3 — Composition is preserved by using the same mechanism for parents and children.** No member of a composed relationship is routed by a different mechanism from its parent, and none is ported to `simple_block_tag`. The 15 introspection sites are the invariant to pin.

**D4 — `docs/TEMPLATE_BACKEND.md:220` and the refusal message are updated as part of the change**, not after it. A contract that describes a limitation djust no longer has is the drift this repo has paid for most often.

## Security

The handler renders through Django's own nodes against a real `Context`, so escaping, `is_safe` and `needs_autoescape` remain Django's — the bridge adds no escaping decision. Three points to verify rather than assume:

- **`WANTS_AUTOESCAPE`** (`:1494`) must be set on the general path as it is for `blocktranslate`, or `{% autoescape off %}` around a component would escape where Django inserts raw (`:1485-1494`).
- **Bindings recovery** diffs `ctx.dicts[-1]` (`:1509-1515`). A content-block node that assigns into an outer scope could report bindings differently from a data-block node; `{% callout %}`-shaped tags do not assign, but the composed family includes tags that do.
- **A body is now lexed on the Python side.** It was already lexed for `blocktranslate`; widening the population widens the surface. The body is template source authored by the developer, not request data, and the node is compiled once per `(args, body)` (`:1473-1479`) — so this is a parse-time cost, not a per-request one, but it should be measured.

## Consequences

**Positive.** 67 tags stop refusing, 57 of them djust's own. The component gallery's last 8 render sites resolve — including all three `{% pane %}` failures, which turn out to be `split_pane`'s body rather than a missing tag. `simple_block_tag` remains the recommendation for *new* libraries (it is declarative and version-appropriate); the raw kind becomes the safety net for existing ones.

**Negative, accepted.** djust gains a second route into `LibraryRawBlockTagHandler` and therefore a second population whose bodies cross the boundary — a widening that must be measured against the template-suite scoreboard rather than assumed neutral. Static source reading is a heuristic: `inspect.getsource` fails on a compile function defined in a REPL or a zip-imported module, in which case D1 leaves the tag refused — the same behaviour as today, not a regression.

**Neutral.** The convention `end<name>` stays the common case (64/67) but is no longer load-bearing.

## Sequencing

Dormant-define → wire → flip, per the repo's convergence pattern (ADR-022), because the flip changes routing for 67 tags at once:

1. **Define the derivation, dormant.** Add end-tag derivation (D1) and its tests. `RefusedTagHandler` still installs for every body-consuming raw tag; assert that `LibraryRawBlockTagHandler` is never installed for one. No routing change.
2. **Measure the population against the Django suite.** Record the scoreboard before any flip (`make django-template-suite`, `.django-src/last-run.txt`), so the flip's effect is attributable.
3. **Flip, per library, starting with `djust_components`.** The 57 first-party tags are the rationale; they are also the ones whose rendering the component gallery asserts end-to-end, so the flip has a real oracle. Django's own libraries follow.
4. **Narrow `RefusedTagHandler`** to the undeterminable case and update the message, `docs/TEMPLATE_BACKEND.md:220`, and `CHANGELOG.md`.

## Verification

- **The gallery is the end-to-end oracle.** All 8 remaining render sites resolve, asserted category by category, not by spot-check.
- **Composition is pinned, not sampled.** Each of the 15 introspection sites gets a rendered assertion that its parent still sees typed children — a `{% tabs %}` containing `{% tab %}` must produce a `TabsNode` holding `TabNode`s.
- **`split_pane` is the derivation's witness.** It is the one tag whose end token differs from convention; it must render, and a fixture must assert the derived name is `pane`, not `endpane`.
- **The refusal is retained where it is still correct.** A synthetic raw tag whose end token is computed must still refuse loudly — the gate-off that proves D1's "never guessed" clause is load-bearing rather than decorative (the decorative-pin failure class).
- **Django-suite parity.** The `make django-template-suite` scoreboard must not regress; the raw-block path already renders Django's own nodes, so parity is the expectation, and a regression means the derivation guessed.

## Non-goals

- **Not a Rust engine change.** The parsing happens in Rust, but the routing decision and the derivation are Python-side; no `crates/` change is proposed here.
- **Not a `simple_block_tag` deprecation.** It remains the right shape for new libraries and is unaffected.
- **Not a fix for the composed family's other fragilities.** 15 introspection sites is a design worth revisiting on its own, but not in this ADR.
- **Not a gallery change.** The gallery's own theming defects are separate and were addressed independently; this ADR removes the last reason its components could not render.
