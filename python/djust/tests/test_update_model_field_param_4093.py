"""#4093: the built-in ``update_model`` handler and a non-string ``field``.

``LiveViewSmokeTest`` reported ``update_model`` failing for ``field=None``, a
list and a dict. Reproduced: the handler never ran. The shared parameter
validator refused those payloads with the transports' standard
invalid-parameter answer (``field: expected str``), and the fuzzer counted that
refusal as a handler failure, for ``update_model`` and every other handler with
a ``str`` parameter. Each case below pins the answer and that nothing changed.
"""

from __future__ import annotations

import logging

import pytest
from django.test import TestCase

from djust import LiveView, state
from djust.decorators import event_handler
from djust.testing import LiveViewSmokeTest, LiveViewTestClient


class BoundView(LiveView):
    exposure_policy = "explicit"
    template = '<div><input dj-model="q"><input dj-model="n"></div>'
    q = state("")
    n = state(0)
    is_admin = False

    def get_context_data(self, **kwargs):
        return {"q": self.q, "n": self.n}


def _client():
    client = LiveViewTestClient(BoundView).mount()
    client.render()  # records the dj-model allowlist, as every transport's render does
    return client


@pytest.mark.parametrize("field", [None, ["q"], {"q": 1}], ids=["none", "list", "dict"])
def test_a_non_string_field_gets_the_invalid_parameter_answer(field):
    client = _client()
    result = client.send_event("update_model", field=field, value="x")
    assert result["success"] is False
    assert "field: expected str" in result["error"]
    assert result["validation_details"]["type_errors"]
    assert client.view_instance.q == ""


@pytest.mark.parametrize(
    "field",
    [
        5,  # coerced to "5" by the validator: no such field
        "x" * 10_000,
        "q.real",
        "__class__",
        "__dict__",
        "_state_q",
        "is_admin",  # public, but no dj-model binding (mass-assignment guard)
        "request",
        "template_name",
    ],
)
def test_a_name_that_is_not_a_bound_field_changes_nothing(field):
    client = _client()
    before = dict(client.get_state())
    result = client.send_event("update_model", field=field, value="x")
    assert result["success"] is True  # refused inside the handler, as before
    assert client.get_state() == before
    assert client.view_instance.is_admin is False


def test_a_bound_field_is_still_set():
    client = _client()
    assert client.send_event("update_model", field="q", value="hello")["success"]
    assert client.send_event("update_model", field="n", value="7")["success"]
    assert (client.view_instance.q, client.view_instance.n) == ("hello", 7)


def test_an_over_long_name_is_bounded_in_the_log(caplog):
    """Pin: the ``djust.*`` log sanitizer filter bounds the client's name."""
    client = _client()
    with caplog.at_level(logging.WARNING, logger="djust.mixins.model_binding"):
        client.send_event("update_model", field="y" * 10_000 + "\nforged line", value="x")
    messages = [r.getMessage() for r in caplog.records]
    assert messages
    assert all(len(m) < 1_000 and "\n" not in m for m in messages)


class FuzzUpdateModel(TestCase, LiveViewSmokeTest):
    views = [BoundView]
    fuzz = True
    check_queries = False

    def _get_views(self):
        return [BoundView]

    def test_smoke_render(self):  # only the fuzzers are under test here
        pass


def test_the_fuzzer_does_not_count_a_validation_refusal_as_a_handler_failure():
    """The fuzzer's own payloads (``None``, ``[]``, ``{}`` for ``field``) reach
    ``update_model`` and are refused by validation: not a failure."""
    FuzzUpdateModel("test_fuzz_handlers_succeed").test_fuzz_handlers_succeed()


class RaisingView(LiveView):
    exposure_policy = "explicit"
    template = "<div></div>"

    @event_handler
    def boom(self, value: str = "", **kwargs):
        raise ValueError("handler failed on valid input")


class _FuzzRaising(TestCase, LiveViewSmokeTest):
    __test__ = False  # driven by the test below
    fuzz = True

    def _get_views(self):
        return [RaisingView]


def test_the_fuzzer_still_reports_a_handler_that_raises_on_valid_input():
    """The validation skip cannot hide a real failure: ``boom`` accepts every
    string payload the fuzzer sends and then raises."""
    with pytest.raises(AssertionError, match=r"RaisingView\.boom .*handler failed on valid input"):
        _FuzzRaising("test_fuzz_handlers_succeed").test_fuzz_handlers_succeed()
