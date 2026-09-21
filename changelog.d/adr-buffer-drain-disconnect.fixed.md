- **Disconnect during buffered updates:** Draining leaves later updates in
  their owned queue, allowing disconnect to discard them instead of delivering
  stale effects from a detached batch.
