"""Count the work one turn does, without reading a clock (#3996, #3997, #3999).

The per-turn cost issues are about work that is repeated: a container walked
again, a contract compiled again, a key sent to Rust again. Each of those is a
call that can be counted, so the tests built on this module assert counts
(calls, items walked, bytes handed to ``update_state``) and never durations.

``counting(monkeypatch)`` installs spies on the functions the issues name and
returns the running :class:`Counts`. The spies only observe: every one calls
the function it replaces with the same arguments and returns its result.
"""

from __future__ import annotations

import json
from collections import Counter
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

from asgiref.sync import sync_to_async

from djust._exposure_sessions import MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S

PATH = "/per-turn/"


def nodes(value: Any) -> int:
    """How many nodes a structural walk of *value* visits (containers, keys, leaves)."""
    if isinstance(value, dict):
        return 1 + sum(1 + nodes(v) for v in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return 1 + sum(nodes(v) for v in value)
    return 1


class Counts(Counter):  # type: ignore[type-arg]
    """Call and volume counters, plus the keys each ``update_state`` carried."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: List[List[str]] = []
        self.changed: List[List[str]] = []
        #: The view of the last event snapshot (what a socket holds, #3998).
        self.view: Any = None

    def reset(self) -> None:
        self.clear()
        self.sent = []
        self.changed = []

    def snapshot(self) -> Dict[str, Any]:
        out: Dict[str, Any] = dict(sorted(self.items()))
        out["sent_keys"] = [sorted(keys) for keys in self.sent]
        out["changed_keys"] = [sorted(keys) for keys in self.changed]
        return out


class _RustViewSpy:
    """Stands in for a ``RustLiveView``: counts what the bridge hands it."""

    def __init__(self, real: Any, counts: Counts) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_counts", counts)

    def update_state(self, updates: Any) -> Any:
        counts = self._counts
        counts["update_state.calls"] += 1
        counts["update_state.keys"] += len(updates)
        counts["update_state.bytes"] += len(json.dumps(updates, default=str))
        counts.sent.append(list(updates))
        return self._real.update_state(updates)

    def set_changed_keys(self, keys: Any) -> Any:
        self._counts.changed.append(list(keys))
        return self._real.set_changed_keys(keys)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._real, name, value)


def counting(monkeypatch: Any) -> Counts:
    """Install the spies and return the counters they feed."""
    import django.contrib.sessions.backends.db as session_db

    from djust import _exposure, _exposure_sessions, _exposure_snapshots, runtime, websocket
    from djust import change_detection, state_pass
    from djust.mixins import context as context_module
    from djust.mixins import rust_bridge

    counts = Counts()

    def spy(owner: Any, name: str, label: str, walked: bool = False) -> None:
        original = getattr(owner, name)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            counts[label + ".calls"] += 1
            if walked:
                counts[label + ".nodes"] += nodes(args[0])
            return original(*args, **kwargs)

        wrapper.__wrapped__ = original  # type: ignore[attr-defined]
        monkeypatch.setattr(owner, name, wrapper)

    # Whole-state snapshots (each walks every container on the view).
    spy(websocket, "_snapshot_assigns", "snapshot_assigns")
    counted_snapshot = websocket._snapshot_assigns

    def snapshot_of(view: Any, *args: Any, **kwargs: Any) -> Any:
        counts.view = view
        return counted_snapshot(view, *args, **kwargs)

    monkeypatch.setattr(websocket, "_snapshot_assigns", snapshot_of)
    # The structural fingerprint, wherever it is taken.
    spy(change_detection, "deep_fingerprint", "fingerprint.other", walked=True)
    spy(websocket, "deep_fingerprint", "fingerprint.snapshot", walked=True)
    spy(rust_bridge, "deep_fingerprint", "fingerprint.sync", walked=True)
    spy(rust_bridge, "fingerprint_and_convert", "fingerprint_and_convert", walked=True)
    spy(rust_bridge, "convert_for_render", "convert_for_render", walked=True)
    spy(state_pass, "_fused", "state_pass.fused_nodes")
    spy(rust_bridge, "normalize_django_value", "normalize_django_value")

    original_sub_ids = rust_bridge._collect_sub_ids

    def sub_ids(value: Any, collected: Any, visited: Any = None, depth: int = 0) -> None:
        if depth == 0:
            counts["collect_sub_ids.calls"] += 1
            before = len(collected)
            original_sub_ids(value, collected, visited, depth)
            counts["collect_sub_ids.ids"] += len(collected) - before
            return
        original_sub_ids(value, collected, visited, depth)

    monkeypatch.setattr(rust_bridge, "_collect_sub_ids", sub_ids)

    # The serializability check on request-scoped values.
    spy(context_module, "_keeps_out_of_state", "keeps_out_of_state")
    spy(context_module, "_is_json_serializable", "is_json_serializable")
    spy(rust_bridge, "_keeps_out_of_state", "keeps_out_of_state.sync")

    # The ``json.dumps`` probe ``_is_json_serializable`` falls back to.
    real_json = context_module.json

    class _CountingJson:
        def __getattr__(self, name: str) -> Any:
            return getattr(real_json, name)

        @staticmethod
        def dumps(*args: Any, **kwargs: Any) -> Any:
            counts["serializable.json_probes"] += 1
            return real_json.dumps(*args, **kwargs)

    monkeypatch.setattr(context_module, "json", _CountingJson())

    # Explicit exposure: the contract, the commit, the snapshot, the session.
    original_from_view_class = _exposure.ExposureContract.__dict__["from_view_class"].__func__

    def from_view_class(cls: Any, view_class: type, **kwargs: Any) -> Any:
        counts["contract.from_view_class.calls"] += 1
        return original_from_view_class(cls, view_class, **kwargs)

    monkeypatch.setattr(_exposure.ExposureContract, "from_view_class", classmethod(from_view_class))
    spy(_exposure.ExposureContract, "__post_init__", "contract.built")
    spy(_exposure.ExposureContract, "project_view", "contract.project_view")
    spy(_exposure_sessions, "save_server_state", "save_server_state")
    spy(_exposure_sessions, "request_binding", "request_binding")
    spy(_exposure_snapshots, "request_binding", "request_binding.snapshot")
    spy(_exposure_snapshots, "snapshot_codec", "snapshot_codec")
    spy(_exposure_snapshots.ClientSnapshot, "capture", "client_snapshot.capture")
    spy(_exposure_sessions.ServerStateSession, "save", "server_state.save")
    spy(session_db.SessionStore, "load", "session.load")
    spy(session_db.SessionStore, "save", "session.save")
    spy(runtime, "_spawn_save", "explicit_save.hops")

    original_sync_to_async = runtime.sync_to_async

    def counted_sync_to_async(*args: Any, **kwargs: Any) -> Any:
        counts["runtime.sync_to_async.hops"] += 1
        return original_sync_to_async(*args, **kwargs)

    monkeypatch.setattr(runtime, "sync_to_async", counted_sync_to_async)

    real_rust_view = rust_bridge.RustLiveView

    def rust_view(*args: Any, **kwargs: Any) -> Any:
        return _RustViewSpy(real_rust_view(*args, **kwargs), counts)

    monkeypatch.setattr(rust_bridge, "RustLiveView", rust_view)
    return counts


@contextmanager
def ws_settings(module: str, **extra: Any) -> Iterator[None]:
    """Allow *module*'s views, lift the event rate limit, drop tenants."""
    from django.conf import settings
    from django.test import override_settings

    from djust.config import config as djust_config

    config = {k: v for k, v in settings.DJUST_CONFIG.items() if not k.startswith("TENANT_")}
    liveview = dict(getattr(settings, "LIVEVIEW_CONFIG", {}))
    liveview["rate_limit"] = {"rate": 10**6, "burst": 10**6, "max_warnings": 10**6}
    try:
        with override_settings(
            LIVEVIEW_ALLOWED_MODULES=[module],
            DEBUG=False,
            DJUST_CONFIG=config,
            DJUST_TENANTS={},
            LIVEVIEW_CONFIG=liveview,
            DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S,
            **extra,
        ):
            djust_config.reset()
            yield
    finally:
        djust_config.reset()


def new_session_key() -> str:
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    session.create()
    return str(session.session_key)


async def mount_view(view_class: type, session_key: str, user: Any = None) -> Any:
    """Open a socket through the real consumer and mount *view_class*."""
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(session_key)
    comm.scope["user"] = user if user is not None else AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()  # connect ack
    await comm.send_json_to(
        {
            "type": "mount",
            "view": f"{view_class.__module__}.{view_class.__name__}",
            "url": PATH,
        }
    )
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    return comm, frame


REPLY_TYPES = ("patch", "html_update", "noop", "error", "embedded_update")

_REF = [1000]


async def event(comm: Any, name: str, params: Any = None) -> Dict[str, Any]:
    """Send one event and return the frame that answers it (matched by ``ref``)."""
    frames = await event_frames(comm, name, params)
    return frames[-1]


async def event_frames(comm: Any, name: str, params: Any = None) -> List[Dict[str, Any]]:
    """Send one event; every frame up to and including the one that answers it."""
    from ._ws_frames import has_frame, receive_until

    _REF[0] += 1
    ref = _REF[0]
    await comm.send_json_to({"type": "event", "event": name, "params": params or {}, "ref": ref})
    frames: List[Dict[str, Any]] = await receive_until(
        comm, has_frame(*REPLY_TYPES, ref=ref), what=f"the reply to {name!r}"
    )
    for frame in frames:
        frame.pop("ref", None)  # a per-call counter, not part of what is compared
    return frames


def fingerprint_bytes(view: Any) -> int:
    """Bytes of the fingerprint trees *view* holds between turns (#3998).

    The dirty baseline and the last render's fingerprints, each object counted
    once: a tree both hold is one tree. The state is walked first and not
    counted, so its strings and numbers (a fingerprint's leaves ARE those
    objects) are not charged to the trees.
    """
    from . import _view_memory

    seen: set = set()
    attrs = vars(view)
    for name in _view_memory.state_names(view):
        _view_memory.deep_bytes(attrs[name], seen, stop=(view,))
    return sum(
        _view_memory.deep_bytes(attrs.get(name), seen, stop=(view,))
        for name in ("_dirty_baseline",) + _view_memory.FINGERPRINT_ATTRS
    )


@contextmanager
def shortcuts(monkeypatch: Any, *off: str) -> Iterator[None]:
    """Run a block with the named per-turn shortcuts turned OFF."""
    from djust import _per_turn

    unknown = set(off) - _per_turn.ALL
    assert not unknown, unknown
    with monkeypatch.context() as patch:
        patch.setattr(_per_turn, "disabled", frozenset(off))
        yield


new_session_key_async = sync_to_async(new_session_key)
