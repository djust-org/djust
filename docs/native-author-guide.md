# Authoring djust native screens

LVN-V (initial cut) — author-facing guide for the LiveView Native track
shipped via [ADR-019](adr/019-liveview-native.md).

This is the first draft. It covers what works **today** (renderer
abstraction, handshake, vocabulary, template-variant resolver) and
labels what's pending (Rust-side widget VDOM walker; Swift / Kotlin
client implementations beyond the scaffolds).

## When to use native vs WebView

djust now ships two mobile paths. Use the one whose trade-offs match
your app.

| Path | Server changes | Client | UX | Bundle size | Per-screen authoring |
| - | - | - | - | - | - |
| **WebView** ([`djust-mobile-toga`](https://github.com/djust-org/djust-mobile-toga)) | None — your existing `.html` templates ship verbatim | Toga `WebView` shell around your djust server | Web-feel (WebView scroll, gestures, accessibility tree) | ~30 MB iOS / ~5 MB Android of Toga + BeeWare runtime | Zero — reuse your web screens |
| **Native** ([`djust-native-ios`](https://github.com/djust-org/djust-native-ios) + [`djust-native-android`](https://github.com/djust-org/djust-native-android)) | Add `.swiftui.html` / `.compose.html` variants per screen | Real SwiftUI / Jetpack Compose | Native (native scroll, gestures, accessibility tree, navigation) | ~20 MB less on iOS / ~3 MB less on Android | Per-screen native variant alongside the HTML |

You can mix: ship most screens via WebView, render one or two key
screens natively for the polish bump. The server reactive lifecycle is
identical.

## How variant resolution works

djust uses a file-extension convention. Given a base template name and
the connection's `?platform=` value, the renderer picks:

```
medicare/home.html        ← base (browser, ?platform= absent or "html")
medicare/home.swiftui.html ← native iOS (?platform=swiftui)
medicare/home.compose.html ← native Android (?platform=compose)
```

If a variant doesn't exist on the template loader path, the renderer
falls back to the base HTML. No 500 — the native client gets HTML and
will report a tag-vocabulary mismatch downstream (which is the right
error layer).

The resolver lives at `python/djust/renderers/template_resolver.py`:

```python
from djust.renderers.template_resolver import resolve_variant

resolve_variant("medicare/home.html", "swiftui")
# → "medicare/home.swiftui.html" if it exists, else "medicare/home.html"
```

## The v1 widget vocabulary

Native templates author against the [12-widget vocabulary](native-widget-vocabulary.md)
(SwiftUI ∩ Jetpack Compose intersection). The frozen Python source of
truth is `python/djust/renderers/widgets.py`; the iOS + Android clients
mirror it in `WidgetTags.swift` / `WidgetTags.kt`.

Example native template:

```html
{# medicare/home.swiftui.html #}
<Stack spacing="12" padding="16">
    <Text font="title">Hello, {{ first_name }}</Text>

    {% if show_alert %}
    <Stack padding="12" foregroundColor="#14457E">
        <Text font="headline">Your screening is due</Text>
        <Button dj-tap="dismiss_alert">Dismiss</Button>
    </Stack>
    {% endif %}

    <List>
        {% for tile in tiles %}
        <Button dj-tap="navigate({{ tile.href|escapejs }})">
            <Text>{{ tile.label }}</Text>
        </Button>
        {% endfor %}
    </List>
</Stack>
```

Notes:
- Django template syntax works unchanged (`{% if %}`, `{% for %}`, `{{ var }}`).
- Event attributes: `dj-tap`, `dj-change`, `dj-input`. The `@event_handler`
  Python decorators on the server are identical to the browser path.
- Style attributes from a constrained subset (`padding`, `spacing`,
  `alignment`, `foregroundColor`, `font`). Anything else is silently
  ignored by the native clients (graceful degradation).

## Connection-time selection

Native clients connect with `?platform=swiftui` or `?platform=compose`
in the LiveView WebSocket URL:

```swift
DjustLiveView(url: URL(string: "ws://127.0.0.1:8111/ws/live/")!)
// SwiftUI client auto-appends ?platform=swiftui
```

```kotlin
DjustLiveView(url = "ws://127.0.0.1:8111/ws/live/")
// Compose client auto-appends ?platform=compose
```

The server's `LiveViewConsumer` parses the param, looks the value up in
`RENDERERS` (`python/djust/renderers/__init__.py`), and passes the
matched factory to `ViewRuntime`. Browser-side connections send no
`?platform=` → factory is `None` → `HtmlRenderer` → byte-identical to
the pre-LVN behavior.

## Status today (and what's pending)

**Shipped on `1.1`:**
- LVN-I: pluggable `Renderer` Protocol + `HtmlRenderer` + `ViewRuntime`
  field + `?platform=` handshake (#1577 closed via #1583, #1584, #1585)
- LVN-II: widget vocabulary + `NativeRenderer` scaffold + template
  variant resolver + wiring (#1578 closed via #1586, #1587, #1588, #1589)
- LVN-V (this guide): initial draft

**Pending (deferred to focused follow-up sessions):**
- LVN-II Rust walker: the actual Rust-side widget VDOM differ that
  produces `Patch` streams from native templates. `NativeRenderer.render_with_diff`
  currently raises `NotImplementedError` with a clear pointer.
- LVN-III: full Swift WebSocket transport + msgpack decoder + patch
  applicator + widget renderers + event sender in
  [`djust-native-ios`](https://github.com/djust-org/djust-native-ios)
  (PR #1 scaffolded; PRs 2-7 awaiting Swift implementer)
- LVN-IV: same for [`djust-native-android`](https://github.com/djust-org/djust-native-android)
  (PR #1 scaffolded; PRs 2-7 awaiting Kotlin implementer)
- LVN-V v1.0 vocabulary lock: documented in `native-widget-vocabulary.md`
  but the SemVer commitment lights up at the v1.0 native-client releases

## Migration from `djust-mobile-toga` (sketch)

`djust-mobile-toga` consumers can adopt native incrementally:

1. Ship today on WebView (existing setup unchanged).
2. Pick one polish screen — author a `.swiftui.html` variant alongside
   the existing `.html`.
3. In the consumer app, host the native client (`DjustLiveView`) in a
   native SwiftUI / Compose view for that one screen; keep the WebView
   for the rest.
4. Iterate per screen as native widget coverage and per-screen polish
   needs justify it.

The reactive event handlers are unchanged in all three modes (web,
WebView, native) — same `@event_handler` Python code drives everything.

## References

- [ADR-019: LiveView Native](adr/019-liveview-native.md) — the design
- [native-widget-vocabulary.md](native-widget-vocabulary.md) — the v1 spec
- Tracking issues: [#1577 LVN-I](https://github.com/djust-org/djust/issues/1577),
  [#1578 LVN-II](https://github.com/djust-org/djust/issues/1578),
  [#1579 LVN-III](https://github.com/djust-org/djust/issues/1579),
  [#1580 LVN-IV](https://github.com/djust-org/djust/issues/1580),
  [#1581 LVN-V](https://github.com/djust-org/djust/issues/1581)
- Companion repos: [`djust-native-ios`](https://github.com/djust-org/djust-native-ios),
  [`djust-native-android`](https://github.com/djust-org/djust-native-android),
  [`djust-mobile-toga`](https://github.com/djust-org/djust-mobile-toga)
