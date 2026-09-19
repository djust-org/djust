- **A plain component is state a handler writes to (ADR-033 S1).**
  `self.rating.value = 5` in an event handler now re-renders. A
  `Component`'s constructor kwargs are its `state`; public attribute writes
  go through to it; every change-detection snapshot (`_snapshot_assigns`,
  the dirty baseline, `@computed`, `_sync_state_to_rust`) compares the
  component by that state under the ONE #2900 rule instead of by `id()`, so
  the in-place write answers `patch` rather than `noop` (M3), and a component
  rebuilt with the same kwargs no longer counts as changed. The walk is
  budgeted like a container; a component narrows it with
  `fingerprint_fields = ("columns",)` (listed keys walked, the rest one-node
  leaves: scalars by value, containers by identity) or opts out with
  `_djust_fingerprint_state = False`. `DataTable`, `DataGrid`, `VirtualList`,
  `BarChart`, `LineChart`, `PieChart` and `Sparkline` declare theirs, so a
  click never pays a per-row walk. Pinned in
  `python/djust/tests/test_component_state_adr033.py`.
- **A plain component's state persists like descriptor state (ADR-033 S2).**
  The session save and the signed back-navigation snapshot carried a plain
  component as its rendered HTML (or dropped it), so `self.rating.value = 5`
  was lost on reconnect. Both now write
  `{"__djust_component__": "<module>.<class>", "state": {...}}` and
  `decode_state_roundtrip` rebuilds the component from it — resolved only
  among already-imported `Component` classes, through the class's own
  constructor, leaving the tag as a dict when it cannot. A reconnect after the
  write renders five stars
  (`python/djust/tests/test_component_state_persistence_adr033.py`).
- **Values are typed on the wire and an instance carries a `name` (ADR-033 S3).**
  `Component.event_attrs(event, trigger="click", **params)` renders the
  attributes an element emits an event with: `dj-<trigger>` plus one typed
  `dj-value-*` per keyword (`int` → `:int`, `bool` → `:bool`, `float` →
  `:float`, `str` untyped), so a handler receives `4`, not `"4"`, and the
  `int(value)` line disappears. `Component(name="row-7")` is state and rides
  along as `dj-value-name` on every trigger (for a class without a `name`
  parameter of its own — a form-field component keeps `name` as the field
  name and is not named on the wire), so one handler serves several
  instances: `def set_rating(self, value, name=None, **kwargs)`. Every shipped
  component that emits an event now goes through the helper (the untyped
  `data-value` form is no longer emitted; the client still reads it), pinned
  by `python/djust/tests/test_component_typed_events_sweep_adr033.py`.
- **Compatibility notes (ADR-033).** A handler annotated `value: str` still
  receives text for a typed wire value (`"4"`, `"true"`). `event_attrs` renders
  a UUID/date/Decimal as its `str()`, untyped. `state` is a reserved
  `Component` constructor kwarg (`TypeError`). `get_template_dirs()` clears its
  cache on `setting_changed`, so `override_settings(TEMPLATES=...)` no longer
  leaks a temporary directory into later `template_name` views in a test run.
