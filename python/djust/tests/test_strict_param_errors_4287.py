"""#4287: a strict refusal says which rule failed, from declared names only."""

from __future__ import annotations

import json
import logging

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.testing import LiveViewTestClient
from djust.validation import validate_handler_params

pytestmark = [pytest.mark.pr_tier]


class _V(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>x</div>"

    @event_handler(parameter_policy="strict")
    def only_value(self, value: str):
        pass

    @event_handler(parameter_policy="strict")
    def field_value(self, field: str, value: str = ""):
        pass

    @event_handler(parameter_policy="strict")
    def pos_only_kw(self, x: str, /, **kw):
        pass

    @event_handler(parameter_policy="strict")
    def typed(self, count: int):
        pass

    @event_handler(parameter_policy="strict")
    def pos_only(self, x: str, /, y: str = ""):
        pass


def _check(name, params, positional=None):
    kwargs = {} if positional is None else {"positional_args": positional}
    return validate_handler_params(getattr(_V(), name), params, name, **kwargs)


def test_undeclared_only_says_undeclared_and_never_echoes():
    r = _check("only_value", {"value": "x", "zz_secret_key": "s3cret"})
    assert r["valid"] is False
    assert "undeclared" in r["error"]
    assert r["missing"] == []
    dumped = json.dumps(r, default=str)
    assert "zz_secret_key" not in dumped and "s3cret" not in dumped


def test_missing_only_names_the_declared_parameter():
    r = _check("only_value", {})
    assert "'value'" in r["error"] and "missing" in r["error"]
    assert "undeclared" not in r["error"]
    assert r["missing"] == ["value"]


def test_one_x_payload_reports_missing_and_undeclared_without_the_key():
    r = _check("field_value", {"field_name": "a"})
    assert "'field'" in r["error"] and "undeclared" in r["error"]
    assert r["missing"] == ["field"]
    assert "field_name" not in json.dumps(r, default=str)


def test_duplicate_positional_and_named():
    r = _check("only_value", {"value": "b"}, positional=["a"])
    assert "'value' was supplied both positionally and by name" in r["error"]


def test_too_many_positional():
    r = _check("only_value", {}, positional=["a", "b"])
    assert "takes 1 positional argument" in r["error"]
    assert "1 positional arguments" not in r["error"]


def test_positional_only_passed_by_keyword():
    r = _check("pos_only", {"x": "a"})
    assert "'x' is positional-only" in r["error"]
    assert "undeclared" not in r["error"]


def test_positional_only_with_catch_all_is_named_too():
    r = _check("pos_only_kw", {"x": "a"})
    assert "'x' is positional-only" in r["error"]


def test_type_error_message_unchanged():
    r = _check("typed", {"count": "abc"})
    assert r["valid"] is False and "int" in r["error"] and "missing" not in r["error"]


def test_debug_log_names_undeclared_keys_only_at_debug(caplog):
    caplog.set_level(logging.INFO, "djust.validation")
    _check("only_value", {"value": "x", "zz_secret_key": "s3cret"})
    assert not [r for r in caplog.records if r.name == "djust.validation"]
    caplog.clear()
    caplog.set_level(logging.DEBUG, "djust.validation")
    _check("only_value", {"value": "x", "zz_secret_key": "s3cret", "bad\nkey": 1})
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "djust.validation")
    assert "zz_secret_key" in text and "s3cret" not in text
    assert "bad\nkey" not in text and "bad" in text


def test_test_client_detail_carries_missing_params():
    client = LiveViewTestClient(_V)
    client.mount()
    r = client.send_event("only_value", name="status", value="done")
    assert r["success"] is False and "undeclared" in r["error"]
    assert r["validation_details"]["missing_params"] == []
    r = client.send_event("only_value")
    assert r["validation_details"]["missing_params"] == ["value"]
