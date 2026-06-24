# ADR-019: LiveView Native — Pluggable Rendering for SwiftUI + Jetpack Compose Clients

**Status**: Accepted — LVN-I/II renderer groundwork (widget vocabulary, NativeRenderer scaffold, variant resolver + wiring, author guide; #1578/#1581) shipped v1.1.0rc1 (2026-06-24). The Rust-side widget-VDOM differ that produces real `Patch` streams from native templates is pending in a follow-up sequence (LVN-III/IV).
**Date**: 2026-05-23
**Deciders**: Project maintainers
**Related**:
- [ADR-016](016-transport-runtime-interface.md) — `ViewRuntime` + `Transport` Protocol — the pluggability refactor this ADR extends with a third axis (renderer)
- [ADR-013](013-view-transitions-api-integration.md) — `applyPatches` shape; the contract native clients mirror
- [ADR-011](011-sticky-liveviews.md) / [ADR-014](014-sticky-liveview-autodetect.md) / [ADR-018](018-sticky-child-state-persistence.md) — sticky-LiveView family; native clients must handle reconnect state the same way the browser client does
- [`crates/djust_vdom/src/lib.rs`](../../crates/djust_vdom/src/lib.rs) — `Patch` enum (~lines 432–532) and `VNode` struct
- [`python/djust/websocket.py`](../../python/djust/websocket.py) — patch serialization (~lines 1074–1115), `LiveViewConsumer` handshake
- [`python/djust/mixins/template.py`](../../python/djust/mixins/template.py) — `render_with_diff` (~line 898), the dispatch point for renderer pluggability
- [`python/djust/static/djust/client.js`](../../python/djust/static/djust/client.js) — `applyPatches` (~line 7221); native patch applicators mirror this
- Companion package: [`djust-mobile-toga`](https://github.com/djust-org/djust-mobile-toga) — WebView mode; stays supported alongside native

---

## Summary

djust grows a pluggable **`Renderer`** abstraction so the existing server-side
reactive lifecycle can drive non-HTML targets. We ship two official native
clients — **`djust-native-ios`** (Swift Package, SwiftUI) and
**`djust-native-android`** (Kotlin library, Jetpack Compose) — that connect to
the existing LiveView WebSocket endpoint, consume the same `Patch` opcode
stream the browser receives, and render true native widgets. The
`djust-mobile-toga` WebView path stays supported as the "easy mode" for apps
that want to reuse their web templates verbatim inside a WebView; native is
the "polish mode" for apps that want native UX.

Pattern is borrowed from [Phoenix LiveView
Native](https://github.com/liveview-native/live_view_native) — same idea
("the server is canonical; the client just renders"), djust's implementation.

## Context

### What we have today

djust renders HTML, full stop. The pipeline:

1. A `LiveView` subclass declares a Django template
   (`template_name = "foo/bar.html"`).
2. State changes → `TemplateMixin.render_with_diff()` is invoked
   (`python/djust/mixins/template.py:~898`).
3. The Django template renders an HTML string.
4. The Rust `djust_vdom` crate parses the HTML into a `VNode` tree, diffs
   it against the previous tree, and emits a `Vec<Patch>`
   (`crates/djust_vdom/src/lib.rs:~432–532`).
5. The patches are serialized as msgpack (binary) or JSON and sent over
   the LiveView WebSocket (`python/djust/websocket.py:~1074–1115`).
6. The browser client (`static/djust/client.js:~7221`) applies the patches
   to the DOM, keyed by a base62 `djust_id` per node for O(1) lookup.

The protocol — the `Patch` enum (`Replace`, `SetText`, `SetAttr`,
`RemoveAttr`, `InsertChild`, `RemoveChild`, `MoveChild`, `RemoveSubtree`,
`InsertSubtree`) — is **structurally abstract**. `InsertChild` carries a full
`VNode` (tag, attrs, children, text), not an `innerHTML` string. A native
client could consume the same opcode stream and translate `tag → native
widget`, given a non-HTML widget vocabulary.

The HTML-specific bits live in two places:

- **Tag vocabulary**: templates emit `<div>`, `<span>`, etc.; the Rust
  parser produces `VNode`s with those tag names.
- **Renderer dispatch**: `render_with_diff()` calls into the Rust extension
  with no output-format parameter; HTML is hardcoded.

### The mobile situation

`djust-mobile-toga` puts a Toga `WebView` in front of a loopback uvicorn
server inside an iOS/Android app bundle. It works — MAX Companion ships on
that path — but the cost is real:

- **Bundle size**: ~30 MB of Toga + BeeWare iOS support pack on iOS;
  ~5 MB of equivalent on Android. The "djust" payload itself is single-digit
  MB.
- **Feel**: WebView scroll, WebView gestures, accessibility tree lives in
  the WebView (not the OS), no native navigation transitions, persistent
  "website inside an app" affordance.
- **Per-platform polish**: every native iOS / Android API (haptics, share
  sheet, deep-link routing, native modals) is at arm's length behind a
  WebView bridge.

The user asked the architectural question — *"is Toga the best option, could
we create something better fitted for djust?"* — and the answer settled on:
Toga's widget surface is too thin and too cross-platform to be worth bridging.
The real prize is **native** (SwiftUI / Compose), and the right pattern is
LiveView Native.

### Why now

- ADR-016 already established the pluggability refactor (`ViewRuntime` +
  `Transport` Protocol). The renderer pluggability proposed here is the next
  natural axis on the same refactor.
- The Rust VDOM is mature — the `Patch` enum has been stable through
  ADR-013 (View Transitions integration) and the sticky-LiveView family
  (ADRs 011/014/018). The protocol is ready to carry non-HTML payloads
  without schema change.
- Phoenix LiveView Native has been in production long enough to validate
  the pattern across both iOS and Android.

## Decision

Add a pluggable **`Renderer`** abstraction to djust. Ship two official
native clients. Keep the browser client and `djust-mobile-toga` first-class.

### Three layers

**1. Renderer abstraction (`python/djust/renderers/`)**

```python
class Renderer(Protocol):
    output_format: str   # "html" | "swiftui" | "compose"

    def render(self, template_name: str, context: dict) -> VNode:
        """Emit a VNode tree (not an HTML string).

        The HTML renderer's `render` chains through the existing Django
        template engine → HTML string → Rust parser → VNode (current
        behavior wrapped). The native renderers' `render` skips the
        HTML round-trip and emits widget-shaped VNodes directly from a
        template variant the developer authored as `.swiftui.html` etc.
        """
```

`TemplateMixin.render_with_diff()` (`python/djust/mixins/template.py:~898`)
dispatches to the renderer matched by the connection's `output_format`.
HTML stays default — no breaking change for browser views.

Template variant convention: `foo.html` (default), `foo.swiftui.html`,
`foo.compose.html` — resolved by extension match. Authors keep the
familiar Django template syntax; only the tag vocabulary differs.

**2. Connection-time renderer selection**

`LiveViewConsumer` accepts the renderer choice via WebSocket handshake:

- Query string: `ws://…/ws/live/?platform=swiftui`
- OR custom WebSocket subprotocol (`Sec-WebSocket-Protocol: djust.swiftui`)

Defaults to `html`. The chosen renderer flows into `ViewRuntime` (the
abstraction from ADR-016), which threads it into `render_with_diff`.

**3. Baseline widget vocabulary (12 widgets — SwiftUI ∩ Compose)**

Each `VNode` tag maps directly to a native widget. The MVP set covers ~80%
of typical screens:

| Tag | SwiftUI | Compose |
|---|---|---|
| `<Stack>` | `VStack` | `Column` |
| `<HStack>` | `HStack` | `Row` |
| `<ZStack>` | `ZStack` | `Box` |
| `<Text>` | `Text` | `Text` |
| `<Button>` | `Button` | `Button` |
| `<TextField>` | `TextField` | `TextField` |
| `<Toggle>` | `Toggle` | `Switch` |
| `<List>` | `List` | `LazyColumn` |
| `<Image>` | `Image` | `Image` |
| `<ScrollView>` | `ScrollView` | `Modifier.verticalScroll` |
| `<Spacer>` | `Spacer` | `Spacer` |
| `<NavigationView>` | `NavigationView` | `NavHost` |

Event attributes: `dj-tap` (analog of `dj-click`), `dj-change`, `dj-input`.
Styling: a constrained subset — `padding`, `spacing`, `alignment`,
`foregroundColor`, `font`. The ADR commits to *this set* for v1; growth
goes through follow-up ADRs.

### Two client libraries

**`djust-native-ios`** (Swift Package, separate repo
`djust-org/djust-native-ios`):

- Public API: `DjustLiveView(url: URL)` returns a SwiftUI `some View`
- Internals: `URLSessionWebSocketTask` for transport, an msgpack decoder,
  a patch applicator that maintains a tree of `Identifiable` widget
  descriptors keyed by the same base62 `djust_id` the browser uses, and
  an event sender that round-trips `dj-tap` etc. back to the server with
  the same payload shape `client.js` uses.
- Distribution: SwiftPM, consumable via
  `https://github.com/djust-org/djust-native-ios`.

**`djust-native-android`** (Kotlin library, separate repo
`djust-org/djust-native-android`):

- Public API: `@Composable fun DjustLiveView(url: String)`
- Mirror structure: OkHttp `WebSocket`, msgpack decoder, patch applicator
  to a Compose `MutableState`-backed widget tree, event sender to server.
- Distribution: Maven Central artifact, Gradle dependency.

Both clients implement the *same patch applicator contract* the browser
client implements (ADR-013 already documents the four-phase ordering:
Remove → Move → Insert → other). Differences are only in the leaf
operations (`SetText` on `Text` widget vs DOM text node, etc.).

## Architecture

```
                      ┌──────────────────┐
                      │  LiveView (Py)   │
                      │  state, events   │
                      └────────┬─────────┘
                               │
                  TemplateMixin.render_with_diff()
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
        ┌──────────────┐ ┌────────────┐ ┌────────────┐
        │ HtmlRenderer │ │  SwiftUI   │ │  Compose   │
        │   (default)  │ │  Renderer  │ │  Renderer  │
        └──────┬───────┘ └─────┬──────┘ └─────┬──────┘
               │               │               │
               │  ┌────────────┴───────────────┘
               │  │  VNode (tag vocabulary varies)
               ▼  ▼
        ┌──────────────────────────────────┐
        │  djust_vdom (Rust) — diff/patch  │
        │  Patch enum — unchanged          │
        └─────────────────┬────────────────┘
                          │
              ViewRuntime (ADR-016) — owns dispatch
                          │
                  Transport (ADR-016)
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
    ┌─────────┐      ┌─────────┐      ┌─────────┐
    │ client.js│      │ Swift   │      │ Kotlin  │
    │  (DOM)   │      │ (SwiftUI)│      │(Compose)│
    └─────────┘      └─────────┘      └─────────┘
```

### What changes in djust core

- **NEW**: `python/djust/renderers/__init__.py`, `renderers/html.py`,
  `renderers/native.py`
- **EDIT**: `python/djust/mixins/template.py` — `render_with_diff` accepts
  a renderer from the runtime, falls back to `HtmlRenderer` for
  back-compat. ~50 lines.
- **EDIT**: `python/djust/websocket.py` — handshake reads `platform` param,
  passes it into `ViewRuntime` construction. ~20 lines.
- **EDIT**: `python/djust/runtime.py` (the file ADR-016 introduced) —
  `ViewRuntime.__init__` takes the renderer. ~10 lines.

### What does NOT change

- **`crates/djust_vdom`** — Rust VDOM crate is renderer-agnostic. `VNode`
  and `Patch` shapes are unchanged. Native renderers produce VNodes with
  widget-shaped tag names; the diff/patch engine doesn't care what the
  tags mean.
- **`static/djust/client.js`** — Browser client is unchanged. The default
  renderer's output is byte-identical to today's.
- **Wire format** — Same msgpack/JSON, same `Patch` opcodes, same
  `djust_id` scheme.

## Consequences

### Pros

- True native UX on iOS + Android: native scroll, gestures, accessibility
  tree, navigation transitions, haptics, system-integration affordances.
- Bundle size shrinks ~20 MB on iOS / ~3 MB on Android by dropping the
  WebView + Toga overhead for apps that go native-only.
- Same Python view code drives three targets (browser, iOS, Android). The
  reactive lifecycle, event handlers, state model — unchanged.
- djust enters the SwiftUI / Compose ecosystem without giving up the
  "one Python stack" principle.
- The renderer abstraction is a third axis of pluggability that complements
  the `ViewRuntime` + `Transport` work from ADR-016; future targets
  (terminal UI, native macOS, embedded screens) cost a renderer module,
  not a framework fork.
- Phoenix LiveView Native is proof the pattern works at scale.

### Cons

- Three new artifacts to maintain: `djust-native-ios`, `djust-native-android`,
  and the `Renderer` abstraction in djust core. Two new build/test
  pipelines (Xcode-based + Gradle-based).
- App authors targeting both web AND native maintain parallel templates
  per screen (`home.html` + `home.swiftui.html` + `home.compose.html`).
  The shared content is the data-binding markup (`{{ var }}`,
  `dj-tap="..."`, `{% for %}`); the structural markup is different.
- Widget vocabulary is fixed in v1. New widgets require ADRs + version
  bumps in three places (core + two clients). Pace is intentionally slow.
- Event-handler argument-dispatch quirks (the named-parameter requirement
  documented in `scratch/diag_argdispatch.py` of consumer apps) apply
  identically to native — the Swift/Kotlin clients must serialize event
  args the same way `client.js` does.
- Sticky-LiveView state persistence (ADRs 011/014/018) is currently
  WebSocket+browser-tested. Native client implementations must mirror
  the reconnect / restore semantics; will likely surface bugs.

### Neutral

- `djust-mobile-toga` continues to ship and stays supported. Apps choose
  the model per-app:
  - **WebView mode** — reuse the web templates verbatim, ship in
    `djust-mobile-toga`, get something on a phone in an afternoon.
  - **Native mode** — author per-screen `*.swiftui.html` / `*.compose.html`
    variants, ship with `djust-native-ios` / `djust-native-android`,
    pay the parallel-template cost for true native UX.
- Browser client is byte-identical to today's. No external regression
  surface from the renderer refactor — `HtmlRenderer` wraps the existing
  pipeline.
- MAX Companion stays on `djust-mobile-toga` as the WebView-mode
  reference; it could later add a native variant as the LiveView Native
  reference, demoing both modes.

## Iteration plan

Each iteration is one or more PRs. Roman numerals because the work spans
multiple repos and won't all land in a single djust version.

### Iter I — Renderer abstraction in djust core

`python/djust/renderers/` with `Renderer` Protocol; `HtmlRenderer`
extracted from the existing `render_with_diff`; `ViewRuntime` plumbing
to pass the renderer through. HTML stays default. No external behavior
change. Lands in djust as a minor version (e.g. v1.1.0).

**Gate**: existing demo project + max-companion `make verify` continue
passing 11/11. djust's own test suite green.

### Iter II — Widget vocabulary + `NativeRenderer`

`python/djust/renderers/native.py` produces VNodes with the 12-widget
vocabulary above. A reference template engine for `.swiftui.html` /
`.compose.html` extensions; Django template tags adapted to emit
widget-shaped VNodes (most `{% if %}`, `{% for %}`, `{{ var }}` work
unchanged; only structural tags differ).

**Gate**: server emits valid widget-VNode patches over the existing WS
contract; can be exercised end-to-end with a stub client that just
deserializes and prints.

### Iter III — Swift Package `djust-native-ios` v0.1

Separate repo. SwiftPM. Implements `DjustLiveView` SwiftUI view with
the patch applicator + event sender. MAX Companion home screen as the
pilot (re-implement `home.html` as `home.swiftui.html`, run on iOS sim
side-by-side with the WebView build).

**Gate**: pilot screen renders identically (visually equivalent) on iOS
sim; dismiss-alert event round-trips through the existing
`HomeView.dismiss_alert` handler; BeneficiaryPreferences write-back
(ADR-style persistence from max-companion PR #7) survives a relaunch.

### Iter IV — Kotlin library `djust-native-android` v0.1

Mirror of Iter III on Android. Same pilot screen.

**Gate**: same as Iter III, on Android emulator.

### Iter V — Documentation + the v1.0 vocabulary lock

Author guide for native templates. Migration guide for `djust-mobile-toga`
users who want to adopt native incrementally. Vocabulary frozen at v1.0
of each client library; future widgets via ADR.

## Verification

How a reviewer confirms the architecture is sound *before* Iter I lands.

1. **Protocol re-confirmation**: walk through `crates/djust_vdom/src/lib.rs`
   `Patch` enum and `VNode` struct; confirm none of the variants embed
   raw HTML strings (e.g., no `Patch::SetInnerHTML(String)`). If any
   variant turns out to be HTML-specific, this ADR's wire-format claim
   needs revision before Iter I starts. *Spot-checked during ADR drafting
   — confirmed clean as of v1.0.0rc9.*
2. **Renderer dispatch site**: read `TemplateMixin.render_with_diff`
   (`python/djust/mixins/template.py:~898`) end-to-end. Confirm there's
   exactly one HTML-rendering call site to abstract. *Confirmed by
   exploration during plan; one site.*
3. **ViewRuntime integration**: confirm `ViewRuntime` from ADR-016 has a
   construction site reachable from `LiveViewConsumer.handle_mount`; the
   renderer needs to flow through that path.
4. **Pilot screen exists**: max-companion's `medicare/views.py::HomeView`
   is the agreed pilot. Confirm template + view are simple enough to
   re-implement in native widgets (1 stack, 1 alert card with a button,
   a tile grid of 8 cards — well within MVP vocabulary).

## Alternatives considered

### Alt 1 — HTML-to-native auto-translation

Native clients ingest the existing HTML-shaped patches and translate
`<div class="alert">` → SwiftUI `VStack`, `<p>` → `Text`, etc. Rejected:
the translation surface is unbounded (CSS, layout, every web idiom),
the output is lossy (no native idiom recognition), and any non-trivial
design breaks. Same reason React Native didn't adopt this approach for
React Web components.

### Alt 2 — Status quo (WebView only)

Defer. Ship MAX Companion on `djust-mobile-toga`, revisit when there's
demand. Rejected because (a) the "djust on mobile" story is a product
positioning question that gets answered better by being able to show
native UX, and (b) the renderer abstraction is itself useful for
non-mobile targets (terminal UI, embedded screens) that this ADR
doesn't preclude.

### Alt 3 — Toga widget renderer

Server emits a Toga widget tree; both iOS and Android render via Toga.
Rejected because Toga is itself a cross-platform widget abstraction —
we'd pay the entire bridging cost (renderer abstraction + Toga client)
and *still* not get true native feel. The bridging makes sense only when
the target widget toolkit is one developers actually want their app to
look like; Toga isn't that.

### Alt 4 — Full SDK pivot (Flutter / React Native target)

Generate Flutter or React Native code from djust views. Rejected:
abandons djust's "one Python stack" principle, forces a JS/Dart layer
into every consumer, and the codegen surface dwarfs the
direct-WS-protocol approach.

### Alt 5 — Renderer abstraction without committing to native clients

Land Iter I only; let third parties build native clients on top.
Rejected per user input (scope chose "full stack"): the protocol-only
deliverable doesn't move the product story forward and risks shipping
an abstraction that turns out to be the wrong shape for the clients
that don't yet exist.
