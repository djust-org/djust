"""Audio authoring, scope, bounds and template safety."""

import json

import pytest
from django.template import Context, Template
from django.test import override_settings

from djust.audio import AudioMixin, Sound, SoundBank
from djust.mixins.push_events import PushEventMixin


class ContextBase:
    def __init__(self, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"audio_banks": self.audio_banks}


class View(AudioMixin, PushEventMixin, ContextBase):
    audio_banks = {"test": SoundBank({"eat": Sound("sound/eat.wav", volume=0.4)})}


def mounted():
    view = View()
    view._websocket_session_id = "test-session"
    view._audio_ready = True
    return view


@pytest.mark.parametrize("value", [-1, 2, float("nan"), float("inf"), True, "0.5"])
def test_volume_rejects_invalid(value):
    with pytest.raises(ValueError):
        Sound("eat.wav", volume=value)


@pytest.mark.parametrize("path", ["", "../secret", "/outside", "https://example.com/x", "a\\b"])
def test_sound_requires_static_path(path):
    with pytest.raises(ValueError):
        Sound(path)


def test_bank_is_immutable_and_bounded():
    values = {"eat": Sound("eat.wav")}
    bank = SoundBank(values)
    values.clear()
    assert "eat" in bank.sounds
    with pytest.raises(TypeError):
        bank.sounds["oops"] = Sound("oops.wav")
    for count in (0, 9, True):
        with pytest.raises(ValueError):
            SoundBank({"eat": Sound("eat.wav")}, max_voices=count)


def test_audio_cues_are_scoped_ordered_and_drained():
    a, b = mounted(), mounted()
    a.play_sounds("test", [{"sound": "eat", "id": "1"}, {"sound": "eat", "id": "2"}])
    events = a._drain_push_events()
    assert events[0][0] == "djust:audio"
    payload = events[0][1]
    assert [e["id"] for e in payload["events"]] == ["1", "2"]
    assert payload["scope"] == a._audio_scope != b._audio_scope
    assert a._drain_push_events() == b._drain_push_events() == []


def test_prerender_does_not_queue_and_bad_inputs_fail():
    view = View()
    view.play_sound("test", "eat")
    assert not view._drain_push_events()
    for bank, sound in [("missing", "eat"), ("test", "missing")]:
        with pytest.raises(ValueError):
            view.play_sound(bank, sound)
    with pytest.raises(ValueError):
        view.play_sounds("test", [{"sound": "eat", "id": "x" * 129}])


def test_queue_has_a_per_turn_cap_and_stop_coalesces():
    view = mounted()
    for i in range(100):
        view.play_sound("test", "eat", event_id=str(i))
    view.stop_sounds("test")
    view.stop_sounds("test")
    events = view._drain_push_events()
    assert len(events) == 33
    assert events[-1][1]["op"] == "stop"
    with pytest.raises(ValueError):
        view.play_sounds("test", ({"sound": "eat", "id": str(i)} for i in range(33)))


@override_settings(STATIC_URL="/static/")
def test_manifest_and_controls_are_escaped_without_executable_inline_script():
    view = mounted()
    view.audio_banks = {"test": SoundBank({"eat": Sound('sound/"<bad>.wav')})}
    context = view.get_context_data()
    assert "audio_banks" not in context
    manifest = json.loads(context["djust_audio_manifest"])
    assert manifest["scope"] == view._audio_scope
    html = Template("{% load live_tags %}{% djust_audio %}").render(Context(context))
    assert "<script" not in html
    assert "<bad>" not in html
    assert 'data-audio-src="/static/djust/audio.js"' in html
    assert 'dj-update="ignore"' in html


def test_private_audio_epoch_and_cursors_never_restore():
    from djust import LiveView

    class RealView(AudioMixin, LiveView):
        pass

    view = RealView()
    view._audio_cursor = 42
    view._user_private_keys = {"_audio_scope", "_audio_cursor"}
    assert not view._get_private_state()
    original = view._audio_scope
    view._restore_private_state({"_audio_scope": "stale", "_audio_cursor": 1})
    assert view._audio_scope == original
    assert view._audio_cursor == 42


def test_connected_mount_cues_are_discarded_before_first_render():
    view = View()
    view._websocket_session_id = "connected"
    view.play_sound("test", "eat")
    assert view._drain_push_events() == []
    view.get_context_data()
    view.play_sound("test", "eat")
    assert len(view._drain_push_events()) == 1


@pytest.mark.asyncio
async def test_tick_with_audio_only_flushes_without_rendering():
    from djust import LiveView
    from djust.websocket import LiveViewConsumer

    class TickView(AudioMixin, LiveView):
        audio_banks = View.audio_banks

        def handle_tick(self):
            self.play_sound("test", "eat")

    consumer = LiveViewConsumer()
    view = TickView()
    view._audio_ready = True
    consumer.view_instance = view
    sent = []

    async def capture(payload):
        sent.append(payload)

    consumer.send_json = capture
    assert await consumer._tick_once() is False
    assert len(sent) == 1
    assert sent[0]["event"] == "djust:audio"


@pytest.mark.asyncio
async def test_ws_and_sse_transports_preserve_scope_and_batch():
    import asyncio
    from unittest.mock import AsyncMock
    from djust.runtime import ViewRuntime, SSESessionTransport, WSConsumerTransport
    from djust.sse import SSESession

    for kind in ("ws", "sse"):
        view = mounted()
        view.play_sounds("test", [{"sound": "eat", "id": "one"}, {"sound": "eat", "id": "two"}])
        if kind == "sse":
            session = SSESession("audio-test")
            transport = SSESessionTransport(session)
        else:
            consumer = AsyncMock()
            transport = WSConsumerTransport(consumer)
        runtime = ViewRuntime(transport)
        runtime.view_instance = view
        runtime._flush_push_events()
        await asyncio.sleep(0)
        frame = (
            session.queue.get_nowait() if kind == "sse" else consumer.send_json.call_args.args[0]
        )
        assert frame["payload"]["scope"] == view._audio_scope
        assert [item["id"] for item in frame["payload"]["events"]] == ["one", "two"]
        assert not view._drain_push_events()


def test_asset_check_reports_missing_static_files(monkeypatch):
    from djust.checks import audio as checks

    monkeypatch.setattr(checks, "_routed_liveview_classes", lambda: iter([View]))
    monkeypatch.setattr(checks.finders, "find", lambda path: None)
    assert [message.id for message in checks.check_audio(None)] == ["djust.audio.W001"]
    monkeypatch.setattr(checks.finders, "find", lambda path: "/resolved/eat.wav")
    assert checks.check_audio(None) == []


def test_manifest_resolves_through_static_storage(monkeypatch):
    import djust.audio as audio

    monkeypatch.setattr(audio, "static", lambda path: "/static/eat.abc123.wav")
    manifest = json.loads(mounted().get_context_data()["djust_audio_manifest"])
    assert manifest["banks"]["test"]["sounds"]["eat"]["url"] == "/static/eat.abc123.wav"
