"""
Tests for Phase 7.6 #35 v1 — /eval_handler/ endpoint.
"""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings

from djust.observability.registry import _clear_registry, register_view
from djust.observability.views import eval_handler


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class _FakeCounterView:
    def __init__(self):
        self.count = 0
        self.label = "initial"

    def increment(self):
        self.count += 1
        return {"new_count": self.count}

    def add(self, amount: int = 1):
        self.count += amount

    def set_label(self, value: str = ""):
        self.label = value

    def explode(self):
        raise RuntimeError("handler boom")

    async def async_handler(self):  # noqa: RUF029
        self.count += 1


def _post(body: dict, session_id: str = "s"):
    rf = RequestFactory()
    return rf.post(
        f"/?session_id={session_id}",
        data=json.dumps(body),
        content_type="application/json",
    )


# --- Happy path -----------------------------------------------------------


@override_settings(DEBUG=True)
def test_eval_returns_delta_and_result():
    view = _FakeCounterView()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "increment"}))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["handler_name"] == "increment"
    assert data["before_assigns"]["count"] == 0
    assert data["after_assigns"]["count"] == 1
    assert data["delta"]["change_count"] == 1
    assert "count" in data["delta"]["modified"]
    assert data["delta"]["modified"]["count"] == {"before": 0, "after": 1}
    assert data["result"] == {"new_count": 1}


@override_settings(DEBUG=True)
def test_eval_passes_params():
    view = _FakeCounterView()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "add", "params": {"amount": 5}}))
    data = json.loads(resp.content)
    assert data["after_assigns"]["count"] == 5
    assert data["delta"]["modified"]["count"]["after"] == 5


@override_settings(DEBUG=True)
def test_eval_change_count_zero_when_handler_is_noop():
    view = _FakeCounterView()
    register_view("s", view)
    # set_label to the same value — no state change.
    resp = eval_handler(_post({"handler_name": "set_label", "params": {"value": "initial"}}))
    data = json.loads(resp.content)
    assert data["delta"]["change_count"] == 0


# --- Error paths ----------------------------------------------------------


@override_settings(DEBUG=True)
def test_eval_405_for_non_post():
    view = _FakeCounterView()
    register_view("s", view)
    rf = RequestFactory()
    resp = eval_handler(rf.get("/?session_id=s"))
    assert resp.status_code == 405


@override_settings(DEBUG=True)
def test_eval_400_when_session_id_missing():
    resp = eval_handler(_post({"handler_name": "increment"}, session_id=""))
    assert resp.status_code == 400


@override_settings(DEBUG=True)
def test_eval_404_when_session_unknown():
    resp = eval_handler(_post({"handler_name": "increment"}, session_id="never"))
    assert resp.status_code == 404


@override_settings(DEBUG=True)
def test_eval_400_when_handler_name_missing():
    view = _FakeCounterView()
    register_view("s", view)
    resp = eval_handler(_post({}))
    assert resp.status_code == 400


@override_settings(DEBUG=True)
def test_eval_404_when_handler_doesnt_exist():
    view = _FakeCounterView()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "not_a_handler"}))
    assert resp.status_code == 404


@override_settings(DEBUG=True)
def test_eval_400_when_handler_is_async():
    view = _FakeCounterView()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "async_handler"}))
    assert resp.status_code == 400
    data = json.loads(resp.content)
    assert "async" in data["error"]


@override_settings(DEBUG=True)
def test_eval_400_when_params_type_error():
    """Wrong kwargs should surface as 400 so the caller can fix their call."""
    view = _FakeCounterView()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "add", "params": {"wrong_kwarg": 1}}))
    assert resp.status_code == 400


@override_settings(DEBUG=True)
def test_eval_500_when_handler_raises():
    view = _FakeCounterView()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "explode"}))
    assert resp.status_code == 500
    data = json.loads(resp.content)
    assert "RuntimeError" in data["error"]


@override_settings(DEBUG=True)
def test_eval_400_on_invalid_json_body():
    view = _FakeCounterView()
    register_view("s", view)
    rf = RequestFactory()
    resp = eval_handler(
        rf.post("/?session_id=s", data=b"not-json", content_type="application/json")
    )
    assert resp.status_code == 400


@override_settings(DEBUG=False)
def test_eval_404_when_debug_off():
    view = _FakeCounterView()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "increment"}))
    assert resp.status_code == 404
