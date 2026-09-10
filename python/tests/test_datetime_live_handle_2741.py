"""#2741: the datetime family DOES carry a live handle — pinned.

The ``walk_from_handle``-first routing comment in
``crates/djust_core/src/context.rs`` claimed the whole datetime family never
carries a live handle. False: ``django_json_encoded`` (``lib.rs``) attaches
``live: Some(..)`` for ``datetime`` / ``date`` / ``time`` / ``timedelta``
unconditionally, unlike ``opaque_value``, whose handle is flag-gated. #2743
reworded the comment; ``docs/architecture/VALUE_BOUNDARY.md`` row I11 recorded
the invariant as "no test", which is how the false claim survived (#1867).

Why Python and not a ``crates/djust_core/tests`` pyo3 test: ``django_json_encoded``
imports ``django.core.serializers.json`` to build its type table, and no Rust
test in this repo imports Django on the embedded interpreter (CI's
``rust-tests`` venv is not on the embedded ``sys.path``) — a first version of
this pin returned ``None`` there and went red. A pin that cannot run on CI is
decorative (#1859), so it lives here, against the public render path.

How the handle is isolated: ``resolution`` / ``max`` / ``min`` are in NEITHER
``ENCODED_ATTR_NAMES`` nor ``ENCODED_CALL_NAMES`` (their values are themselves
temporal, so collecting them would not terminate — see ``Encoded::attrs``), and
``{% with q=xs|first %}`` is the one binding shape the by-name sidecar cannot
reach through ``Context::aliases`` (an alias is registered only over an
UNFILTERED operand; VALUE_BOUNDARY.md §6.2). So through that binding, ONLY the
handle can answer ``q.resolution`` — and it does, byte-for-byte with Django,
under the shipped default. ``year`` (in the name table) is the control: it
renders under BOTH flag states, proving the flag-off blank on ``resolution`` is
the missing handle walk, not a broken render.

Refs #2741, #2743, #1867, #1859.
"""

from __future__ import annotations

import datetime as _dt

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from adr027_flag import resolve_lazy, shipped_default  # noqa: E402

from djust import LiveView, _rust  # noqa: E402

# The isolating binding, one live-only name and one name-table control each.
# `min` is deliberately covered too: `datetime.min.min is datetime.min` is the
# non-termination that keeps these out of the attrs table.
TEMPORAL = [
    pytest.param(_dt.datetime(2026, 3, 4, 5, 6, 7), "resolution", "year", id="datetime"),
    pytest.param(_dt.date(2026, 3, 4), "max", "year", id="date"),
    pytest.param(_dt.time(5, 6, 7), "max", "hour", id="time"),
    pytest.param(_dt.timedelta(days=1, seconds=2), "min", "days", id="timedelta"),
]


def _source(live_only: str, control: str) -> str:
    return (
        "{% with q=xs|first %}" + f"{{{{ q.{live_only} }}}}|{{{{ q.{control} }}}}" + "{% endwith %}"
    )


def _django(source: str, value) -> str:
    return DjangoTemplate(source).render(DjangoContext({"xs": [value]}))


def _djust(source: str, value) -> str:
    return _rust.render_template(source, {"xs": [value]})


def test_the_shipped_default_is_lazy_on():
    """The comment's claim is about the default; make sure the test's 'default'
    IS the shipped one rather than a literal (#1200)."""
    assert shipped_default() is True


@pytest.mark.parametrize("value, live_only, control", TEMPORAL)
def test_a_temporal_value_carries_a_live_handle_under_the_default(value, live_only, control):
    source = _source(live_only, control)
    expected = _django(source, value)
    live_expected, control_expected = expected.split("|")
    assert live_expected, f"Django must render {live_only} for this fixture to isolate anything"

    with resolve_lazy(shipped_default()):
        assert _djust(source, value) == expected, (
            f"{type(value).__name__}.{live_only} through the isolating binding is answerable "
            f"ONLY by the live handle `django_json_encoded` attaches (#2741); a blank here "
            f"means the datetime family no longer carries one"
        )


@pytest.mark.parametrize("value, live_only, control", TEMPORAL)
def test_the_handle_walk_is_what_answers_it(value, live_only, control):
    """Gate-off sibling, in-suite: with the flag OFF the handle is never walked,
    so the live-only name goes blank while the name-table control still
    renders. Proves the test above is reading the handle and not `attrs`."""
    source = _source(live_only, control)
    _, control_expected = _django(source, value).split("|")

    with resolve_lazy(False):
        assert _djust(source, value) == f"|{control_expected}"


# --------------------------------------------------------------------------- #
# #2767: the handle does NOT survive a state-backend round trip — pinned as the
# CURRENT behaviour, on both layers, so a change in either direction is
# deliberate (ADR-027 documented limit; VALUE_BOUNDARY.md §3.4 / row I11).
# --------------------------------------------------------------------------- #
RESTORE_HEAD = '<div dj-root dj-id="0">'


def _restore_source(live_only: str, control: str) -> str:
    return f"[{{{{ q.{live_only} }}}}][{{{{ q.{control} }}}}]"


@pytest.mark.parametrize("value, live_only, control", TEMPORAL)
def test_a_raw_clone_answers_empty_for_a_handle_only_name_after_a_round_trip(
    value, live_only, control
):
    """DOCUMENTED ADR-027 LIMIT (#2767), pinned as-is — NOT a bug fix.

    The state backends' ``get`` returns a msgpack clone
    (``python/djust/state_backends/memory.py`` ``serialize_msgpack`` /
    ``deserialize_msgpack``). The wire carries the ``attrs`` map but not the
    handle, and every ``visit_map`` arm in ``crates/djust_core/src/lib.rs``
    restores ``live: None``; nothing re-acquires one on restore. So on a clone
    rendered WITHOUT an ``update_state`` re-sync, a name only the handle can
    answer (``resolution`` / ``max`` / ``min``) renders EMPTY, while a name in
    ``ENCODED_ATTR_NAMES`` (the control) still renders from the persisted map
    — and ``get_state()`` still hands back a real temporal object
    (``temporal_object``), so the object a future re-attach would use exists.

    Direction (a) of #2767 (re-attach on restore) would turn the clone
    assertion red; a regression that drops the map would turn the control
    red. Both are meant to be noticed, not worked around.
    """
    from djust._rust import RustLiveView

    source = _restore_source(live_only, control)
    django_out = DjangoTemplate(source).render(DjangoContext({"q": value}))
    live_expected, control_expected = django_out.strip("[]").split("][")
    assert live_expected, f"Django must render {live_only} for this fixture to isolate anything"
    synced = f"{RESTORE_HEAD}[{live_expected}][{control_expected}]</div>"

    with resolve_lazy(shipped_default()):
        view = RustLiveView(RESTORE_HEAD + source + "</div>", [])
        view.update_state({"q": value})
        assert view.render() == synced, "fresh render answers both names (#2741's own pin)"

        clone = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert clone.render() == f"{RESTORE_HEAD}[][{control_expected}]</div>", (
            f"CURRENT behaviour after a state-backend round trip (#2767): "
            f"{type(value).__name__}.{live_only} is handle-only and the handle is "
            f"transient, so it renders empty; {control} survives in the attr map. "
            f"If this went red because the live-only name now renders, the ADR-027 "
            f"limit was lifted — update VALUE_BOUNDARY.md §3.4 / I11 and this pin."
        )
        restored = clone.get_state()["q"]
        assert isinstance(restored, type(value)) and restored == value, (
            "the restored STATE is still a real temporal object — the blank is a "
            "missing handle, not a lost value"
        )

        # The re-attachment IS the sync (#2570): one update_state re-converts it.
        clone.update_state({"q": value})
        assert clone.render() == synced


class _DatetimeRestoreView(LiveView):
    template = RESTORE_HEAD + _restore_source("resolution", "year") + "</div>"

    def mount(self, request, **kwargs):
        self.q = _dt.datetime(2026, 3, 4, 5, 6, 7)


@pytest.mark.django_db
async def test_the_framework_restore_path_re_attaches_the_handle_before_rendering(
    monkeypatch,
):
    """The CONTROL for the pin above, on the real path: a second WebSocket
    mount on the same session + URL takes the state-backend cache HIT
    (``InMemoryStateBackend.get`` → a msgpack clone, spied so the test cannot
    pass by never restoring) and the #2570 mount sync re-converts the value
    with a fresh handle before the first render. So on the shipped reconnect
    path ``{{ q.resolution }}`` does NOT go blank — the #2767 limit bites a
    clone rendered without a sync, and only there.
    """
    pytest.importorskip("channels")
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings

    from djust.state_backends import memory as memory_mod
    from djust.state_backends.registry import get_backend
    from djust.websocket import LiveViewConsumer

    assert isinstance(get_backend(), memory_mod.InMemoryStateBackend)

    hits: list[str] = []
    real_get = memory_mod.InMemoryStateBackend.get

    def spying_get(self, key):
        result = real_get(self, key)
        if result is not None:
            hits.append(key)
        return result

    monkeypatch.setattr(memory_mod.InMemoryStateBackend, "get", spying_get)

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    async def _mount_once(session_key: str, url: str) -> dict:
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        communicator.scope["session"] = _ScopeSession(session_key)
        connected, _ = await communicator.connect()
        assert connected
        await communicator.receive_json_from(timeout=2)  # drain connect frame
        await communicator.send_json_to(
            {"type": "mount", "view": f"{__name__}._DatetimeRestoreView", "url": url}
        )
        frame = None
        for _ in range(5):
            frame = await communicator.receive_json_from(timeout=3)
            if frame.get("type") == "mount":
                break
        await communicator.disconnect()
        assert frame and frame.get("type") == "mount", frame
        return frame

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()
    url = "/datetime-restore-2767/"
    expected = "[0:00:00.000001][2026]"

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]), resolve_lazy(shipped_default()):
        first = await _mount_once(session_key, url)
        assert expected in (first.get("html") or ""), first.get("html")
        assert hits == [], "the first mount must be a cache MISS (no clone yet)"

        second = await _mount_once(session_key, url)
        assert len(hits) == 1, f"the second mount must take the cache HIT; hits={hits!r}"
        assert expected in (second.get("html") or ""), (
            "the first render after a state-backend round trip is preceded by a full "
            "sync that re-attaches the handle (#2570), so the handle-only name renders "
            f"on the real path; got {second.get('html')!r}"
        )
