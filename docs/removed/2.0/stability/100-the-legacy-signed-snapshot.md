### The legacy signed snapshot → `state(..., persist="client", client=True)`

A view that set `enable_state_snapshot = True` had every public attribute
signed (`djust.security.sign_snapshot`) and sent to the browser on the mount
frame and after each event. On back-navigation the browser echoed the token,
and `LiveView._restore_snapshot()` applied it in place of `mount()`. djust
`2.0.0` removed that snapshot: `djust.security.state_snapshot`
(`sign_snapshot`, `unsign_snapshot`, `SNAPSHOT_SALT`),
`LiveView._restore_snapshot()`, `NonPersistableStateError` and the `strict=`
argument of `LiveView._capture_snapshot_state()`.

Importing `sign_snapshot`, `unsign_snapshot` or `SNAPSHOT_SALT` from
`djust.security` raises `ImportError` naming the replacement; so does reading
one as an attribute, so `hasattr()` raises rather than returning `False` (an
`AttributeError` from a module `__getattr__` would lose the message on
`from ... import`). A view class that defines `_restore_snapshot()` is refused
with `ImproperlyConfigured`, when the class is created and on the mount path. A legacy view no
longer sends a token, and a token a 1.x server signed is refused.
The session copy `enable_state_snapshot` also restored from is removed with
the attribute (next section), so a legacy view mounts fresh on Back.
The HTTP page-POST fallback still restores a legacy view's public and private state from the session between requests; that goes later in 2.0 (rows 5 and 6 of the exposure-flip plan).
Declare what the browser may hold:

```python
# Before (1.3)
class WizardView(LiveView):
    enable_state_snapshot = True

    def mount(self, request, **kwargs):
        self.step = 1

# After
class WizardView(LiveView):
    step = state(1, persist="client", client=True)

    def get_context_data(self, **kwargs):
        return {"step": self.step}
```

The token is still signed, bound and aged by the server; only its contents
changed. `get_max_age` and `DEFAULT_MAX_AGE` stay importable from
`djust.security`. `DJUST_STATE_SNAPSHOT_MAX_AGE` is now read in one place: a
whole number of seconds from 1 to 86400 is used as given, a larger value is
capped at 86400, and anything else falls back to 3600 with a warning.
`LiveView._should_restore_snapshot()` remains the hook that can refuse a
token.
