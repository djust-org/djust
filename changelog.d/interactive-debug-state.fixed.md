- **Interactive debug state** — staged dropdowns now capture and restore their
  declared state through both time-travel scrubbers without emitting callbacks or
  changing binding IDs. Invalid component records and stale owners are rejected
  before component mutation.
