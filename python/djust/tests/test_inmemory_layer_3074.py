"""``djust.layers.InMemoryChannelLayer``: Channels' layer with a rate-limited expiry sweep (#3074).

The stock layer sweeps every channel and group on every ``receive`` and
``group_send``, which makes a broadcast round O(sessions²). The djust layer
sweeps at most once per ``clean_interval`` and otherwise behaves the same.
"""

from __future__ import annotations

import asyncio

import pytest
from channels.layers import InMemoryChannelLayer as ChannelsLayer

from djust import layers
from djust.layers import InMemoryChannelLayer


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(layers.time, "monotonic", c)
    return c


def _count_sweeps(monkeypatch):
    calls = {"n": 0}
    real = ChannelsLayer._clean_expired

    def counting(self):
        calls["n"] += 1
        return real(self)

    monkeypatch.setattr(ChannelsLayer, "_clean_expired", counting)
    return calls


def test_it_is_a_channels_in_memory_layer():
    layer = InMemoryChannelLayer()
    assert isinstance(layer, ChannelsLayer)
    assert layer.clean_interval == 1.0
    assert layer.expiry == 60 and layer.group_expiry == 86400


def test_negative_clean_interval_is_rejected():
    with pytest.raises(ValueError, match="clean_interval"):
        InMemoryChannelLayer(clean_interval=-1)


def test_delivery_and_groups_are_unchanged():
    async def scenario():
        layer = InMemoryChannelLayer()
        a = await layer.new_channel()
        b = await layer.new_channel()
        await layer.group_add("room", a)
        await layer.group_add("room", b)
        await layer.group_send("room", {"type": "hello", "n": 1})
        got = [await layer.receive(a), await layer.receive(b)]
        await layer.group_discard("room", b)
        await layer.group_send("room", {"type": "hello", "n": 2})
        got.append(await layer.receive(a))
        await layer.send(b, {"type": "direct"})
        got.append(await layer.receive(b))
        return got

    assert asyncio.run(scenario()) == [
        {"type": "hello", "n": 1},
        {"type": "hello", "n": 1},
        {"type": "hello", "n": 2},
        {"type": "direct"},
    ]


def test_the_sweep_runs_at_most_once_per_interval(monkeypatch, clock):
    sweeps = _count_sweeps(monkeypatch)

    async def scenario():
        layer = InMemoryChannelLayer(clean_interval=1.0)
        channels = [await layer.new_channel() for _ in range(50)]
        for ch in channels:
            await layer.group_add("room", ch)
        for _ in range(20):  # 20 broadcast rounds within one interval
            await layer.group_send("room", {"type": "tick"})
            for ch in channels:
                await layer.receive(ch)
        first = sweeps["n"]
        clock.now += 1.0  # the interval has passed
        await layer.group_send("room", {"type": "tick"})
        return first, sweeps["n"]

    first, after = asyncio.run(scenario())
    # Stock: one sweep per group_send and per receive = 20 * (1 + 50) = 1020.
    assert first == 1
    assert after == 2


def test_clean_interval_zero_sweeps_on_every_message(monkeypatch, clock):
    sweeps = _count_sweeps(monkeypatch)

    async def scenario():
        layer = InMemoryChannelLayer(clean_interval=0)
        ch = await layer.new_channel()
        await layer.group_add("room", ch)
        for _ in range(5):
            await layer.group_send("room", {"type": "tick"})
            await layer.receive(ch)

    asyncio.run(scenario())
    assert sweeps["n"] == 10  # exactly Channels' behaviour


def test_expired_messages_and_memberships_are_still_removed(monkeypatch, clock):
    """Expiry still happens, at the next sweep after clean_interval."""
    wall = {"t": 5000.0}
    monkeypatch.setattr("channels.layers.time.time", lambda: wall["t"])

    async def scenario():
        layer = InMemoryChannelLayer(expiry=10, group_expiry=100, clean_interval=1.0)
        dead = await layer.new_channel()
        live = await layer.new_channel()
        await layer.group_add("room", dead)
        await layer.group_add("room", live)
        await layer.group_send("room", {"type": "a"})  # sweep 1 (nothing expired)
        await layer.receive(live)  # `dead` never reads its message
        wall["t"] += 11  # dead's message is now expired
        await layer.group_send("room", {"type": "b"})  # inside the interval: no sweep
        still_there = dead in layer.groups["room"]
        clock.now += 1.0
        await layer.group_send("room", {"type": "c"})  # sweep 2 evicts `dead`
        return still_there, set(layer.groups["room"]), dead, live

    still_there, members, dead, live = asyncio.run(scenario())
    assert still_there  # not evicted inside the interval
    assert members == {live}  # evicted by the next sweep


def test_flush_resets_the_sweep_clock(monkeypatch, clock):
    sweeps = _count_sweeps(monkeypatch)

    async def scenario():
        layer = InMemoryChannelLayer(clean_interval=60)
        ch = await layer.new_channel()
        await layer.send(ch, {"type": "x"})
        await layer.receive(ch)  # sweep 1
        await layer.flush()
        await layer.send(ch, {"type": "y"})
        await layer.receive(ch)  # sweep 2: flush started a fresh layer

    asyncio.run(scenario())
    assert sweeps["n"] == 2


def test_channel_layers_config_selects_it(settings):
    from channels.layers import get_channel_layer

    settings.CHANNEL_LAYERS = {
        "t3074": {
            "BACKEND": "djust.layers.InMemoryChannelLayer",
            "CONFIG": {"clean_interval": 0.5},
        }
    }
    from channels.layers import channel_layers

    try:
        layer = get_channel_layer("t3074")
        assert isinstance(layer, InMemoryChannelLayer)
        assert layer.clean_interval == 0.5
    finally:
        channel_layers.backends.pop("t3074", None)
