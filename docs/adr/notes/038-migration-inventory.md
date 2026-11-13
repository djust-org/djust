# ADR-038 E6-2: migration inventory (`djust_exposure_inventory`)

ADR-038 *Compatibility and rollout* requires "a values-redacted inventory of
inferred names and their destinations before migration". This is that
inventory. Use it to choose declarations. Do not use it as an allowlist: the
ADR says not to "automatically allowlist everything the old implementation
found".

```bash
python manage.py djust_exposure_inventory                     # every user LiveView
python manage.py djust_exposure_inventory --app shop          # one app label
python manage.py djust_exposure_inventory --view shop.views.CartView --json
```

The command finds views the way the `check_liveviews` system check does: the
routed views from the root URLconf plus every imported `LiveView` subclass.
Framework classes are skipped. `--view` names classes directly.

## What it reads, and what it never does

- It never instantiates a view. It never evaluates a property, a
  `cached_property` or a `state()` default factory. It never reads an instance.
- It reads class attributes with `vars()` and reports only their type name.
- It parses method source with `ast` to find assignments, the same approach
  `python/djust/tests/test_log_exposure_pin.py` uses for log calls. It
  recognizes `self.x = …`, `self.x: T = …`, `self.x += …`, tuple unpacking and
  `setattr(self, "x", …)`.
- A type comes from an annotation or a literal on the right-hand side, such as
  `0`, `[]`, `f""` or `list()`. Anything else is reported as unknown (`?` in
  text, `null` in JSON).
- The output contains names, builtin type names, destinations and suggestions.
  It never contains values. The test suite puts sentinel secrets in class
  attributes, `mount()` assignments, a property and a state factory, and
  asserts that none of them appears in text or JSON output.

## Destinations (legacy policy)

2.0 removed the shared render cache (the Rust view kept in the state backend)
and the page's `liveview_<path>` and `liveview_<path>__private` session copies
(#4087). A legacy view's state reaches the session only when it renders as a
`lazy=True` child and an event reaches it over the page-POST fallback
(`_lazy_containers.py`). Whether a view is used that way is not visible from
its class, so the two session destinations are always conditional.

| Destination | Where it goes under legacy |
| --- | --- |
| `template_context` | `get_context_data()`: rendered HTML and the Rust render state |
| `client_state` | `get_state()`: client and debug state, and API assign diffs |
| `snapshot` | `_capture_snapshot_state()`: time-travel history (on 1.3.x also the signed browser snapshot of an `enable_state_snapshot` view) |
| `session_state` | Only as a `lazy=True` child on the page-POST fallback: session key `liveview_<path>__lazy__<id>`, the context copy |
| `private_session` | Only as a `lazy=True` child on the page-POST fallback: the same entry's private part, `_get_private_state()` |

| Kind of name | Destinations |
| --- | --- |
| Public `self.x` assigned in any method (`instance`) | `template_context`, `client_state`, `snapshot`; `session_state` is conditional |
| Public class value, JSON-serializable type (`class_value`) | `template_context`; `session_state` is conditional. Other types are conditional on `template_context`, because legacy drops values that are not JSON-serializable (#694). |
| `@property` and other non-callable descriptors (`property`) | `template_context`; `session_state` is conditional. Evaluated at render, never by this command. |
| `state()` field (`state`) | `template_context`. `session_state` and `private_session` are conditional: a public field's `_state_<name>` slot is saved in the private part only when its value holds a Django model (#2959, #1994). A `_`-named field, or one in `static_assigns`, has only `private_session`, under `storage_key` `_state_<name>`. |
| `_x` (`private`) | `private_session` is conditional |

The command leaves out framework-defined names: anything on `LiveView`'s MRO,
`_FRAMEWORK_INTERNAL_ATTRS`, `exposure_policy` and `request`. Legacy does
still put some framework class attributes, such as `template` and
`login_required`, into template context. They are configuration, not
application state, so the command does not list them.

`python/djust/tests/test_exposure_inventory_command.py`
(`test_inventory_matches_the_legacy_runtime`) mounts a legacy view and checks
the inventory against the real APIs:
- every `template_context` name appears in `get_context_data()`;
- the `client_state` names equal `get_state()`;
- the conditional `private_session` storage keys include what `_get_private_state()` returns.

## Suggestions

Each name gets a suggested explicit declaration that grants no more than the
view has today. An `instance` name gets `count: int = state(...)` and a
reminder to supply it from `get_context_data()`. Render-only kinds get "supply
it from `get_context_data()`". `_x` names are told that explicit mode never
persists underscore attributes.

A persistence grant is suggested only for a legacy view that restores its
state on a reconnect today: one that sets `enable_state_snapshot`, which
`PersistentLiveView` does for its subclasses. For those views an `instance`
name gets `count: int = state(persist="server")`. Every other legacy view
runs `mount()` again on a reconnect, so `persist="server"` would make its
attributes survive one and put them in the session, which it does not do
today. Each view's report line says which case applies (`reconnect: fresh
mount` or `reconnect: restored (enable_state_snapshot)`; `persists_today` in
JSON). The flag is read statically from the class, and the
`state_snapshot_enabled` setting is not consulted.

Nothing is inferred from what a template reads: the command never suggests a
client grant or persistence because a name is rendered. One thing the default
does not cover: a view served without a WebSocket restores its public state
from the session on the HTTP POST fallback, and under explicit exposure only
`persist="server"` fields are restored there. If a view relies on that, add
the grant by hand.

The suggestions are a starting point. Declare `persist="server"` only for
fields that must survive reconnect, and `client=True` only for fields the
browser must read.

## Known limits

- It is static. It does not see assignments through helpers such as
  `vars(self).update(...)`, a computed `setattr` name, or `self` aliased to
  another name. It also does not follow calls: a private attribute set by a
  helper that `mount()` calls is reported as conditional.
- If source is unavailable (built-in, generated or `exec`-defined classes),
  the class is listed in `source_unavailable`. Its class attributes are still
  reported, but its assignments are not.
- For views that are already `explicit`, the destinations are what legacy
  *would* do. Declared `state()` grants are shown under `declared`.
- Components registered at runtime, and state kept in components, are not
  enumerated per component.
