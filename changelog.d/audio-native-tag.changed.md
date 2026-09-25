- **`{% djust_audio %}` renders natively in Rust, and `AudioMixin` builds its
  manifest once per view.** The bridged Python tag made every render of an
  `AudioMixin` view hand Python the whole render context to read one string,
  and the mixin re-resolved every sound through `static()` and re-serialised
  the banks on every render. The native node emits the same markup (a test
  pins it byte for byte to the Django-engine `simple_tag`, which is unchanged)
  and reports `djust_audio_manifest` as its only dependency instead of `*`.
  The manifest is rebuilt only when the banks or `DJUST_AUDIO_STATIC_ORIGINS`
  change. On Snake Arena (a 16-sound bank, ~5 frames a second per player) a
  frame renders in 1.27 ms instead of 1.53 ms, and live server CPU per
  delivered frame drops from 4.04 to 3.71 ms.
