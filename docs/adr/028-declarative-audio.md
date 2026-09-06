# ADR-028: Declarative audio in the djust package

**Status**: Proposed
**Date**: 2026-09-05
**Deciders**: Project maintainers
**Related**:
- [ADR-007](007-package-taxonomy-and-consolidation.md) — runtime extensions belong in core
- [ADR-016](016-transport-runtime-interface.md) — shared transport runtime
- [ADR-025](025-js-extension-sockets.md) — framework client extension mechanisms

## Context

Applications need short audio cues for events such as a completed task, an
incoming message, a countdown, or a collision. A server-rendered application
should be able to declare those cues in Python without maintaining JavaScript
hooks, audio contexts, DOM observers, and reconnect logic itself.

Snake Arena provides a concrete use case. Its authoritative Python simulation
already emits named effects, including `eat`, `golden`, `crash`, and `win`.
Previously a custom WebAudio module observed the latest sound name and sequence
number in rendered attributes. Removing that module satisfied the application's
no-custom-JavaScript requirement but made the game silent. A single latest-event
field also overwrites earlier sounds when several events occur before a render.

Djust already has the required delivery substrate:

- `PushEventMixin.push_event()` queues events for delivery to a view session;
  `_drain_push_events()` preserves multiple queued events.
- WebSocket and SSE runtime paths flush those events. The client dispatches
  pushed events without requiring new application DOM nodes.
- Existing framework behavior and lifecycle mechanisms can own playback and
  controls. Audio should not create another game-state or rendering system.

Browsers restrict audible playback without user activation. A server round trip
cannot be assumed to preserve the activation needed to unlock audio. Playback
must be enabled in the browser's user-gesture handler, and failures must be
handled without claiming sound is enabled. See the
[MDN autoplay guide](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay)
and [AudioContext.resume()](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/resume).

## Decision

Propose **built-in, opt-in audio support in the `djust` distribution**, exposed
as `djust.audio`, with a maintained framework client module. Do not create a
separate `djust-audio` Python distribution.

Applications declare sound banks, emit named events in Python, and render
framework controls. They provide audio files but write no playback JavaScript.
Simulation, authorization, scoring, and application rendering remain on the
server; browser-owned state is limited to playback, mute/volume preferences,
resource readiness, and duplicate suppression.

This ADR proposes a contract, not an implemented API or a release commitment.

### 1. Python API and template integration

Proposed authoring surface:

```python
from djust import LiveView
from djust.audio import AudioMixin, Sound, SoundBank
from djust.decorators import event_handler


class ArenaView(AudioMixin, LiveView):
    audio_banks = {
        "arena": SoundBank(
            sounds={
                "eat": Sound("snake/audio/eat.wav", volume=0.35),
                "crash": Sound("snake/audio/crash.wav", volume=0.6),
                "win": Sound("snake/audio/win.wav", volume=0.6),
            },
            max_voices=8,
        ),
    }

    @event_handler()
    def demo_sound(self, **kwargs):
        self.play_sound("arena", "eat")
```

```html
{% load live_tags %}
<div dj-root dj-view="myapp.views.ArenaView">
  {% djust_audio %}
  <button dj-click="demo_sound">Try a sound</button>
</div>
```

The proposed `djust_audio` tag resolves the mounted view's bank configuration
and emits a safely serialized manifest plus accessible enable, mute, and volume
controls. It also requests the optional framework audio asset through djust's
asset pipeline, once per document, including when first encountered during
live navigation. It emits no executable inline application script. Controls
are associated with their owning view root; repeated roots or bank names must
not share routing accidentally.

`Sound` paths resolve through Django's staticfiles storage, including manifest
hashes and configured static origins. Bank declarations are immutable shared
configuration, not a place to store per-session playback state. Unknown banks
or sound names and invalid numeric options raise clear Python errors. Check
asset existence through Django system checks/collectstatic validation, not by
fetching assets during module import.

`play_sound(bank, sound, *, event_id=None)` queues a cue for **this view's
session**. It does not implicitly broadcast to every instance of the view
class. `play_sounds(bank, events)` preserves an ordered batch for applications
with multiple events per update. `stop_sounds(bank)` stops active cues in the
owning root; mute remains a local user action.

Default event IDs are generated per mounted view session. Callers may provide
stable logical IDs, such as `match-42:128`, when the same simulation event
is delivered through repeated room updates. ID reuse within an active root
means the same logical cue; reuse for different sounds is an application error.

### 2. Wire format and delivery semantics

Use the existing `push_event` envelope with a reserved `djust:audio` event:

```json
{
  "type": "push_event",
  "event": "djust:audio",
  "payload": {
    "version": 1,
    "op": "play",
    "bank": "arena",
    "events": [
      {"id": "match-42:128", "sound": "eat"},
      {"id": "match-42:129", "sound": "crash"}
    ]
  }
}
```

Retain the transport's existing view/root routing metadata outside this
payload. Verify routing for nested/multiple roots over both WS and SSE; a
page-global event listener alone is insufficient to identify the recipient.
Do not infer the target from a bank name or DOM selector supplied in a cue.

Audio is **best-effort ephemeral feedback**, not durable application state:

- Preserve events within a batch and their order. Starting two cues in order
  does not mean waiting for the first to finish; overlap is permitted.
- No playback from HTTP prerender, mount snapshots, or state restoration.
  Do not serialize queued cues into persisted LiveView state.
- Do not replay sounds missed while disconnected. Flush only current-session
  events; SSE replay/resume must also exclude stale audio events. A reconnect
  starts a new audio delivery epoch and rejects messages from the old one.
- A live root keeps a bounded LRU of 512 consumed IDs, scoped to that root and
  bank. Duplicate suppression is within this window, not exactly-once delivery.
  Clear delivery bookkeeping when the root is permanently destroyed.
- Consume and discard cues while disabled, muted, hidden, or loading; do not
  save a backlog to play when the user enables sound or returns to the tab.
- Cap batches at 32 cues and banks at 8 concurrent voices by default. Validate
  bounds on both ends. When voices are full, discard incoming cues; v1 does
  not introduce priorities or interrupt an existing sound to make room.
- Do not retry failed playback. Record a bounded debug diagnostic and continue
  rendering. Malformed or unsupported protocol messages must fail safely.

There is no promise of synchronized audio across clients, or of zero latency
under a slow connection. Event queues must remain bounded. A future freshness
protocol may add server-side expiry before delivery; v1 does not compare
uncoordinated client/server wall clocks or promise a maximum sound delay.

Audio must flush from ordinary handlers, ticks, and server-push callbacks even
when no DOM patch is produced. Audit those paths during implementation rather
than assuming the event-handler flush covers them. Exclude audio from any
future durable push-event replay feature unless it has explicit freshness
semantics.

### 3. User activation and playback lifecycle

Use Web Audio for short decoded sound buffers, with at most one `AudioContext`
per document and root-scoped banks/gain nodes. No third-party audio dependency
is required for v1.

The initial state is disabled. A real click or keyboard activation of the
framework's **Enable sound** button synchronously creates/resumes the context
before awaiting network or server work. Fetch/decode the declared assets after
opt-in. Report loading separately; enable playback only after the context is
running and the relevant buffer is ready. Rejected resume, unsupported APIs,
and decode/fetch failure leave truthful, accessible controls and a usable app.

- A server event cannot enable audio, unmute, or raise the user's volume.
- Provide immediate local mute and volume controls; mute stops active cues.
  Volume defaults to 0.5 and is clamped to 0–1, multiplied by each sound's gain.
- Preferences are tab-local and in memory in v1. Persisted browser permission
  or a previous visit does not bypass the framework's explicit opt-in.
- Hidden documents stop active cues and suppress incoming cues. If the browser
  suspends or interrupts the context, show **Resume sound** when another user
  gesture is needed; do not loop on rejected resume attempts.
- DOM patches and snapshot recovery must preserve runtime ownership of enable,
  mute, loading, and volume state. Replacing a control must not reset it or
  create duplicate event listeners. Follow the existing framework widget
  lifecycle conventions and test actual morph/remount behavior.
- Removing a root, changing rooms, or leaving via live navigation releases its
  voices, buffers, fetches, and listeners. Use refcounts for any shared buffers;
  bound cache memory and close the context after the last audio root leaves.
  Ordinary patches must not trigger this teardown.

Audio remains supplementary. Every meaningful cue needs a visual equivalent.
Controls have keyboard access, labels, and an announced enable/error state;
individual rapid sound cues must not flood an ARIA live region. Reduced-motion
preferences do not substitute for an independent mute control.

### 4. Packaging and security

Include `djust.audio` and an optional `static/djust/audio.js` in the standard
wheel. Only opted-in pages request the module or fetch audio assets. No extra
Python dependencies, separate pip package, npm setup, CDN scripts, or custom
application hooks are required. Sound files remain application-owned; do not
bundle a mandatory game sound pack.

Use the existing client lifecycle/event integration points described in
ADR-025. Do not add a parallel WebSocket listener or transport. Register the
framework handler through an explicit startup/readiness contract so asset load
order cannot lose registration or events; do not rely on polling globals.

Playback messages contain declared bank/sound names, never arbitrary URLs,
JavaScript, audio data, or selectors. Reject unknown names and invalid values
on the client as defense in depth. Escape manifests with the established
safe JSON/template mechanism; test strings containing HTML/script delimiters.

Resolve URLs from trusted static configuration. Default to the same origin;
allow configured static CDN origins explicitly with appropriate CORS. Reject
unexpected redirect destinations and non-HTTP(S) schemes. Limit fetched bytes
and decoded duration/memory per asset and total bank; cancel work on teardown.
Web Audio fetches require a compatible CSP `connect-src`, and the optional
module requires `script-src` allowance for its static origin. No `eval`, inline
executable scripts, microphone permission, audio capture, or third-party
telemetry is part of this feature.

### 5. Snake Arena integration

Replace the latest `sfx_name`/`sfx_seq` render attribute with a bounded list of
logical sound events produced during a simulation update. Broadcast that list
alongside the room version; each authorized recipient queues its own
`play_sounds()` call. Do not let the first recipient drain a shared room queue
and thereby consume the events intended for everyone else.

Initialize each joining/reconnecting session at the room's current event
cursor. New spectators should not hear historical countdowns or crashes.
Room authorization and simulation sequencing stay in the application; djust
provides scoped delivery and playback, not a multiplayer room service.

## Alternatives considered

1. **Restore per-application WebAudio hooks.** Possible with existing djust
   extensions, but repeats activation, mute, cleanup, and reconnect logic and
   violates the application's no-custom-JavaScript constraint.
2. **Separate `djust-audio` package.** Adds installation/version coordination
   for behavior tightly coupled to djust's runtime. ADR-007 supports keeping
   this in core; loading its client asset remains optional.
3. **Patch `<audio autoplay>` elements for each event.** Does not remove browser
   activation restrictions and couples repeated playback to DOM identity and
   morph behavior. It is not a reliable ephemeral-event delivery mechanism.
4. **Bundle a general audio library.** Potentially useful for streaming music,
   spatial audio, and complex mixing, but adds dependency and payload costs
   without eliminating the djust integration work. Reconsider if scope grows.
5. **Generic effects framework first.** Risks delaying a small useful feature
   behind speculative animation/haptics APIs. Reserve a clear audio protocol;
   evaluate common abstractions after actual use.

## Consequences

Applications gain declarative sound without moving business/game logic into
the browser. Djust assumes maintenance of browser activation, audio resources,
CSP documentation, and transport/lifecycle interactions. Opt-in loading avoids
adding audio work to pages that do not use it; measure the implementation's
compressed asset size and memory costs rather than claiming a budget now.

Audio may be dropped, blocked, or late. Application correctness must never
depend on hearing a cue or on a playback acknowledgment. These limitations
are preferable to bursts of stale sounds and unbounded queues.

V1 excludes music/loops, speech/TTS, recording, remote enable/unmute, spatial
mixing, synthesis, custom DSP, client-side game prediction, room scheduling,
and synchronized cross-device playback.

## Implementation and acceptance criteria

1. Implement typed bank definitions, validation, the mixin, and safely rendered
   manifests/controls. Add public exports and type stubs as appropriate.
2. Add the optional framework runtime with activation, bounded resources,
   root routing, and lifecycle cleanup. Audit WS/SSE flush and reconnect paths.
3. Integrate Snake Arena as a reference, using event batches and per-session
   cursors. Document generic notification and accessibility examples as well.
4. Before acceptance, verify:
   - Python serialization, bad names/values, multiple events per update, and
     no queued-audio persistence or mount replay.
   - WS/SSE parity; ticks and pushes without patches; multiple/nested roots;
     room isolation; duplicate delivery and reconnect without old cues.
   - Browser activation success and rejection; enabled/loading/muted states;
     user volume; duplicate controls; hidden tabs; navigation and DOM recovery.
   - Caps on batches, voices, buffers, and decode failures; teardown leaves
     no growing listener, context, or memory counts over repeated navigation.
   - Static manifest hashing/CDNs, strict CSP, hostile manifest strings,
     unauthorized names/URLs, and unsupported browsers failing silently.
   - No audio module request, context, or audio fetch on non-opted-in pages;
     no application-authored JavaScript in the reference integration.

Use unit tests for deterministic routing and event semantics, plus real browser
checks in Chromium, Firefox, and WebKit with default autoplay policies. Do not
pass the activation gate using autoplay-disabling browser flags. Assert actual
context/buffer playback behavior as well as visible controls; supplement with a
short listening check, since successful API calls alone do not prove audible
output quality. This ADR itself adds documentation only.
