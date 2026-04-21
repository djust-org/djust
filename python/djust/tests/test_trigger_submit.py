"""Tests for LiveView.trigger_submit() — server-side API for dj-trigger-action."""

from __future__ import annotations

from djust.live_view import LiveView


def test_trigger_submit_queues_push_event():
    class V(LiveView):
        pass

    v = V()
    v.trigger_submit("#payment-form")
    assert v._pending_push_events == [("dj:trigger-submit", {"selector": "#payment-form"})]


def test_trigger_submit_passes_selector_verbatim():
    class V(LiveView):
        pass

    v = V()
    v.trigger_submit('form[data-name="oauth"]')
    ev_name, payload = v._pending_push_events[-1]
    assert ev_name == "dj:trigger-submit"
    assert payload["selector"] == 'form[data-name="oauth"]'


def test_multiple_trigger_submits_queue_independently():
    class V(LiveView):
        pass

    v = V()
    v.trigger_submit("#first")
    v.trigger_submit("#second")
    assert len(v._pending_push_events) == 2
    assert v._pending_push_events[0][1]["selector"] == "#first"
    assert v._pending_push_events[1][1]["selector"] == "#second"


def test_trigger_submit_coexists_with_push_event():
    class V(LiveView):
        pass

    v = V()
    v.push_event("flash", {"message": "saving"})
    v.trigger_submit("#payment-form")
    # Order preserved — flash arrives at client first, then the submit trigger.
    assert v._pending_push_events[0][0] == "flash"
    assert v._pending_push_events[1][0] == "dj:trigger-submit"
