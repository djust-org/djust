"""``djust.layers.MultiLoopInMemoryChannelLayer``: one layer, several event loops (#3128).

With ``djust serve --loops N`` a process runs N asyncio loops, one per thread.
A consumer receives on the loop that accepted its connection, and sends reach
it from every loop. These tests run real loops in real threads; on the 3.14t
CI job they run with the GIL off.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from channels.exceptions import ChannelFull

from djust import layers
from djust.layers import InMemoryChannelLayer, MultiLoopInMemoryChannelLayer


class LoopThread:
    """An asyncio loop running forever in its own thread."""

    def __init__(self, name: str) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name=name, daemon=True)
        self.thread.start()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self, coro, timeout: float = 10):
        return self.submit(coro).result(timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


@pytest.fixture
def loops():
    started = [LoopThread(f"test-loop-{i}") for i in range(4)]
    yield started
    for lt in started:
        lt.stop()


def test_it_is_djusts_in_memory_layer_and_declares_itself_loop_safe():
    layer = MultiLoopInMemoryChannelLayer(capacity=7, expiry=5, clean_interval=0.5)
    assert isinstance(layer, InMemoryChannelLayer)
    assert layer.multi_loop_safe is True
    assert not getattr(InMemoryChannelLayer(), "multi_loop_safe", False)
    assert (layer.capacity, layer.expiry, layer.clean_interval) == (7, 5, 0.5)


def test_single_loop_behaviour_matches_djusts_layer():
    async def scenario(layer):
        a, b = await layer.new_channel(), await layer.new_channel()
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

    assert asyncio.run(scenario(MultiLoopInMemoryChannelLayer())) == asyncio.run(
        scenario(InMemoryChannelLayer())
    )


def test_a_send_from_another_loop_wakes_a_receiver_parked_on_its_own_loop(loops):
    layer = MultiLoopInMemoryChannelLayer()
    a, b = loops[0], loops[1]
    channel = a.run(layer.new_channel())
    pending = a.submit(layer.receive(channel))  # parks on loop a: nothing queued
    time.sleep(0.05)
    assert not pending.done()
    b.run(layer.send(channel, {"type": "x", "n": 1}))
    assert pending.result(5) == {"type": "x", "n": 1}


def test_a_message_sent_before_anyone_receives_waits_for_the_receiver(loops):
    layer = MultiLoopInMemoryChannelLayer()
    channel = loops[0].run(layer.new_channel())
    loops[1].run(layer.send(channel, {"type": "early"}))
    assert loops[2].run(layer.receive(channel)) == {"type": "early"}
    assert channel not in layer.channels  # an emptied channel is dropped, as Channels does


def test_group_send_reaches_members_on_every_loop(loops):
    layer = MultiLoopInMemoryChannelLayer()
    members = []
    for lt in loops:
        ch = lt.run(layer.new_channel())
        lt.run(layer.group_add("room", ch))
        members.append((lt, ch))
    parked = [lt.submit(layer.receive(ch)) for lt, ch in members]
    time.sleep(0.05)
    loops[0].run(layer.group_send("room", {"type": "push", "payload": {"k": [1]}}))
    got = [f.result(5) for f in parked]
    assert got == [{"type": "push", "payload": {"k": [1]}}] * 4
    got[0]["payload"]["k"].append(2)
    assert got[1]["payload"]["k"] == [1], "members share one message object"


def test_group_send_wakes_each_receiving_loop_once(loops, monkeypatch):
    layer = MultiLoopInMemoryChannelLayer()
    target = loops[1]
    chans = [target.run(layer.new_channel()) for _ in range(20)]
    for ch in chans:
        target.run(layer.group_add("room", ch))
    parked = [target.submit(layer.receive(ch)) for ch in chans]
    time.sleep(0.05)

    handoffs = []
    real = type(target.loop).call_soon_threadsafe

    def spy(self, callback, *args, **kwargs):
        if callback is layers._wake_pending:
            handoffs.append(len(args[0]))
        return real(self, callback, *args, **kwargs)

    monkeypatch.setattr(type(target.loop), "call_soon_threadsafe", spy)
    loops[0].run(layer.group_send("room", {"type": "tick"}))
    assert [f.result(5) for f in parked] == [{"type": "tick"}] * 20
    assert handoffs == [20], f"expected one hand-off for 20 receivers, got {handoffs}"


def test_messages_on_one_channel_keep_their_order_across_sender_loops(loops):
    """Four loops send 300 numbered messages each to one channel on a fifth
    receiver; each sender's messages arrive in order and none is lost."""
    layer = MultiLoopInMemoryChannelLayer(capacity=0)  # unbounded, as asyncio.Queue(0)
    receiver = LoopThread("receiver")
    try:
        channel = receiver.run(layer.new_channel())
        per_sender = 300

        async def sender(tag):
            for n in range(per_sender):
                await layer.send(channel, {"type": "m", "src": tag, "n": n})
                if n % 50 == 0:
                    await asyncio.sleep(0)

        async def drain():
            return [await layer.receive(channel) for _ in range(per_sender * len(loops))]

        received = receiver.submit(drain())
        sends = [lt.submit(sender(i)) for i, lt in enumerate(loops)]
        for s in sends:
            s.result(10)
        got = received.result(10)
    finally:
        receiver.stop()
    for tag in range(len(loops)):
        assert [m["n"] for m in got if m["src"] == tag] == list(range(per_sender))


def test_two_group_sends_reach_every_member_in_the_same_order(loops):
    layer = MultiLoopInMemoryChannelLayer(capacity=0)
    members = []
    for lt in loops:
        ch = lt.run(layer.new_channel())
        lt.run(layer.group_add("room", ch))
        members.append((lt, ch))
    rounds = 200

    async def blast(tag):
        for n in range(rounds):
            await layer.group_send("room", {"type": "m", "src": tag, "n": n})

    sends = [loops[0].submit(blast("a")), loops[1].submit(blast("b"))]
    for s in sends:
        s.result(10)

    async def drain(ch):
        return [await layer.receive(ch) for _ in range(2 * rounds)]

    seqs = [lt.run(drain(ch)) for lt, ch in members]
    orders = [[(m["src"], m["n"]) for m in seq] for seq in seqs]
    assert all(o == orders[0] for o in orders), "members saw the group sends in different orders"
    for tag in ("a", "b"):
        assert [n for src, n in orders[0] if src == tag] == list(range(rounds))


def test_a_full_channel_raises_before_any_hand_off(loops):
    layer = MultiLoopInMemoryChannelLayer(capacity=2)
    channel = loops[0].run(layer.new_channel())
    loops[1].run(layer.send(channel, {"type": "1"}))
    loops[2].run(layer.send(channel, {"type": "2"}))
    with pytest.raises(ChannelFull):
        loops[3].run(layer.send(channel, {"type": "3"}))
    # group_send skips a full member and still delivers to the others.
    other = loops[0].run(layer.new_channel())
    loops[0].run(layer.group_add("g", channel))
    loops[0].run(layer.group_add("g", other))
    loops[1].run(layer.group_send("g", {"type": "g"}))
    assert loops[0].run(layer.receive(other)) == {"type": "g"}
    assert [loops[0].run(layer.receive(channel)) for _ in range(2)] == [
        {"type": "1"},
        {"type": "2"},
    ]


def test_per_channel_capacity_applies(loops):
    layer = MultiLoopInMemoryChannelLayer(capacity=100, channel_capacity={"small.*": 1})
    loops[0].run(layer.send("small.one", {"type": "a"}))
    with pytest.raises(ChannelFull):
        loops[1].run(layer.send("small.one", {"type": "b"}))


def test_a_cancelled_receiver_that_was_picked_passes_the_wake_up_on(loops):
    """A sender picks the oldest parked receiver; if that receive() is
    cancelled before it resumes, the next receiver must still get the message."""
    layer = MultiLoopInMemoryChannelLayer()
    a, b = loops[0], loops[1]
    channel = "shared.chan"
    holder = {}

    async def park_first():
        holder["task"] = asyncio.ensure_future(layer.receive(channel))
        await asyncio.sleep(0.02)

    a.run(park_first())
    second = b.submit(layer.receive(channel))
    time.sleep(0.05)

    async def send_then_cancel_first():
        # On loop a, the first receiver's loop: send() picks it and sets its
        # future; cancelling its task before it resumes abandons a receiver
        # that was already picked.
        await layer.send(channel, {"type": "only"})
        holder["task"].cancel()
        await asyncio.sleep(0.02)
        return holder["task"].cancelled()

    assert a.run(send_then_cancel_first()) is True
    assert second.result(5) == {"type": "only"}


def test_a_cancelled_receiver_leaves_no_waiter_behind(loops):
    layer = MultiLoopInMemoryChannelLayer()
    fut = loops[0].submit(layer.receive("lone.chan"))
    time.sleep(0.05)
    fut.cancel()
    time.sleep(0.05)
    assert "lone.chan" not in layer._waiters
    assert "lone.chan" not in layer.channels


def test_the_expiry_sweep_runs_from_any_loop(loops, monkeypatch):
    layer = MultiLoopInMemoryChannelLayer(expiry=10, clean_interval=0)
    loops[0].run(layer.group_add("g", "stale.chan"))
    loops[0].run(layer.send("stale.chan", {"type": "old"}))
    loops[0].run(layer.group_add("g", "fresh.chan"))
    real_time = time.time
    monkeypatch.setattr(layers.time, "time", lambda: real_time() + 11)
    loops[1].run(layer.send("fresh.chan", {"type": "new"}))  # expires at +21
    loops[2].run(layer.group_send("g", {"type": "sweep"}))  # sweeps first
    assert "stale.chan" not in layer.channels
    assert "stale.chan" not in layer.groups["g"], (
        "a channel with an expired message stays in groups"
    )
    assert loops[3].run(layer.receive("fresh.chan")) == {"type": "new"}


def test_the_sweep_is_still_rate_limited(monkeypatch):
    layer = MultiLoopInMemoryChannelLayer(expiry=1, clean_interval=10)
    mono = {"t": 1000.0}
    wall = {"t": 5000.0}
    monkeypatch.setattr(layers.time, "monotonic", lambda: mono["t"])
    monkeypatch.setattr(layers.time, "time", lambda: wall["t"])

    async def scenario():
        await layer.group_send("g", {"type": "x"})  # sweeps; next sweep at 1010
        await layer.send("old.chan", {"type": "old"})  # expires at wall 5001
        wall["t"] += 2
        mono["t"] = 1005.0
        await layer.group_send("g", {"type": "x"})
        kept = "old.chan" in layer.channels
        mono["t"] = 1011.0
        await layer.group_send("g", {"type": "x"})
        return kept, "old.chan" in layer.channels

    assert asyncio.run(scenario()) == (True, False)


def test_flush_clears_messages_and_groups_and_keeps_receivers_waiting(loops):
    layer = MultiLoopInMemoryChannelLayer()
    loops[0].run(layer.send("a.chan", {"type": "x"}))
    loops[0].run(layer.group_add("g", "a.chan"))
    parked = loops[1].submit(layer.receive("b.chan"))
    time.sleep(0.05)
    loops[2].run(layer.flush())
    assert layer.channels == {} and layer.groups == {}
    loops[3].run(layer.send("b.chan", {"type": "after"}))
    assert parked.result(5) == {"type": "after"}


def test_a_closed_receiver_loop_does_not_break_the_sender(loops):
    layer = MultiLoopInMemoryChannelLayer()
    doomed = LoopThread("doomed")
    doomed.submit(layer.receive("gone.chan"))
    time.sleep(0.05)
    doomed.stop()  # its receive() never finishes; the loop is closed
    loops[0].run(layer.send("gone.chan", {"type": "late"}))  # must not raise
    assert loops[1].run(layer.receive("gone.chan")) == {"type": "late"}


def test_many_loops_many_channels_under_load(loops):
    """Every loop sends to channels owned by every other loop, concurrently."""
    layer = MultiLoopInMemoryChannelLayer(capacity=0)
    per_loop = 10
    owned = {
        i: [lt.run(layer.new_channel()) for _ in range(per_loop)] for i, lt in enumerate(loops)
    }
    msgs = 40

    async def send_all(src):
        for dst, chans in owned.items():
            if dst == src:
                continue
            for ch in chans:
                for n in range(msgs):
                    await layer.send(ch, {"type": "m", "src": src, "n": n})

    async def drain(chans):
        out = {}
        expected = msgs * (len(loops) - 1)
        for ch in chans:
            out[ch] = [await layer.receive(ch) for _ in range(expected)]
        return out

    drains = [lt.submit(drain(owned[i])) for i, lt in enumerate(loops)]
    sends = [lt.submit(send_all(i)) for i, lt in enumerate(loops)]
    for s in sends:
        s.result(20)
    for i, d in enumerate(drains):
        for ch, got in d.result(20).items():
            for src in range(len(loops)):
                if src == i:
                    continue
                assert [m["n"] for m in got if m["src"] == src] == list(range(msgs))
    assert layer.channels == {} and layer._waiters == {}


def test_channel_layers_config_selects_it(settings):
    from channels.layers import channel_layers

    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "djust.layers.MultiLoopInMemoryChannelLayer"}}
    channel_layers.backends.pop("default", None)
    try:
        assert isinstance(channel_layers["default"], MultiLoopInMemoryChannelLayer)
    finally:
        channel_layers.backends.pop("default", None)
