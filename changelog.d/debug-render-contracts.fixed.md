- **Debug render ownership** — Serialize time-travel restoration and forward
  replay with normal renders, capture public parameter contracts with their DOM
  updates, and retain the render lock until cancelled workers finish. Reject
  replaced owners and label debug updates/errors so they cannot acknowledge
  unrelated foreground requests.
