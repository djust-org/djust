# djust Native Widget Vocabulary (v1)

Frozen for LVN-II per [ADR-019](adr/019-liveview-native.md) §"Three layers" §3.

This is the tag vocabulary native templates author against and that the
`djust-native-ios` and `djust-native-android` clients translate into
SwiftUI and Jetpack Compose widgets respectively.

The vocabulary is the SwiftUI ∩ Compose intersection — 12 widgets
covering ~80% of typical app screens. Adding new widgets requires a
follow-up ADR and a coordinated release across all three repos
(djust core + both native clients).

## Mapping

| Tag                | SwiftUI            | Jetpack Compose                    | Category    |
|--------------------|--------------------|------------------------------------|-------------|
| `<Stack>`          | `VStack`           | `Column`                           | Layout      |
| `<HStack>`         | `HStack`           | `Row`                              | Layout      |
| `<ZStack>`         | `ZStack`           | `Box`                              | Layout      |
| `<Text>`           | `Text`             | `Text`                             | Leaf        |
| `<Button>`         | `Button`           | `Button`                           | Leaf        |
| `<TextField>`      | `TextField`        | `TextField`                        | Leaf        |
| `<Toggle>`         | `Toggle`           | `Switch`                           | Leaf        |
| `<List>`           | `List`             | `LazyColumn`                       | Leaf        |
| `<Image>`          | `Image`            | `Image`                            | Leaf        |
| `<ScrollView>`     | `ScrollView`       | `Modifier.verticalScroll`          | Layout      |
| `<Spacer>`         | `Spacer`           | `Spacer`                           | Layout      |
| `<NavigationView>` | `NavigationView`   | `NavHost`                          | Navigation  |

The Python source of truth is `python/djust/renderers/widgets.py`:
`WIDGET_TAGS: FrozenSet[str]`. The same set is mirrored in
`djust-native-ios/Sources/DjustNative/WidgetTags.swift` and
`djust-native-android/src/main/kotlin/org/djust/native/WidgetTags.kt`
(both pending LVN-III PR-1 / LVN-IV PR-1 — see #1579 / #1580).

## Event attributes

Mirror of the browser's `dj-click` family:

| Attribute    | Triggers on (SwiftUI)                  | Triggers on (Compose)         |
|--------------|----------------------------------------|-------------------------------|
| `dj-tap`     | `Button` action / `.onTapGesture {}`   | `onClick`                     |
| `dj-change`  | `Toggle.onChange` / `TextField` commit | `onCheckedChange` / `onValueChange` |
| `dj-input`   | `TextField.onChange` (per-keystroke)   | `onValueChange` (per-keystroke) |

The wire encoding matches the browser's `dj-click` payload byte-for-byte
— the server-side `@event_handler` decorator sees identical event shapes
across all three clients.

## Style attributes

Constrained subset honored in v1. Anything else is silently ignored by
the native clients (no error — graceful degradation, so unknown styling
hints never break a render):

- `padding` — uniform inset (string or int).
- `spacing` — child spacing within layout containers.
- `alignment` — `"leading"`, `"center"`, `"trailing"`, `"top"`, `"bottom"`.
- `foregroundColor` — hex string (`"#14457E"`) or named color (`"red"`).
- `font` — short name (`"title"`, `"body"`, `"caption"`); platform default mapping.

## SemVer commitment

- **v1.x**: this 12-widget vocabulary is frozen. New widgets land in
  v1.x via minor version bumps; both native client libraries ship
  matching minor bumps in the same release window.
- **v2.0**: a widget *removal* requires a major bump. Renames are
  effectively removals (old name removed, new name added) and need
  the same major bump.
- **Custom widgets**: third-party widgets are out of scope for v1. A
  future extension hook may live behind a sub-namespace
  (`<x:CustomWidget>`) — see follow-up ADR if/when demand surfaces.

## References

- [ADR-019: LiveView Native](adr/019-liveview-native.md)
- Tracking issue: [#1578 (LVN-II)](https://github.com/djust-org/djust/issues/1578)
- Python source: `python/djust/renderers/widgets.py`
- Pending: `djust-org/djust-native-ios` (#1579), `djust-org/djust-native-android` (#1580)
