- **An `AudioMixin` view's `_audio_` attributes are never saved by automatic state (#4197).**
  The mixin documents its `_audio_` namespace (scope, cursors, the manifest cache)
  as ephemeral, but only the legacy private-state API honoured that: on an explicit
  view an application cursor such as `self._audio_cursor` set in `mount()` was saved
  with the automatic state and put back on the next request. A mixin can now declare
  `_djust_unsaved_prefixes`, a tuple of private-name prefixes automatic state
  neither saves nor restores; `AudioMixin` declares `("_audio_",)`.
