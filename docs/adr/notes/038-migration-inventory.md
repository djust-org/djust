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

| Destination | Where it goes under legacy |
| --- | --- |
| `template_context` | `get_context_data()`: rendered HTML and the Rust render state |
| `render_cache` | The Rust view, with its render state, stored in the shared state backend |
| `session_state` | Session key `liveview_<path>`: the context copy saved after an HTTP GET or POST |
| `client_state` | `get_state()`: client and debug state, and API assign diffs |
| `snapshot` | `_capture_snapshot_state()`: the signed browser snapshot (`enable_state_snapshot`) and time-travel history |
| `private_session` | Session key `liveview_<path>__private`: `_get_private_state()` |

| Kind of name | Destinations |
| --- | --- |
| Public `self.x` assigned in any method (`instance`) | all five public destinations |
| Public class value, JSON-serializable type (`class_value`) | `template_context`, `render_cache`, `session_state`. Other types are conditional, because legacy drops values that are not JSON-serializable (#694). |
| `@property` and other non-callable descriptors (`property`) | `template_context`, `render_cache`, `session_state`. Evaluated at render, never by this command. |
| `state()` field (`state`) | `template_context`, `render_cache`, `session_state`. Also `private_session` under `storage_key` `_state_<name>` when `mount()` reads or assigns it; otherwise that is conditional. |
| `_x` assigned in `mount()` (`private`) | `private_session` |
| `_x` assigned only after `mount()` | `private_session` is conditional: legacy persists it only if it is added to `_user_private_keys` |

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
- the `private_session` storage keys appear in `_get_private_state()`.

## Suggestions

Each name gets a suggested explicit declaration. For example, an `instance`
name gets `count: int = state(persist="server")` and a reminder to supply it
from `get_context_data()`. Render-only kinds get "supply it from
`get_context_data()`". `_x` names are told that explicit mode never persists
underscore attributes. The suggestions are a starting point. Declare
`persist="server"` only for fields that must survive reconnect, and
`client=True` only for fields the browser must read.

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
