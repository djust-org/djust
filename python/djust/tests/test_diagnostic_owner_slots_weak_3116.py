"""#3116: the diagnostic owner slots must not keep a disconnected session alive.

``watch_diagnostic_owner`` records ``(container, attribute)`` in a ContextVar.
Any copy of the context taken while a turn is open (a task created inside the
turn, or a thread started from it on 3.14+) kept a strong reference to the
container, so a disconnected consumer and its runtime stayed alive for as long
as that copy did. The slots now hold weak references; a dead owner still
restricts diagnostics (fails closed).
"""

from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from djust._exposure_diagnostics import (
    _details_allowed,
    diagnostic_scope,
    diagnostics_allowed,
    owned_diagnostic_scope,
    watch_diagnostic_owner,
)


class _LegacyView:
    """A legacy owner: its presence never restricts diagnostics."""

    exposure_policy = "legacy"


class _Consumer:
    def __init__(self) -> None:
        self.view_instance = _LegacyView()

    @owned_diagnostic_scope
    async def receive(self, started: asyncio.Event, release: asyncio.Event, log: list) -> None:
        # A view starting a long-lived background task inside the turn: the
        # task's context is a copy of the turn's, owner slots included.
        async def room_clock() -> None:
            started.set()
            await release.wait()
            log.append("ran")

        self.task = asyncio.get_running_loop().create_task(room_clock())


@pytest.mark.asyncio
async def test_a_task_created_inside_a_turn_does_not_pin_the_consumer():
    started, release, log = asyncio.Event(), asyncio.Event(), []
    consumer = _Consumer()
    await consumer.receive(started, release, log)
    task = consumer.task
    await started.wait()

    probe = weakref.ref(consumer)
    consumer.task = None
    del consumer
    gc.collect()
    assert probe() is None, "the task's context copy kept the disconnected consumer alive"

    # The long-lived task is unaffected and still runs to completion.
    release.set()
    await task
    assert log == ["ran"]


def test_a_dead_owner_restricts_diagnostics():
    class Holder:
        pass

    holder = Holder()
    holder.view_instance = _LegacyView()
    with diagnostic_scope():
        watch_diagnostic_owner(holder, "view_instance")
        assert diagnostics_allowed() is True
        del holder
        gc.collect()
        # The owner can no longer be read: fail closed, as for an unreadable one.
        assert diagnostics_allowed() is False
    assert _details_allowed.get() is True


def test_watching_the_same_owner_twice_registers_one_slot():
    from djust._exposure_diagnostics import _owner_slots

    class Holder:
        view_instance = None

    holder = Holder()
    with diagnostic_scope():
        watch_diagnostic_owner(holder, "view_instance")
        watch_diagnostic_owner(holder, "view_instance")
        assert len(_owner_slots.get()) == 1
        watch_diagnostic_owner(holder, "other")
        assert len(_owner_slots.get()) == 2


def test_an_owner_without_weakref_support_is_kept_strongly():
    class Slotted:
        __slots__ = ("view_instance",)

    owner = Slotted()
    owner.view_instance = _LegacyView()
    with diagnostic_scope():
        watch_diagnostic_owner(owner, "view_instance")
        assert diagnostics_allowed() is True
