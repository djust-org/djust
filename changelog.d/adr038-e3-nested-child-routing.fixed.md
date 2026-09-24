- **Events can target nested explicit-exposure children (ADR-038 E3-4).** A
  `view_id` event under an explicit root now resolves a grandchild through the
  server-owned registry when exactly one owned explicit descendant carries that
  id; direct children resolve as before, and an ambiguous or unknown id is not
  routed. Legacy roots are unchanged.
