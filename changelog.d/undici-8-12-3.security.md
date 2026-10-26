- **The JS test toolchain's `undici` is now 8.12.3 (GHSA-4qv8-2p9j-2685,
  GHSA-f72v-6w45-q472, GHSA-xjj6-f33q-929c).** 8.11.2 was inside the affected
  `8.0.0 - 8.12.1` range of all three: unbounded memory in the EventSource
  parser (high), a Subresource Integrity bypass through non-lowercase algorithm
  tokens, and an unhandled error on excessive content-encodings. `undici`
  reaches the lockfile only through `jsdom`, whose `^8.11.2` range already
  allows the fix, so only `package-lock.json` changes. It is a development
  dependency only: nothing in the published package changes. It was the
  finding failing the 1.3.0rc12 release audit's `npm audit` gate.
