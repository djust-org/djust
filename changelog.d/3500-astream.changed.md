- **BREAKING: `StreamingMixin`'s async `stream_insert` / `stream_delete` are now
  `astream_insert` / `astream_delete` (#3500).** `StreamsMixin` comes first in
  `LiveView`'s bases and has sync methods with the same names, so on a
  `LiveView` the async pair was only reachable as
  `StreamingMixin.stream_insert(self, ...)`. Now `await self.astream_insert(...)`
  and `await self.astream_delete(...)` work directly, and `self.stream_insert` /
  `self.stream_delete` are unambiguously the sync collection-stream API.
  Replace `StreamingMixin.stream_insert(self, ...)` with
  `self.astream_insert(...)`, and the same for delete.
