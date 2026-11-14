"""The stored-session contract of the ADR-038 E5 matrix, without a browser (#4107).

``tests/playwright/test_exposure_matrix.py`` checks every destination of a live
flow; its stored-session row is ``_exposure_session_contract.stored_session_failures``.
These tests apply the same function to views shaped like the matrix's demo
views, driven through the real middleware stack over the page-POST fallback,
and gate it off both ways: a floor value that reaches the session, and an
undeclared value that reaches a client-facing answer.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from django.contrib.sessions.models import Session
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView, _automatic_state, state
from djust.decorators import event_handler
from djust.tests.http_fallback_matrix import PAGE, meta_digest, post_event

pytestmark = [pytest.mark.django_db(transaction=True)]

_SOURCE = Path(__file__).resolve().parents[3] / "tests/playwright/_exposure_session_contract.py"
_spec = importlib.util.spec_from_file_location("_exposure_session_contract", _SOURCE)
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)

UNDECLARED, PRIVATE = contract.CLIENT_SENTINELS
FLOOR = contract.FLOOR_SENTINEL
BODY = "\n<p>Count: <strong>{{ count }}</strong></p>\n"


class Matrix(LiveView):
    """The shape of the demo's ``ExposureMatrixView``."""

    exposure_policy = "explicit"
    template = PAGE % BODY
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.secret_note = UNDECLARED
        self._private_note = PRIVATE
        self.password = FLOOR

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class LegacyMatrix(LiveView):
    exposure_policy = "legacy"
    template = PAGE % BODY

    def mount(self, request, **kwargs):
        self.count = 0
        self.secret_note = UNDECLARED
        self._private_note = PRIVATE
        self.password = FLOOR

    def get_context_data(self, **kwargs):
        return {"count": self.count}

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class Leaks(LiveView):
    """Gate-off: a client-facing path that sends the undeclared value."""

    exposure_policy = "explicit"
    template = PAGE % (BODY + "<p>{{ note }}</p>\n")
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.secret_note = UNDECLARED
        self._private_note = PRIVATE
        self.password = FLOOR

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["note"] = self.secret_note
        return context

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


urlpatterns = [
    path("e5/explicit/", Matrix.as_view()),
    path("e5/legacy/", LegacyMatrix.as_view()),
    path("e5/leaks/", Leaks.as_view()),
]


@pytest.fixture(autouse=True)
def _routes():
    _automatic_state._NOTICED.clear()
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        LIVEVIEW_ALLOWED_MODULES=["djust"],
    ):
        yield


def flow(url: str):
    """A page load and two events: (everything the client received, session rows)."""
    client = Client()
    response = client.get(url)
    assert response.status_code == 200, response.status_code
    page = response.content.decode()
    tab = re.search(r'<meta name="djust-tab" content="([^"]+)">', page).group(1)
    token = meta_digest(page)
    received = [page, json.dumps(dict(response.headers.items()))]
    for _ in range(2):
        answer = post_event(client, url, "increment", {}, token, HTTP_X_DJUST_TAB=tab)
        assert answer.status_code == 200, answer.content[:300]
        received += [answer.content.decode(), json.dumps(dict(answer.headers.items()))]
        token = json.loads(answer.content).get("render_digest", token)
    rows = [row.get_decoded() for row in Session.objects.all()]
    return "\n".join(received), rows


def client_failures(received: str):
    return [value for value in (UNDECLARED, PRIVATE, FLOOR) if value in received]


@pytest.mark.parametrize("policy,url", [("explicit", "/e5/explicit/"), ("legacy", "/e5/legacy/")])
def test_the_matrix_views_meet_both_contracts(policy, url):
    received, rows = flow(url)
    assert rows, "the flow stored no session"
    assert client_failures(received) == []
    assert contract.stored_session_failures(policy, policy, rows) == []
    if policy == "legacy":  # a legacy view saves none of its own state
        assert not any(value in json.dumps(rows, default=str) for value in (UNDECLARED, PRIVATE))


def test_the_explicit_views_own_state_rests_only_in_its_envelope():
    _, rows = flow("/e5/explicit/")
    dumped = json.dumps(rows, default=str)
    assert UNDECLARED in dumped and PRIVATE in dumped and FLOOR not in dumped
    (row,) = rows
    envelopes = [v for k, v in row.items() if k.startswith(contract.ENVELOPE_PREFIX)]
    assert [e["values"]["secret_note"] for e in envelopes] == [UNDECLARED]
    assert [e["private"]["_private_note"] for e in envelopes] == [PRIVATE]
    assert all("password" not in e["values"] for e in envelopes)


def test_gate_off_a_saved_floor_value_turns_the_contract_red(monkeypatch):
    monkeypatch.setattr(_automatic_state._Names, "sensitive", lambda self, name: False)
    _, rows = flow("/e5/explicit/")
    failures = contract.stored_session_failures("gate", "explicit", rows)
    assert any(FLOOR in failure for failure in failures), failures


def test_gate_off_a_client_path_that_sends_the_undeclared_value_is_still_red():
    received, rows = flow("/e5/leaks/")
    assert client_failures(received) == [UNDECLARED]
    # The stored-session contract does not excuse it: that row is unchanged.
    assert contract.stored_session_failures("leaks", "explicit", rows) == []


def _envelope(**over):
    envelope = {
        "binding": "a" * 64,
        "expires": 1,
        "values": {"secret_note": UNDECLARED},
        "private": {"_private_note": PRIVATE},
    }
    envelope.update(over)
    return envelope


def test_the_contract_accepts_only_the_two_resting_places():
    good = [{"_djust_auto_x": _envelope()}]
    assert contract.stored_session_failures("t", "explicit", good) == []
    cases = {
        "another session key": [{"_djust_auto_x": _envelope(), "note": UNDECLARED}],
        "the declared envelope": [
            {"_djust_auto_x": _envelope(), "_djust_explicit_x": {"values": {"n": PRIVATE}}}
        ],
        "another name": [{"_djust_auto_x": _envelope(values={"other": UNDECLARED})}],
        "another section": [
            {"_djust_auto_x": _envelope(values={"secret_note": UNDECLARED, "p": PRIVATE})}
        ],
        "a floor value": [
            {"_djust_auto_x": _envelope(values={"secret_note": UNDECLARED, "password": FLOOR})}
        ],
        "no binding": [{"_djust_auto_x": _envelope(binding=None)}],
        "no lifetime": [{"_djust_auto_x": _envelope(expires=None)}],
        "nothing saved": [{"other": 1}],
    }
    for name, rows in cases.items():
        assert contract.stored_session_failures("t", "explicit", rows), name


def test_the_floor_is_asserted_for_the_legacy_policy_too():
    assert contract.stored_session_failures("t", "legacy", [{"k": FLOOR}])
    assert contract.stored_session_failures("t", "legacy", [{"k": "fine"}]) == []
