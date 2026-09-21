- **Background parameter contracts** — Capture public handler contracts with
  tick, push, database-notification and async-result renders. Preserve matching
  recovery snapshots, reject stale owners, and withhold invalid snapshots without
  re-running application result callbacks. Cancelled render workers retain the
  render lock until they settle and discard their unsent diff baseline.
