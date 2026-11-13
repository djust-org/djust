### Legacy view state between requests → `state(..., persist="server")`

Until 2.0 a legacy-exposure view kept three things between requests that it no
longer keeps:

- its `_private` attributes, saved in the session (`liveview_<path>__private`)
  by the page GET and each page-POST fallback request and put back on the next
  one;
- its public context and its components' state (`liveview_<path>`,
  `liveview_<path>_components`, and a `__mount_work` loading marker), which
  the next page-POST fallback request restored instead of running `mount()`;
- its compiled renderer and diff baseline, stored in the state backend under
  the session and page and reused by the next request or reconnect.

djust `2.0.0` removed all three, `LIVEVIEW_CONFIG['view_state_in_session']`
(refused with `ImproperlyConfigured` at startup, in `LIVEVIEW_CONFIG` or
`DJUST_CONFIG`) and system check `djust.C027`. A legacy view still mounts,
renders and handles events within one connection; each page-POST fallback
request mounts it again, and `handle_restore()` is not called for it. An event
for an interactive component the previous answer rendered is refused with 400,
as on an explicit view. Declare what must survive on an explicit view:

<!-- doc-snippet-check: anti-pattern -->
```python
# Before (1.3): the fallback kept self.offset in the session
class FeedView(LiveView):
    exposure_policy = "legacy"

    def mount(self, request, **kwargs):
        self.offset = 0
```

```python
# After
class FeedView(LiveView):
    offset = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return {"offset": self.offset}
```
