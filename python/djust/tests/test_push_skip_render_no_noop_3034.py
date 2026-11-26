"""#3034: a skip-render server push or DB notification sends no ``noop`` frame.

A push has no ``ref`` to acknowledge. The client's noop handler falls back to
the oldest in-flight user event when a noop carries no ``ref``, so a push noop
arriving mid-event ended that event's loading state early. The skip-render
arms of ``server_push`` (stock and worker-offloaded turns) and ``db_notify``
now send nothing of their own; side effects the hook queued still go out.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust.tests._frames import effects_of, is_ack, legacy_type
from djust.tests.test_skip_render_force_parity_2834 import _mounted_consumer

_ALLOWED = "djust.tests.test_push_skip_render_no_noop_3034"


class QuietPushView(LiveView):
    exposure_policy = "explicit"
    template = f'<div dj-root dj-view="{_ALLOWED}.QuietPushView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}

    def handle_skip(self, **kwargs):
        self.push_event("pinged", {"n": 1})
        self._skip_render = True

    def handle_change(self, **kwargs):
        self.c += 1

    def handle_info(self, message):
        self.push_event("notified", {})
        self._skip_render = True


def _types(sent):
    """Each frame's 1.x name; an effects-only render frame (#3658) is ``"effects"``,
    not the ``"noop"`` ack that a skipped background turn must not send."""
    return ["effects" if is_ack(f) and f.get("effects") else legacy_type(f) for f in sent]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestPushSkipRenderSendsNoNoop:
    async def test_server_push_skip_sends_no_noop_but_flushes_side_effects(self):
        consumer = await _mounted_consumer(QuietPushView)

        await consumer.server_push({"handler": "handle_skip", "payload": {}})

        assert "noop" not in _types(consumer.sent), consumer.sent
        pushes = [f for f in effects_of(consumer.sent, "push_event")]
        assert [p["event"] for p in pushes] == ["pinged"], consumer.sent
        assert not consumer._render_lock.locked()

    async def test_server_push_that_changes_state_still_renders(self):
        """Gate-off sibling: only the skip arm lost its frame."""
        consumer = await _mounted_consumer(QuietPushView)

        await consumer.server_push({"handler": "handle_change", "payload": {}})

        assert {"patch", "html_update"} & set(_types(consumer.sent)), consumer.sent

    async def test_offloaded_server_push_skip_sends_no_noop(self, monkeypatch):
        from djust import worker_pool

        monkeypatch.setattr(worker_pool, "offload_enabled", lambda: True)
        consumer = await _mounted_consumer(QuietPushView)
        taken = []
        offloaded = consumer._run_server_push_turn_offloaded

        async def spy(*args, **kwargs):
            taken.append(1)
            return await offloaded(*args, **kwargs)

        consumer._run_server_push_turn_offloaded = spy

        await consumer.server_push({"handler": "handle_skip", "payload": {}})

        assert taken, "the offloaded turn was not the one exercised"
        assert "noop" not in _types(consumer.sent), consumer.sent
        assert effects_of(consumer.sent, "push_event"), consumer.sent
        assert not consumer._render_lock.locked()

    async def test_db_notify_skip_sends_no_noop_but_flushes_side_effects(self):
        consumer = await _mounted_consumer(QuietPushView)

        await consumer.db_notify({"channel": "orders", "payload": {}})

        assert "noop" not in _types(consumer.sent), consumer.sent
        pushes = [f for f in effects_of(consumer.sent, "push_event")]
        assert [p["event"] for p in pushes] == ["notified"], consumer.sent
