---
table: python
removed: `StreamingMixin.stream_insert(self, ...)` and `StreamingMixin.stream_delete(self, ...)` (the async pair)
use: `await self.astream_insert(...)` and `await self.astream_delete(...)`. `self.stream_insert` and `self.stream_delete` are the synchronous collection streams.
---
