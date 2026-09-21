- **Replay argument contracts** — Validate strict replay arguments before state
  restoration and use the canonical positional/keyword call plan. Await async
  replay handlers through Django's sync bridge, preserve legacy raw arguments,
  and refuse handler invocation after a failed restoration.
