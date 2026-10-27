"""A truncated fingerprint must bound work without hiding later mutations."""

import pytest

from djust.change_detection import deep_fingerprint
from djust.decorators import event_handler
from djust.live_view import LiveView
from djust.state_pass import fingerprint_and_convert
from djust.websocket import _snapshot_assigns


def test_wide_container_fingerprint_is_bounded_and_fused_matches():
    data = list(range(100_000))
    fp, truncated = deep_fingerprint(data, budget=20)
    fused_fp, fused_truncated, converted = fingerprint_and_convert(data, "data", budget=20)
    assert truncated and fused_truncated
    assert fp == fused_fp
    assert len(repr(fp)) < 100
    assert converted[1] == data


def test_truncated_snapshot_and_dirty_tracking_conservatively_change():
    rows = list(range(20_004)) + [[0]]

    class View:
        pass

    view = View()
    view.rows = rows
    before = _snapshot_assigns(view)
    before_dirty = LiveView._dirty_value_fingerprint(rows)
    rows[-1][0] = -1
    assert _snapshot_assigns(view) != before
    assert LiveView._dirty_value_fingerprint(rows) != before_dirty


def test_nontruncated_unchanged_values_remain_equal():
    data = [1, 2, {"x": 3}]
    assert deep_fingerprint(data) == deep_fingerprint(data)
    assert LiveView._dirty_value_fingerprint(data) == LiveView._dirty_value_fingerprint(data)


def test_computed_rechecks_truncated_dependencies():
    from djust.decorators import computed

    class Value:
        def __init__(self):
            self.rows = [0] * 20_004 + [[1]]

        @computed("rows")
        def last(self):
            return self.rows[-1][0]

    value = Value()
    assert value.last == 1
    value.rows[-1][0] = 2
    assert value.last == 2


class LargeTailView(LiveView):
    template = "<div dj-root><p>{{ rows.20004.0 }}</p></div>"

    def mount(self, request, **kwargs):
        self.rows = [0] * 20_004 + [["before"]]

    def get_context_data(self, **kwargs):
        return {"rows": self.rows}

    @event_handler()
    def change_tail(self, **kwargs):
        self.rows[-1][0] = "after"


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_tail_mutation_reaches_browser_on_real_event():
    from django.test import override_settings
    from .test_in_place_mutation_2664 import _mounted, _receive_until

    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DJUST_CONFIG={"view_state_in_session": False}
    ):
        socket = await _mounted(f"{__name__}.LargeTailView", "/large-tail/")
        try:
            await socket.send_json_to(
                {"type": "event", "event": "change_tail", "params": {}, "ref": 1}
            )
            frame = await _receive_until(socket, {"patch", "html_update", "noop"})
            assert frame["type"] in ("patch", "html_update")
            assert "after" in str(frame)
        finally:
            await socket.disconnect()


@pytest.mark.parametrize("budget", [0, 1, 3, 4])
def test_fused_and_plain_fingerprints_agree_at_budget_boundary(budget):
    data = [1, 2, 3]
    fp, truncated = deep_fingerprint(data, budget)
    fused_fp, fused_truncated, converted = fingerprint_and_convert(data, "data", budget)
    assert (fp, truncated) == (fused_fp, fused_truncated)
    assert truncated == (budget < 4)
    assert converted[1] == data


def test_truncated_bookkeeping_stays_out_of_session_and_render_context():
    import json

    from djust.decorators import computed

    class View(LiveView):
        @computed("rows")
        def last(self):
            return self.rows[-1][0]

    view = View()
    view.rows = [0] * 20_004 + [["before"]]
    assert view.last == "before"
    view._prev_context_fingerprints = {"rows": object()}
    view.mark_clean()
    view._snapshot_user_private_attrs()
    private = view._get_private_state()
    context = view.get_context_data()
    for key in (
        "_djust_computed_cache",
        "_djust_computed_deps",
        "_djust_computed_lock",
        "_prev_context_fingerprints",
        "_dirty_baseline",
    ):
        assert key not in private
        assert key not in context
    json.dumps(private)
    json.dumps(context)


@pytest.mark.parametrize("factory", [set, frozenset])
def test_unhashable_fingerprint_in_set_falls_back_to_identity(factory):
    class UnhashableString(str):
        __hash__ = None

    class Member:
        _djust_fingerprint_state = True

        def __init__(self):
            self.state = {"name": UnhashableString("value")}

    value = factory([Member()])
    fingerprint, truncated = deep_fingerprint(value)
    assert fingerprint == ("i", id(value))
    assert not truncated
