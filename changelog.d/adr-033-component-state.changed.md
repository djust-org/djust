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
