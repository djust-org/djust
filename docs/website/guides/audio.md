# Declarative audio

Local feature implementation on the rc5 base; not yet a published djust release.
Short sound effects are application-owned static files. Python emits names;
the optional djust player owns browser activation and playback.

```python
from djust import LiveView
from djust.audio import AudioMixin, Sound, SoundBank
from djust.decorators import event_handler

class Inbox(AudioMixin, LiveView):
    audio_banks = {
        "notifications": SoundBank({
            "done": Sound("inbox/done.wav", volume=0.4),
        }),
    }

    @event_handler()
    def finish(self, **kwargs):
        # Complete the work and render visible confirmation too.
        self.play_sound("notifications", "done")
```

Inside the owning view's `dj-root`:

```django
{% load live_tags %}
{% djust_audio %}
```

The tag works in both Django and djust's native renderer. It emits escaped
configuration and accessible Enable sound, mute and volume controls, with no
inline executable script. No audio context or media fetch starts until the user
opts in. The framework loads `djust/audio.js` only on opted-in pages, including
live navigation. Refresh collected static files when updating the framework. The optional
`djust/audio.css` provides compact controls. Theme them with `--dj-audio-bg`,
`--dj-audio-text`, `--dj-audio-muted`, `--dj-audio-accent`,
`--dj-audio-border` and `--dj-audio-button` on a surrounding element.

`play_sounds(bank, [{"id": "job-123:1", "sound": "done"}, ...])` preserves an
ordered batch of up to 32 cues. These can overlap. Each bank supports up to eight
voices; incoming cues are dropped when full. `stop_sounds(bank)` stops a bank in
the current view without changing mute or volume preferences. Server handlers
cannot enable or unmute sound. Cues work through ordinary events and server
pushes; audio-only ticks flush even when no HTML changes.

For multiplayer rooms, keep a bounded shared event log and a separate
`_audio_cursor` for every view. Advance that cursor even while the user is muted.
Initialize it to the room's current sequence at mount. Do not drain a shared log
for the first listener. Snake Arena is a reference implementation.

## Delivery and lifecycle

Audio is optional, best-effort feedback. Events received while disabled, muted,
hidden or loading are consumed without playback. There is no replay backlog.
A view has a random delivery scope, independent of bank names. Duplicate IDs are
suppressed within a 512-entry window across its banks. The `_audio_` private
attribute namespace is reserved for ephemeral values and excluded from saved
state. A remount gets a fresh scope and requires opt-in again. Ordinary patches
preserve enable/mute/volume. Removing a view stops voices and aborts its fetches;
the last removal closes the shared context.

SSE uses the same event envelope; the existing transport removes a session when
its stream closes. This does not add durable SSE audio replay or a separate
streaming media transport. Speech/music streaming is a future extension.

## Assets and limits

Use relative staticfiles paths. Django's static storage resolves hashed URLs.
`manage.py check` reports missing assets for routed audio views. Each view may
have four banks with 32 sounds each. Downloads are limited to 1 MiB per asset
and 8 MiB per loading pass; decoded audio is checked against ten seconds per
clip and 16 MiB per view. Decoding is browser-owned; these checks do not promise
a strict peak-memory ceiling during decoding. Use short, modest WAV/MP3/OGG files
that your supported browsers can decode. Failures leave the application usable
and expose a Retry sound control without retrying gameplay cues.

Same-origin URLs are allowed by default. For a static CDN, explicitly set:

```python
DJUST_AUDIO_STATIC_ORIGINS = ["https://static.example.com"]
```

The CDN must support CORS. The site's CSP must allow its framework script under
`script-src` and its media downloads under `connect-src`. Redirected media,
credentials in URLs, and non-HTTP(S) URLs are refused. Media URLs are declared
configuration, never supplied in playback events. No microphone, recording,
external service, persistence of volume preferences or telemetry is used.

## Verification status

The implementation has Python tests for validation, template escaping, scoped
batches, ephemeral state, WS/SSE delivery and audio-only ticks, plus browser
runtime tests for activation failure, mute, hidden tabs, resource limits,
nested roots, duplicate events and teardown. Snake has integration tests for
fan-out, late joins and bounded room history. The local browser exercise verifies
real buffer starts and mute during gameplay; the `data-audio-played` diagnostic
on the controls counts successful buffer starts, not proof of audible output.

Release qualification still needs Firefox/WebKit coverage and human listening
review of the application sound pack. Passing automated checks does not establish
sound quality, audibility on the user's speakers, or synchronized playback.
