- **Keyed collections of interactive dropdowns (ADR-034 C3).**
  `rows = DropdownMenu.collection()` declares one. `self.rows.sync([(key, DropdownMenu(...)), ...])`
  reconciles its members:
  - retained keys keep their state;
  - reordering never moves state between rows;
  - removed keys are refused afterwards, and a re-added key is a new lifetime;
  - duplicate keys change nothing.

  One `@rows.on.selected` callback receives the member that emitted, with
  `component.key` its collection key. `get(key)`, `len()`, iteration and
  `.values` give the current members.

  A view with a collection is mounted fresh on Back navigation, so a member
  removed since cannot come back.
