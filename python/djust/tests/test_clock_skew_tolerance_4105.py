"""Saved state written slightly "in the future" by another process is kept (#4105).

Saved state records when it was written, in whole seconds of the writing
process's clock, and a reader refused anything written after its own "now". A
reader one second behind the writer (or less, at a whole-second boundary)
therefore discarded fresh state and mounted from defaults, with no log line.
Now a skew up to ``CLOCK_SKEW_TOLERANCE`` is accepted, and a refusal past it
is logged. The clock is driven by the test, never read.
"""

from __future__ import annotations

import json
import logging
import re
import time

import pytest
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView
from djust._automatic_state import TAB_HEADER
from djust._exposure_sessions import CLOCK_SKEW_TOLERANCE, written_in_the_future
from djust.decorators import event_handler, state
from djust.tests.http_fallback_matrix import PAGE

pytestmark = [pytest.mark.django_db(transaction=True)]

T = 2_000_000_000  # a fixed "now" for the writer


class SkewCounter(LiveView):
    exposure_policy = "explicit"
    template = PAGE % '<p id="d">{{ declared }}</p><p id="a">{{ automatic }}</p>'
    declared = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.automatic = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(declared=self.declared, automatic=self.automatic, **kwargs)

    @event_handler
    def increment(self, **kwargs):
        self.declared += 1
        self.automatic += 1


urlpatterns = [path("skew/", SkewCounter.as_view())]


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        SESSION_ENGINE="django.contrib.sessions.backends.db",
    ):
        yield


@pytest.fixture
def clock(monkeypatch):
    now = {"t": float(T)}
    monkeypatch.setattr(time, "time", lambda: now["t"])
    return now


def _post(client: Client, tab: str) -> tuple[str, str]:
    answer = client.post(
        "/skew/",
        data=json.dumps({}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT="increment",
        **{"HTTP_" + TAB_HEADER.upper().replace("-", "_"): tab},
    )
    assert answer.status_code == 200, answer.content
    html = answer.json().get("html") or json.dumps(answer.json())
    declared = re.search(r'id=\\?"d\\?">(\d+)<', html)
    automatic = re.search(r'id=\\?"a\\?">(\d+)<', html)
    assert declared and automatic, html
    return declared.group(1), automatic.group(1)


def _written_by_a_process_ahead(clock) -> tuple[Client, str]:
    client = Client()
    page = client.get("/skew/")
    tab = re.search(r'name="djust-tab" content="([^"]+)"', page.content.decode()).group(1)
    assert _post(client, tab) == ("1", "1")
    return client, tab


def test_a_reader_just_inside_the_tolerance_keeps_the_state(clock, caplog):
    client, tab = _written_by_a_process_ahead(clock)
    clock["t"] = float(T - CLOCK_SKEW_TOLERANCE + 0.5)  # reader's clock is behind
    with caplog.at_level(logging.WARNING):
        assert _post(client, tab) == ("2", "2")
    assert "ahead of this process's clock" not in caplog.text


def test_a_reader_just_outside_the_tolerance_mounts_fresh_and_says_why(clock, caplog):
    client, tab = _written_by_a_process_ahead(clock)
    clock["t"] = float(T - CLOCK_SKEW_TOLERANCE - 1)
    with caplog.at_level(logging.WARNING):
        assert _post(client, tab) == ("1", "1")
    assert "ahead of this process's clock" in caplog.text
    assert "declared server state" in caplog.text and "saved view state" in caplog.text


def test_the_boundary_of_the_helper():
    assert not written_in_the_future(T + CLOCK_SKEW_TOLERANCE, T, "x")
    assert written_in_the_future(T + CLOCK_SKEW_TOLERANCE + 1, T, "x")
    assert not written_in_the_future(T - 5, T, "x")
