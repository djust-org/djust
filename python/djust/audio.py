"""Opt-in, session-scoped short audio cues (ADR-028).

Playback is ephemeral: never use a cue as an application acknowledgement.
"""

import json
import math
import re
from dataclasses import dataclass
from itertools import islice
from threading import Lock
from types import MappingProxyType
from typing import Mapping, TypedDict
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.templatetags.static import static

from ._exposure import ProviderContract
from ._exposure_providers import provide_context

__all__ = ["Sound", "SoundBank", "SoundEvent", "AudioMixin"]
_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
#: ADR-038 E2-1: the client audio manifest key, render-only.
AUDIO_PROVIDER = ProviderContract("djust.audio", rendered=frozenset({"djust_audio_manifest"}))
#: Serialise manifest builds (a cache miss) so one view builds its manifest
#: once under concurrency. Striped by view, so first renders of different
#: sessions (a reconnect burst on free-threaded Python) still build in
#: parallel. Never taken on the cache-hit path.
_MANIFEST_BUILD_LOCKS = tuple(Lock() for _ in range(64))


class SoundEvent(TypedDict):
    id: str
    sound: str


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError("Audio names must be 1–64 letters, digits, underscores or hyphens")
    return value


def _volume(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Audio volume must be a finite number between 0 and 1")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Audio volume must be a finite number between 0 and 1")
    return value


@dataclass(frozen=True)
class Sound:
    """An application-owned static asset, limited to ten seconds by the client."""

    path: str
    volume: float = 1.0

    def __post_init__(self):
        if (
            not isinstance(self.path, str)
            or not self.path
            or self.path.startswith(("/", "\\"))
            or ".." in self.path.split("/")
            or "\\" in self.path
            or urlsplit(self.path).scheme
        ):
            raise ValueError("Sound.path must be a relative staticfiles path")
        _volume(self.volume)


@dataclass(frozen=True)
class SoundBank:
    sounds: Mapping[str, Sound]
    max_voices: int = 8

    def __post_init__(self):
        if not isinstance(self.sounds, Mapping) or not 1 <= len(self.sounds) <= 32:
            raise ValueError("A sound bank must contain 1–32 sounds")
        if type(self.max_voices) is not int or not 1 <= self.max_voices <= 8:
            raise ValueError("max_voices must be an integer between 1 and 8")
        for name, sound in self.sounds.items():
            _name(name)
            if not isinstance(sound, Sound):
                raise TypeError("SoundBank values must be Sound instances")
        object.__setattr__(self, "sounds", MappingProxyType(dict(self.sounds)))


def _sound_bank(value, name=None):
    if name is not None:
        _name(name)
    if not isinstance(value, SoundBank):
        raise TypeError("audio_banks values must be SoundBank instances")
    return value


class AudioMixin:
    """Declare ``audio_banks`` and use ``{% djust_audio %}`` inside the view root.

    Put this mixin before LiveView. IDs are deduplicated within the client's
    bounded window. Calls made during HTTP prerender are discarded.
    """

    audio_banks = {}
    _djust_context_providers = (AUDIO_PROVIDER,)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._audio_scope = uuid4().hex

    def _audio_bank(self, bank):
        _name(bank)
        if bank not in self.audio_banks:
            raise ValueError(f"Unknown audio bank: {bank}")
        return _sound_bank(self.audio_banks[bank])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.pop("audio_banks", None)
        # First connected render happens after mount; mount-time cues stay silent.
        self._audio_ready = bool(getattr(self, "_websocket_session_id", None))
        if len(self.audio_banks) > 4:
            raise ValueError("At most four audio banks are supported per view")
        provide_context(
            self, context, AUDIO_PROVIDER.name, "djust_audio_manifest", self._audio_manifest()
        )
        return context

    def _audio_manifest(self):
        """The manifest JSON, built once per view and reused on every render.

        Building it resolves every sound through ``static()`` (a storage
        lookup each) and re-serialises the banks, on every render of the view:
        a game pushing frames paid that for sounds that never change. It is
        rebuilt only when an input changes: the scope, the banks (reassigned
        or swapped for another ``SoundBank``; banks themselves are immutable),
        or the allowed static origins.

        Thread safety (free-threaded 3.14t, ``worker_threads``, several event
        loops): a session's render lock already serialises its view's renders,
        but the cache does not rely on it. The hit path is one read of an
        attribute holding an immutable tuple. A miss builds under the view's
        stripe of ``_MANIFEST_BUILD_LOCKS`` and re-checks first, so concurrent first
        renders of one view build it once. The entry keeps the bank objects
        alive, so the ``id()`` in its key cannot be reused by a new bank.
        The lock is held only for a build (one ``static()`` per sound): two
        views on the same stripe that miss at the same moment wait for one
        build, and a render that hits never waits.
        """
        # One snapshot of the inputs: the key and the build must agree even if
        # ``audio_banks`` is reassigned meanwhile.
        items = tuple(self.audio_banks.items())
        origins = list(getattr(settings, "DJUST_AUDIO_STATIC_ORIGINS", []))
        key = (
            self._audio_scope,
            tuple((name, id(bank)) for name, bank in items),
            tuple(origins),
        )
        cached = getattr(self, "_audio_manifest_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        with _MANIFEST_BUILD_LOCKS[(id(self) >> 4) % len(_MANIFEST_BUILD_LOCKS)]:
            cached = getattr(self, "_audio_manifest_cache", None)
            if cached is not None and cached[0] == key:
                return cached[1]
            banks = {}
            for name, value in items:
                bank = _sound_bank(value, name)
                banks[name] = {
                    "maxVoices": bank.max_voices,
                    "sounds": {
                        sound_name: {"url": static(sound.path), "volume": sound.volume}
                        for sound_name, sound in bank.sounds.items()
                    },
                }
            manifest = json.dumps(
                {"version": 1, "scope": self._audio_scope, "banks": banks, "origins": origins}
            )
            # (key, manifest, banks): the third element pins the banks' ids.
            self._audio_manifest_cache = (key, manifest, tuple(bank for _, bank in items))
            return manifest

    def play_sound(self, bank, sound, *, event_id=None):
        self.play_sounds(
            bank, [{"sound": sound, "id": uuid4().hex if event_id is None else event_id}]
        )

    def play_sounds(self, bank, events):
        """Queue at most 32 cues per turn, without consuming a shared room log."""
        definition = self._audio_bank(bank)
        events = list(islice(iter(events), 33))
        if len(events) > 32:
            raise ValueError("An audio batch may contain at most 32 cues")
        checked = []
        for event in events:
            sound, event_id = event["sound"], event["id"]
            if sound not in definition.sounds:
                raise ValueError(f"Unknown sound in {bank}: {sound}")
            if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
                raise ValueError(
                    "Audio event IDs must be nonempty strings of at most 128 characters"
                )
            checked.append({"sound": sound, "id": event_id})
        if not checked or not getattr(self, "_audio_ready", False):
            return
        queued = sum(
            len(p.get("events", [])) for e, p in self._pending_push_events if e == "djust:audio"
        )
        # Drop overload rather than building an audible backlog.
        checked = checked[: max(0, 32 - queued)]
        if checked:
            self.push_event(
                "djust:audio",
                {
                    "version": 1,
                    "op": "play",
                    "scope": self._audio_scope,
                    "bank": bank,
                    "events": checked,
                },
            )

    def stop_sounds(self, bank):
        self._audio_bank(bank)
        if not getattr(self, "_websocket_session_id", None):
            return
        # Coalesce repeated stops in the same turn.
        payload = {"version": 1, "op": "stop", "scope": self._audio_scope, "bank": bank}
        if ("djust:audio", payload) not in self._pending_push_events:
            self.push_event("djust:audio", payload)

    def _get_private_state(self):
        # The _audio_ namespace is ephemeral, including application cursors.
        return {
            key: value
            for key, value in super()._get_private_state().items()
            if not key.startswith("_audio_") and key != "_pending_push_events"
        }

    def _restore_private_state(self, private_state):
        from ._exposure import require_legacy_state_api

        # Reject before filtering: the base guard runs after argument evaluation.
        require_legacy_state_api(self)
        super()._restore_private_state(
            {
                key: value
                for key, value in private_state.items()
                if not key.startswith("_audio_") and key != "_pending_push_events"
            }
        )
