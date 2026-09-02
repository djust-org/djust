"""The state-backend round-trip contract for ADR-027 lazy handles (#2570).

With ``template_resolve_lazy`` ON, a plain object crosses into Rust as a
``Value::Encoded`` carrying a LIVE handle and an EMPTY eager ``attrs`` map
(movement-2 correction 2: the handle replaces the map, which is what stops
the #2516 recursion). The state backends' ``get`` returns a msgpack
round-trip clone (``state_backends/memory.py`` — a real production path) and
``Deserialize`` restores ``live: None`` — so a restored handle-bearing value
has NEITHER a handle NOR attrs, and a raw render of that clone answers ``''``
for ``{{ obj.cls_attr }}``.

The movement-3 plan (prerequisite B) decided NOT to re-architect: no
framework path renders a clone before a full sync. A clone is materialised in
exactly one framework place — ``_initialize_rust_view``'s cache-HIT branch,
behind ``if self._rust_view is None`` — on a Python view that has no
change-detection baseline yet (``_prev_context_refs`` is absent), so the
first ``_sync_state_to_rust`` pushes EVERY context key through
``update_state`` and re-converts the value with a fresh handle before the
first render. This file pins that contract in three layers:

1. **Framework contract** (load-bearing): a real ``WebsocketCommunicator``
   mount, then a second mount on the SAME session + URL (which is what makes
   ``_initialize_rust_view`` take the cache HIT and materialise a clone),
   asserting the class attribute renders under the flag ON and OFF, and
   asserting the clone WAS materialised (a spy on ``InMemoryStateBackend.get``)
   so the test cannot pass by never hitting the cache.
2. **API-level contract** (the degraded bytes, pinned by name): a
   ``RustLiveView`` clone rendered WITHOUT a sync answers ``''`` for a
   handle-only lookup. Changing those bytes is a deliberate act.
3. **Structural pins**: the clone reader has exactly the known call sites,
   ``_initialize_rust_view`` is the only consumer, the only writer of the
   sync-skip flag is the sync itself, and every render entry syncs between
   initialising and rendering.

Gate-off (#1468 / #2129, verified while authoring): marking the view in the
cache-HIT branch of ``_initialize_rust_view`` and making
``_sync_state_to_rust`` early-return for a marked view (= render the clone
without a sync) turns the framework contract test RED under both flags
(``[][]`` lazy-on, ``[][in-dict]`` lazy-off) while the API-level degraded
tests stay GREEN. Two weaker mutations redden ONLY their structural pin:
setting ``_sync_done_this_cycle`` in the HIT branch is a no-op for the mount
path, which syncs explicitly (``dispatch_mount``), and skipping
``_force_full_html`` in ``dispatch_mount`` touches the session-snapshot
restore twin, not the backend clone — the re-attachment mechanism for the
clone is the explicit mount sync on a view with no baseline.
"""

from __future__ import annotations

import contextlib
import inspect
import pathlib
import re

import djust
import pytest
from asgiref.sync import sync_to_async
from djust import LiveView, _rust
from djust._rust import RustLiveView

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixture class + the flag axis (mirrors test_adr027_characterization_net_2539)
# --------------------------------------------------------------------------- #
class Cls:
    """A plain object whose lookup of interest is a CLASS attribute (not in
    ``__dict__``, so the flag-OFF eager map never held it) plus an instance
    attribute (which the eager map does hold)."""

    cls_attr = "class-level"

    def __init__(self) -> None:
        self.inst_attr = "in-dict"

    def __repr__(self) -> str:
        return "<Cls>"


@contextlib.contextmanager
def resolve_lazy(enabled: bool):
    """Flip ``LIVEVIEW_CONFIG['template_resolve_lazy']`` through the real
    wiring and assert the Rust thread-local took it (#2017 — a setter with no
    getter cannot be tested end to end)."""
    from djust.config import config
    from djust.render_env import apply_render_env

    previous = config.get("template_resolve_lazy", False)
    config.update({"template_resolve_lazy": enabled})
    apply_render_env()
    assert _rust.resolve_lazy_enabled() is enabled, (
        "the ADR-027 flag did not reach Rust — apply_render_env() is not wiring it"
    )
    try:
        yield
    finally:
        config.update({"template_resolve_lazy": previous})
        apply_render_env()


FLAGS = [pytest.param(False, id="lazy-off"), pytest.param(True, id="lazy-on")]

TEMPLATE = "<div>[{{ obj.cls_attr }}][{{ obj.inst_attr }}]</div>"
SYNCED = "[class-level][in-dict]"


class _RoundTripView(LiveView):
    template = '<div dj-root dj-id="0">[{{ obj.cls_attr }}][{{ obj.inst_attr }}]</div>'

    def mount(self, request, **kwargs):
        self.obj = Cls()


# --------------------------------------------------------------------------- #
# 1. Framework contract — real WebsocketCommunicator, second mount = cache HIT
# --------------------------------------------------------------------------- #
async def _receive_until(communicator, wanted_type, *, tries=5, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _mount_once(session_key: str, url: str) -> dict:
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame
    await communicator.send_json_to(
        {"type": "mount", "view": f"{__name__}._RoundTripView", "url": url}
    )
    frame = await _receive_until(communicator, "mount")
    await communicator.disconnect()
    assert frame.get("type") == "mount", f"expected a mount frame, got {frame!r}"
    return frame


@pytest.mark.parametrize("lazy", FLAGS)
@pytest.mark.asyncio
async def test_second_mount_renders_the_class_attribute_from_a_backend_clone(
    lazy: bool, monkeypatch
) -> None:
    """The load-bearing pin: the first render after a state-backend round
    trip is preceded by a full sync, so the class attribute renders."""
    pytest.importorskip("channels")
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings
    from djust.state_backends import memory as memory_mod
    from djust.state_backends.registry import get_backend

    backend = get_backend()
    assert isinstance(backend, memory_mod.InMemoryStateBackend), (
        f"this pin exercises the in-memory msgpack round trip; got {backend!r}"
    )

    # Spy on the clone reader: the second mount MUST take the cache HIT, or
    # the test proves nothing about a restored clone.
    hits: list[str] = []
    real_get = memory_mod.InMemoryStateBackend.get

    def spying_get(self, key):
        result = real_get(self, key)
        if result is not None:
            hits.append(key)
        return result

    monkeypatch.setattr(memory_mod.InMemoryStateBackend, "get", spying_get)

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()
    url = "/round-trip-2570/"

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]), resolve_lazy(lazy):
        first = await _mount_once(session_key, url)
        assert SYNCED in (first.get("html") or ""), (
            f"fresh mount must render the live object; got {first.get('html')!r}"
        )
        assert hits == [], "the first mount must be a cache MISS (no clone yet)"

        second = await _mount_once(session_key, url)
        assert len(hits) == 1, (
            "the second mount on the same session + URL must take the cache HIT "
            f"and materialise a msgpack clone; hits={hits!r}"
        )
        assert SYNCED in (second.get("html") or ""), (
            "the first render after a state-backend round trip must be preceded "
            "by a full sync that re-attaches the lazy handle (#2570); got "
            f"{second.get('html')!r}"
        )


# --------------------------------------------------------------------------- #
# 2. API-level contract — the DEGRADED bytes, pinned by name
# --------------------------------------------------------------------------- #
def _api_view() -> RustLiveView:
    view = RustLiveView(TEMPLATE, [])
    view.update_state({"obj": Cls()})
    return view


def test_a_raw_clone_rendered_without_a_sync_answers_empty_under_the_lazy_flag() -> None:
    """API-LEVEL CONTRACT (#2570, deliberate): a ``RustLiveView`` clone that
    came back from ``serialize_msgpack`` / ``deserialize_msgpack`` and is
    rendered WITHOUT an ``update_state`` re-sync answers ``''`` for EVERY
    dotted lookup on a handle-bearing value — the class attribute AND the
    instance attribute — because the handle is transient and the eager
    ``attrs`` map is empty beside it.

    This is the bytes the movement-3 plan chose over an eager-snapshot
    (rejected: re-creates #2516) and over sidecar re-attachment (rejected:
    re-creates #2504/#2505). A future change that makes this render
    ``class-level`` or ``in-dict`` must be a deliberate act that rewrites
    this assertion by name.
    """
    with resolve_lazy(True):
        view = _api_view()
        assert view.render() == f"<div>{SYNCED}</div>"

        clone = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert clone.render() == "<div>[][]</div>", (
            "the DEGRADED contract: a handle-only value restored from msgpack has "
            "neither handle nor attrs; a render without a sync answers empty"
        )

        # The re-attachment IS the sync: one update_state re-converts the value.
        clone.update_state({"obj": Cls()})
        assert clone.render() == f"<div>{SYNCED}</div>"


def test_a_raw_clone_keeps_todays_eager_map_with_the_flag_off() -> None:
    """Flag OFF is byte-identical to today: the eager ``attrs`` map (the
    instance ``__dict__``) survives the round trip, and the class attribute
    was never in it — at the raw API there is no sidecar to fall back to."""
    with resolve_lazy(False):
        view = _api_view()
        assert view.render() == "<div>[][in-dict]</div>"
        clone = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert clone.render() == "<div>[][in-dict]</div>"


# --------------------------------------------------------------------------- #
# 3. Structural pins — one clone site, always synced before the first render
# --------------------------------------------------------------------------- #
_PKG = pathlib.Path(djust.__file__).resolve().parent


def _framework_sources():
    for path in _PKG.rglob("*.py"):
        if "tests" in path.relative_to(_PKG).parts:
            continue
        yield path.relative_to(_PKG).as_posix(), path.read_text()


def test_the_clone_reader_has_exactly_the_known_call_sites() -> None:
    """``deserialize_msgpack`` is called from the state backends only:
    ``memory.get`` (the round-trip clone), ``redis.get`` (the wire read) and
    ``redis.get_stats`` (the timestamp sample). A new caller must add itself
    here AND prove its consumer syncs before rendering (#1125)."""
    found = {
        rel: len(re.findall(r"RustLiveView\.deserialize_msgpack\(", src))
        for rel, src in _framework_sources()
        if "RustLiveView.deserialize_msgpack(" in src
    }
    assert found == {
        "state_backends/memory.py": 1,
        "state_backends/redis.py": 2,
    }, f"clone-reader call sites drifted: {found!r}"


def test_initialize_rust_view_is_the_only_consumer_of_a_backend_clone() -> None:
    """The only framework reads of a backend entry are the two
    ``backend.get(self._cache_key)`` lookups in ``_initialize_rust_view``,
    and both sit behind ``if self._rust_view is None`` — a clone is only ever
    attached to a view that has no Rust view (and so no sync baseline) yet."""
    from djust.mixins import rust_bridge

    consumers = {
        rel: len(re.findall(r"backend\.get\(self\._cache_key\)", src))
        for rel, src in _framework_sources()
        if "backend.get(self._cache_key)" in src
    }
    assert consumers == {"mixins/rust_bridge.py": 2}, f"drifted: {consumers!r}"

    src = inspect.getsource(rust_bridge.RustBridgeMixin._initialize_rust_view)
    guard = src.index("if self._rust_view is None:")
    assert guard < src.index("backend.get(self._cache_key)"), (
        "the cache lookup must be behind the `_rust_view is None` guard"
    )
    assert "self._rust_view = cached_view" in src


def test_the_sync_skip_flag_is_only_set_by_the_sync_itself() -> None:
    """``render_with_diff`` skips the sync when ``_sync_done_this_cycle`` is
    set. The only writer of ``True`` is ``_sync_state_to_rust`` (after a sync
    completed), so the skip can never fire before a clone's first sync."""
    writer = re.compile(r"self\._sync_done_this_cycle\s*=\s*True")
    writers = {
        rel: len(writer.findall(src)) for rel, src in _framework_sources() if writer.search(src)
    }
    assert writers == {"mixins/rust_bridge.py": 1}, f"drifted: {writers!r}"

    from djust.mixins import rust_bridge

    sync_src = inspect.getsource(rust_bridge.RustBridgeMixin._sync_state_to_rust)
    assert "self._sync_done_this_cycle = True" in sync_src


@pytest.mark.parametrize(
    "entry, render_call",
    [
        ("render", "self._rust_view.render()"),
        ("_render_full_template_inner", "self._rust_view.render()"),
        ("render_with_diff", "renderer.render_with_diff("),
    ],
)
def test_every_render_entry_syncs_between_init_and_render(entry: str, render_call: str) -> None:
    """Each render entry initialises (which may attach a clone), then syncs,
    then renders — in that order."""
    from djust.mixins import template as template_mod

    src = inspect.getsource(getattr(template_mod.TemplateMixin, entry))
    init = src.index("self._initialize_rust_view(")
    sync = src.index("self._sync_state_to_rust(")
    render = src.index(render_call)
    assert init < sync < render, f"{entry}: init={init} sync={sync} render={render}"


def test_the_mount_path_syncs_explicitly_between_init_and_its_first_render() -> None:
    """``dispatch_mount`` (the converged WS + SSE + runtime mount seam) does
    not rely on ``render_with_diff``'s own sync: it calls
    ``_initialize_rust_view`` (where the clone is attached), then
    ``_sync_state_to_rust``, then ``render_with_diff`` — so the first render
    of a cache-HIT clone is always a synced one. (The sync marks
    ``_sync_done_this_cycle`` so ``render_with_diff`` does not sync twice —
    one mechanism handing off, not two shadowing each other.)"""
    from djust import runtime

    src = inspect.getsource(runtime.ViewRuntime.dispatch_mount)
    init = src.index("view_instance._initialize_rust_view)(request)")
    sync = src.index("view_instance._sync_state_to_rust)()")
    render = src.index("view_instance.render_with_diff")
    assert init < sync < render, f"init={init} sync={sync} render={render}"


def test_the_snapshot_restore_twin_forces_a_full_html_render() -> None:
    """The OTHER restore mechanism — session-snapshot / signed-snapshot
    restore in ``dispatch_mount`` — forces the first post-restore render to
    full HTML (#1977), and ``_force_full_html`` empties the change-detection
    baseline so that render is a full sync too (#783). Pinned here so the two
    restore twins (#1646) are named side by side."""
    from djust import runtime
    from djust.mixins import rust_bridge

    mount_src = inspect.getsource(runtime.ViewRuntime.dispatch_mount)
    assert re.search(
        r"if mounted_from_restore:\s*\n\s*view_instance\._force_full_html = True", mount_src
    ), "dispatch_mount must set _force_full_html on a restore mount"

    sync_src = inspect.getsource(rust_bridge.RustBridgeMixin._sync_state_to_rust)
    assert re.search(
        r'if getattr\(self, "_force_full_html", False\):\s*\n\s*prev_refs = \{\}', sync_src
    ), "_force_full_html must empty prev_refs (a full sync)"
