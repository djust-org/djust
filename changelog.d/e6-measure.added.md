- **`manage.py djust_exposure_inventory`: a values-redacted migration inventory
  for ADR-038.** For each LiveView it lists the names legacy exposure infers
  (class attributes, `state()` fields, and `self.<name>` assignments found by
  parsing method source), the destinations each reaches under legacy (template
  context, render cache, `liveview_<path>` session, `get_state`, snapshots,
  private session), and a suggested explicit declaration. It prints text or
  `--json`. It never instantiates views, never evaluates properties or state
  factories, and never prints values. See `docs/adr/notes/038-migration-inventory.md`.
