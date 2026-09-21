- **Refuse staged nonlegacy actor mounts before lifecycle work.** The explicit
  actor-event refusal did not cover initial mounts. Runtime now rejects these
  mounts before lifecycle hooks and transport registration, while preserving
  sibling mounts on shared WebSocket batches. This is a safety gate while
  ADR-038 actor support remains unimplemented; the production exposure guard
  remains closed.
