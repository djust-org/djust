# ADR-030: Raw block-consuming template tags — native handlers first, bridge second

**Status**: Proposed
**Date**: 2026-09-16
**Citations**: `file:line` pinned to `main` at `b3d5d5ac`; every one re-asserted against its expected token at rewrite time (see §7 for the two the first draft got wrong, and how)
**Deciders**: Project maintainers
**Related**:
- [ADR-001](001-tag-handler-registry.md) — the tag-handler registry this extends
- Issues: #2558 (the raw-body registration kind), #2547 (the `{% load %}` bridge that introduced the refusal)
- `docs/TEMPLATE_BACKEND.md:220` — "Refused, per tag" is the documented contract this ADR revises
- `ROADMAP.md:141` — #2558's i18n half, landed 2026-09-03 (#2597)
- `CHANGELOG.md:887` — the refusal's introduction; `CHANGELOG.md:919` — the raw-body kind as built

---

## Summary

A raw `@register.tag` whose compile function reads past its own token is refused at parse time, and the message points at "the raw-body registration kind (#2558)" (`python/djust/template_libraries.py:1140-1146`). **That kind already exists** — `LibraryRawBlockTagHandler` (`:1442`) synthesizes a token stream from the body and renders the library's own Django node against a real `Context` — and the stated rationale for refusing everything else ("has no token stream to consume here", `docs/TEMPLATE_BACKEND.md:220`) is not true of it.

The first draft of this ADR concluded from that, incorrectly, that the fix was to route every raw body-consuming tag through the handler. **It is not, and the census that supported it was wrong in a way that inverted the recommendation.** djust's component tags do not rely on the `{% load %}` bridge at all: `djust.components` registers **52 native block handlers** with the Rust engine at app ready (`components/apps.py:10-13` → `rust_handlers.py:2020`, table at `:2005`), and native registration takes precedence over a bridged name. So of the 57 raw body-consuming `djust_components` tags, **52 are never refused**, and the real first-party gap is **five tags** — which are exactly the five the component gallery reports.

This ADR therefore decides the opposite of its first draft:

1. **Fix the five first-party tags the way the other 52 are fixed** — native block handlers in `rust_handlers.py`. Closes all eight gallery failures without touching routing.
2. **Narrow the bridge change to third-party libraries**, opt-in rather than a flip of every body-consuming tag, and only with depth-tracked scanning in Rust (`§Nesting`) and the behavioural caveats below documented.
3. **Drop source-derivation of end tags.** The convention `end<name>` holds for every tag measured (65/65); a deviating name must be validated by a compile-time probe rather than read from a `parser.parse` call, which yields the wrong token for a tag with an intermediate separator.

## Context

### The refusal, and the rationale it states

`_classify` (`template_libraries.py:840`) sorts bridged tags into `simple_tag` / `simple_block_tag` / `inclusion_tag` / `raw`. A raw tag is tested by `_consumes_body` (`:872`), a source-text heuristic over `_BODY_CONSUMERS` (`:869`). On a match, `RefusedTagHandler` (`:1124`) is installed and the Rust parser raises at the first use — per tag, so the rest of the library still bridges (`CHANGELOG.md:887`). The rationale is written down (`docs/TEMPLATE_BACKEND.md:220`): the tag "has no token stream to consume here".

### The machinery the rationale overlooks

`LibraryRawBlockTagHandler` (`:1442`) **is** the raw-body kind: it re-lexes the body with Django's own `Lexer`, appends the end token, and calls the library's own compile function on a synthetic `Parser` (`:1476-1478`); its `render` builds a real `Context` and calls the real node's `.render(ctx)` (`:1505-1508`). It ships for `blocktranslate`/`blocktrans` (`:172`). Its docstring frames it for *data* bodies ("the body is Django's to parse … the
msgid data ``blocktranslate`` builds its catalog key from", `:1443-1452`), which is why the generalisation was not obvious — but the handler does not choose: it hands the body to Django's lexer and the node's own `render` decides. **That much of the first draft was right and survives.**

### The population, measured — and where the first draft went wrong

The first draft enumerated raw body-consuming tags through the bridge and reported "67 refused, 57 of them djust's own". Reproduced, those counts are correct. **The inference was not.** `djust.components` does not go through the bridge: `DjustComponentsConfig.ready()` calls `register_with_rust_engine()` (`components/apps.py:10-13`), which registers `INLINE_HANDLERS` (`rust_handlers.py:1980`, 141 entries) and `BLOCK_HANDLERS` (`:2005`, **52 entries of `(tag_name, end_tag, handler)`**, installed at `:2039-2040`).

Intersecting the two sets:

| | count |
|---|---|
| raw body-consuming tags in `djust_components` (bridge census) | 57 |
| **of which already have a native block handler** | **52** — `accordion`, `card`, `modal`, `tabs`, `split_pane`, `wizard`, … |
| **with no handler at all** | **5** — `aspect_ratio`, `callout`, `scroll_area`, `sticky_header`, `tab` |

Those five are precisely the five the component gallery reports as `TemplateSyntaxError`. So "57 of djust's own component tags are unusable on the Rust engine" is false — the true statement is **five**, and the established remedy for a component tag is the pattern the other 52 use, not a routing change.

This is the load-bearing correction: **the bridge census measures a population that native registration already covers.** A census of one mechanism must be intersected with the other mechanisms that shadow it before it can support a decision.

### End-tag resolution: the convention holds, and the first draft's witness was inverted

`LibraryRawBlockTagHandler.__init__` sets `self.end_name = "end" + name` (`:1467`) — an assumption, not a derivation. The first draft claimed `split_pane` deviates from it (`split_pane` → `{% pane %}`), and used that as the reason a derivation rule was required. **That is wrong.** The source (`djust_components.py:3040-3049`):

```python
pane1 = parser.parse(("pane",))           # :3045 — an INTERMEDIATE separator
parser.delete_first_token()               # consume {% pane %}
pane2 = parser.parse(("endsplit_pane",))  # :3047 — the TERMINATOR
parser.delete_first_token()               # consume {% endsplit_pane %}
```

`pane` is an intermediate; the end tag is `endsplit_pane`, which follows the convention. **65 of 65 tags with a literal end token follow `end<name>`.** The first draft's extraction regex took the *first* `parser.parse` literal in the source, which is the intermediate — so it reported a deviation that does not exist, and its rule ("read the literal passed to `parser.parse`") would have produced `pane` and cut the body at the wrong token, shipping the silent-wrong-parse class that same section claimed to prevent.

Two consequences: the convention is the rule (validated by probe, not trusted), and `split_pane` is not evidence for a derivation — it is evidence that a naive one is dangerous.

The first draft also mis-filed the gallery's three `{% pane %}` failures as the bridge's subject. `split_pane` has a native handler; those failures are the native block parser not knowing `pane` as an intermediate token. They are the native-handler work, not the bridge's.

### Nesting — the constraint that puts Rust back in scope

The first draft listed "Not a Rust engine change" as a non-goal. It is not available. `collect_raw_source` (`crates/djust_templates/src/parser.rs:2537`) scans forward and returns at the first token in `end_names`:

```rust
Token::Tag(name, _) if end_names.contains(&name.as_str()) => return Ok((content, j)),  // :2547
```

No depth counter, no nesting stack. That is correct for `blocktranslate` — Django forbids nesting it — but a content block nests trivially (`{% card %}` inside `{% card %}`, `{% accordion_item %}` inside an accordion inside a card). With a raw-body flip, the outer body would be cut at the inner end tag. **The remedy is depth counting in `collect_raw_source`, which is a `crates/` change**, and it is a precondition of the routing work rather than a follow-up.

The gallery would not catch this: its example templates contain no same-name nesting.

### A rendered body leaves the Rust engine

For `blocktranslate` the body is data. For a content block the body is **rendered by Django's Python nodes and returned to Rust as an opaque string**. Two consequences the first draft did not state:

- Native Rust tags and filters stop working *inside* the body, as does anything the synthetic `Parser` does not carry from a `{% load %}`.
- The Rust engine cannot stamp VDOM identity on elements it never parses, so a LiveView patch inside such a block degrades to replacing the whole block. A `{% callout %}` wrapping a form with `dj-*` bindings is a visible behavioural change.

Neither is fatal, and both are precisely why the 52 native handlers exist. They are the axes on which "route through Django nodes" must be compared against "write a native handler" — a comparison the first draft did not make, because it weighed only `simple_block_tag`.

## Decision Drivers

1. **Fail loud beats fail silent.** A tag djust cannot compile should raise, naming itself. Any widening must preserve that; a *guessed* end tag is worse than a refusal.
2. **Use the mechanism the population already uses.** 52 of 57 first-party tags have native handlers; the five without are an omission, not a different problem.
3. **A census must be intersected across mechanisms.** The bridge census and native registration describe overlapping populations.
4. **Nesting is a precondition, not a follow-up.** Cutting a body at an inner end tag is silent corruption.
5. **Django stays the reference.** The raw handler runs Django's compile functions and nodes; nothing here reimplements node semantics.
6. **The documented contract is part of the change** (`docs/TEMPLATE_BACKEND.md:220`).

## Options Considered

**A. Port the affected tags to `@register.simple_block_tag`.** Rejected: it is a Django **5.2** API while `pyproject.toml` still declares the 4.2 classifier, it cannot express the 15 composed relationships (`djust_components.py:185` and 14 others), and it pushes work onto every library author including the 52 first-party tags that already work.

**B. Route every raw body-consuming tag through `LibraryRawBlockTagHandler` ("the flip").** The first draft's choice. **Rejected**: it is the wrong remedy for the five first-party tags (native handlers are their established pattern and avoid the VDOM and native-tag costs entirely), and as a blanket change it is unsafe for third parties until depth tracking lands.

**C. Keep the refusal; document it harder.** Rejected — it is already documented, and the documentation asserts a rationale the handler already in the tree contradicts. Five first-party tags would stay broken for no reason.

**D. Native handlers for the five; bridge change narrowed and opt-in for third parties.** *Chosen.* It closes the entire observed gap using the pattern 52 tags already use, and leaves third-party libraries a path that is opt-in and gated on the Rust precondition rather than a flip that trades one silent failure for another.

**E. Native Rust scope nodes for the whole family.** Rejected as a general remedy — reimplementing 57 component tags with their kwargs, `as var`, escaping and composition in Rust is what the bridge exists to avoid. It is, however, exactly right for the five, which is why it is D's first half.

## Decision

**D1 — The five first-party tags get native block handlers.** `aspect_ratio`, `callout`, `scroll_area`, `sticky_header` and `tab` are registered in `rust_handlers.py`'s `BLOCK_HANDLERS` table like the other 52, plus `pane` intermediate-token support (or a `split_pane` handler that reads `{% pane %}` itself) so the eight gallery failures close. No bridge routing changes for these.

**D2 — The end tag is never derived from source.** `end<name>` is the rule, corroborated by the measurement above (65/65). Because a raw tag may use an intermediate separator and `simple_block_tag` accepts an explicit `end_name`, a hand-written or third-party tag that deviates must be **validated, not inferred**: the registration path compiles a probe body and refuses loudly if the convention does not hold. The first draft's rule — read the literal passed to `parser.parse` — is withdrawn as unsound.

**D3 — Any bridge widening is opt-in, and gated on depth-tracked scanning.** A third-party library's raw body-consuming tags are routed through `LibraryRawBlockTagHandler` only when the library opts in (per library or per tag), and only after `collect_raw_source` (`parser.rs:2537`) counts depth so a nested same-name block cannot truncate its parent. Until that lands, third-party behaviour is unchanged — the refusal — which is the status quo rather than a regression.

**D4 — `docs/TEMPLATE_BACKEND.md:220` and the refusal message are updated to match whatever D1–D3 ship**, and to say plainly that the first-party remedy is a native handler rather than `simple_block_tag`.

## Security

Routing through Django's own nodes keeps escaping, `is_safe` and `needs_autoescape` Django's — no escaping decision is added. Under D3, three points must be verified rather than assumed: `WANTS_AUTOESCAPE` (`:1494`) must be set on the general path, or `{% autoescape off %}` around such a block escapes where Django inserts raw (`:1485-1494`); bindings recovery diffs `ctx.dicts[-1]` (`:1509-1515`), which is sound for a data-block node and untested for a content-block node that assigns outward; and a body is lexed on the Python side, which is parse-time and cached per `(args, body)` (`:1473-1479`), but widens that surface and should be measured.

## Consequences

**Positive.** Eight gallery failures close with no routing change and no new engine semantics. Five tags join the 52 that already work, using the mechanism a maintainer would reach for first. The stale rationale in `docs/TEMPLATE_BACKEND.md:220` is corrected.

**Negative, accepted.** The first draft's "67 refusals become 67 working tags" does not happen, and should not: 52 of those were already working, and the remaining third-party and i18n tags have a correct treatment under D3 that is a deliberate opt-in. A third party still cannot use a raw body-consuming tag without opting in — unchanged from today, and honest about why.

**Neutral.** The convention stays load-bearing, now *validated by probe at registration* rather than assumed or inferred.

## Sequencing

1. **Native handlers for the five**, plus `pane` intermediate support. Self-contained, closes all 8 gallery sites, needs no Rust change. Ships first.
2. **Depth-tracked `collect_raw_source`**, with a nested-same-name fixture that fails before the change. Rust-side; a precondition for step 3, not for step 1.
3. **Opt-in routing for third-party libraries**, with the native-tag, filter and VDOM caveats documented in `docs/TEMPLATE_BACKEND.md`.
4. **Narrow `RefusedTagHandler`** to libraries that have not opted in, and update the message.

## Verification

- **The gallery is the end-to-end oracle.** All 8 remaining render sites resolve, asserted category by category rather than by spot-check.
- **`split_pane` is the convention's witness, read correctly.** It must render, and a fixture must assert the *terminator* is `endsplit_pane` — the assertion that, run against the first draft's extraction, would have failed.
- **D2's probe is load-bearing.** A synthetic raw tag whose end token deviates must be refused at registration; gating the probe off must make that test fail.
- **The nesting fixture must fail without depth counting** — a `{% card %}` inside a `{% card %}` — or step 2 has no evidence.
- **Composition is pinned, not sampled.** Each of the 15 introspection sites (`djust_components.py:185` and others) gets a rendered assertion that its parent still sees typed children.

## Non-goals

- **Not a Rust change for step 1.** The five native handlers are Python-side registrations; only step 2 is `crates/`.
- **Not a `simple_block_tag` deprecation.** It remains right for new third-party libraries.
- **Not a fix for the composed family's fragility** — 15 introspection sites is worth revisiting, separately.
- **Not a gallery change.** The gallery's theming defects were fixed independently; step 1 removes the last reason its components could not render.

## 7. Record of what the first draft got wrong

Kept because the errors are instructive, in the style of ADR-029 §7.

1. **A census of one mechanism treated as a census of the system.** 57 bridge refusals was correct; concluding "57 unusable" ignored that `djust.components` registers 52 native handlers at app ready and never goes through the bridge. The remedy changes hands entirely.
2. **A regex took the first match where the last was the answer.** `split_pane`'s first `parser.parse` literal is an intermediate separator; the terminator is `endsplit_pane`. The draft built its central "the convention cannot be assumed" claim on that, and its proposed rule would have cut the body at the wrong token.
3. **A non-goal that was load-bearing.** "Not a Rust engine change" was stated while the flip required depth-tracked scanning in `crates/`; nested same-name blocks would have truncated silently, and the gallery's fixtures would not have caught it.
4. **An options analysis against one alternative.** `simple_block_tag` was weighed; the native-handler pattern that 52 tags already use — and that avoids the VDOM and native-tag costs — was not considered at all.
5. **A self-contradiction.** Option D claimed the derivation "adds no new failure mode" while Consequences correctly noted `inspect.getsource` fails for zip-imported or REPL-defined functions. The claim was the wrong half and is gone with D2.
6. **An unverified version number.** The draft said `simple_block_tag` is Django 5.0; it is **5.2** (present in the pinned 5.2.16, which also carries its `end_name` parameter). The argument does not depend on the number, which is the reason it should not have been asserted without checking.
