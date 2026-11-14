"""Saved view state (#4107, per tab #4106) together with fallback patches (#4108).

Over the page-POST fallback a request restores the tab's saved state, then
#4108 renders a baseline on a deep copy of the view, then the handler runs, and
the answer is patches when the client's token matches that baseline. These
tests drive both through the real middleware stack (``django.test.Client``),
with state held in PLAIN attributes, so it is the automatic save that carries
it and the tab id that selects it.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView, _automatic_state
from djust.decorators import event_handler
from djust.tests.http_fallback_matrix import PAGE, answer_kind, meta_digest, post_event

pytestmark = [pytest.mark.django_db(transaction=True)]

MOUNTS: list = []


class Plain(LiveView):
    """A counter and a label in plain attributes: nothing is declared to persist."""

    exposure_policy = "explicit"
    template = (
        PAGE % "\n<p>Value: <strong>{{ count }}</strong></p>\n<p>Tab: <em>{{ label }}</em></p>\n"
    )

    def mount(self, request, **kwargs):
        MOUNTS.append(type(self).__name__)
        self.count = 0
        self.label = "new"

    def get_context_data(self, **kwargs):
        self.renders_seen = getattr(self, "renders_seen", 0) + 1  # a side effect of rendering
        return super().get_context_data(count=self.count, label=self.label, **kwargs)

    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    @event_handler
    def name(self, value: str = "", **kwargs):
        self.label = value


class Service:
    """Not saveable: a view holding one mounts again on every fallback request."""


class Remounts(Plain):
    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.service = Service()


class Limited(Plain):
    automatic_state_max_kb = 8

    @event_handler
    def bloat(self, **kwargs):
        self.count += 1
        self.blob = ["item-%05d" % n for n in range(3000)]


urlpatterns = [
    path("s/plain/", Plain.as_view()),
    path("s/remounts/", Remounts.as_view()),
    path("s/limited/", Limited.as_view()),
]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": ["djust"],
}


@pytest.fixture(autouse=True)
def _routes():
    MOUNTS.clear()
    _automatic_state._NOTICED.clear()
    with override_settings(**SETTINGS):
        yield


class Tab:
    """One browser tab: its page load's tab id and the render token it last got."""

    def __init__(self, client: Client, url: str) -> None:
        self.client, self.url = client, url
        response = client.get(url)
        assert response.status_code == 200, response.status_code
        page = response.content.decode()
        self.token = meta_digest(page)
        found = re.search(r'<meta name="djust-tab" content="([^"]+)">', page)
        assert self.token and found, page[:300]
        self.id = found.group(1)

    def send(self, event: str, params: Any = None, status: int = 200) -> dict:
        response = post_event(
            self.client, self.url, event, params or {}, self.token, HTTP_X_DJUST_TAB=self.id
        )
        assert response.status_code == status, (response.status_code, response.content[:300])
        answer = json.loads(response.content)
        if "render_digest" in answer:
            self.token = answer["render_digest"]
        return answer

    def resync(self) -> dict:
        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_RESYNC="1",
            HTTP_X_DJUST_TAB=self.id,
        )
        assert response.status_code == 200, response.content[:300]
        answer = json.loads(response.content)
        self.token = answer.get("render_digest", self.token)
        return answer


def _texts(answer: dict) -> list:
    return [patch["text"] for patch in answer["patches"] if patch.get("type") == "SetText"]


def _saved(client: Client) -> list:
    return [value for key, value in client.session.items() if key.startswith("_djust_auto_")]


def test_the_baseline_is_rendered_from_the_restored_state_so_each_answer_is_a_patch():
    """Restore, then the baseline, then the handler: the second and third
    answers are patches against what the client shows (1, then 2), which only
    a baseline rendered from the RESTORED count can produce. mount() runs once."""
    tab = Tab(Client(), "/s/plain/")
    answers = [tab.send("increment") for _ in range(3)]
    assert [answer_kind(answer) for answer in answers] == ["patches"] * 3
    assert [_texts(answer) for answer in answers] == [["1"], ["2"], ["3"]]
    assert MOUNTS == ["Plain"]  # the page GET's; no POST mounted again


def test_the_baseline_copy_is_not_saved_and_marks_nothing_as_changed():
    """The pre-handler render runs ``get_context_data()`` on a deep copy. Its
    side effect (``renders_seen``) must not reach the saved state through the
    copy, and must not make an untouched name a handler's change."""
    client = Client()
    tab = Tab(client, "/s/plain/")
    tab.send("increment")
    (envelope,) = _saved(client)
    # The page GET saved 1 (its own render). This request restored 1, the
    # baseline rendered on a COPY (which counted to 2 there), and the save ran
    # before the answer's render: still 1, and not a handler's change.
    assert envelope["values"]["count"] == 1
    assert envelope["values"]["renders_seen"] == 1
    assert "renders_seen" not in envelope["changed"]
    assert "label" not in envelope["changed"] and "count" in envelope["changed"]
    tab.send("name", {"value": "mine"})
    (envelope,) = _saved(client)
    assert envelope["values"]["renders_seen"] == 1
    assert envelope["changed"] == ["count", "label"]


def test_a_request_that_had_to_mount_again_still_answers_correct_patches(caplog):
    """An unsaveable attribute makes every request run mount(); the saved state
    goes back over it BEFORE the baseline, so the patches are still right."""
    tab = Tab(Client(), "/s/remounts/")
    answers = [tab.send("increment") for _ in range(3)]
    assert MOUNTS == ["Remounts"] * 4
    assert [answer_kind(answer) for answer in answers] == ["patches"] * 3
    assert [_texts(answer) for answer in answers] == [["1"], ["2"], ["3"]]


def test_two_tabs_each_get_patches_for_their_own_state():
    """The tab id and the render token travel together: each tab's baseline is
    its own restored state, so neither is sent the other's values, and neither
    falls back to a whole page because of the other."""
    client = Client()
    one = Tab(client, "/s/plain/")
    two = Tab(client, "/s/plain/")
    assert one.id != two.id
    first = [one.send("increment"), one.send("increment"), one.send("name", {"value": "one"})]
    second = [two.send("increment"), two.send("name", {"value": "two"})]
    back = one.send("increment")
    assert [answer_kind(a) for a in (*first, *second, back)] == ["patches"] * 6
    assert [_texts(a) for a in first] == [["1"], ["2"], ["one"]]
    assert [_texts(a) for a in second] == [["1"], ["two"]]
    assert _texts(back) == ["3"]
    assert len(_saved(client)) == 2


def test_a_token_with_another_tabs_id_is_answered_with_that_tabs_whole_page():
    """Tab one's token names tab one's render. Sent with tab two's id, the
    baseline is tab two's state: no match, so the whole page of tab two's
    state, never a patch computed against the wrong page."""
    client = Client()
    one = Tab(client, "/s/plain/")
    two = Tab(client, "/s/plain/")
    one.send("increment")
    one.send("increment")
    two.send("name", {"value": "two"})
    response = post_event(client, "/s/plain/", "increment", {}, one.token, HTTP_X_DJUST_TAB=two.id)
    answer = json.loads(response.content)
    assert answer_kind(answer) == "html"
    assert ">1</strong>" in answer["html"] and ">two</em>" in answer["html"]


def test_a_resync_renders_the_tabs_own_saved_state():
    client = Client()
    one = Tab(client, "/s/plain/")
    two = Tab(client, "/s/plain/")
    one.send("increment")
    one.send("increment")
    two.send("increment")
    before = len(MOUNTS)
    answer = one.resync()
    assert ">2</strong>" in answer["html"]
    assert ">1</strong>" in two.resync()["html"]
    assert len(MOUNTS) == before  # rebuilt from the saved state, not mounted
    assert _texts(one.send("increment")) == ["3"]  # and it counts on, with a patch


def test_a_resync_without_a_tab_id_restores_nothing_and_never_another_tabs_state():
    client = Client()
    tab = Tab(client, "/s/plain/")
    tab.send("increment")
    response = client.post(
        "/s/plain/", data="{}", content_type="application/json", HTTP_X_DJUST_RESYNC="1"
    )
    assert response.status_code == 200
    assert ">0</strong>" in json.loads(response.content)["html"]


def test_a_refused_oversized_change_leaves_the_token_and_the_saved_state_usable():
    client = Client()
    tab = Tab(client, "/s/limited/")
    assert _texts(tab.send("increment")) == ["1"]
    token = tab.token
    refused = tab.send("bloat", status=413)
    assert refused["code"] == "state_too_large" and "blob" in refused["error"]
    assert "render_digest" not in refused and tab.token == token
    (envelope,) = _saved(client)
    assert envelope["values"]["count"] == 1 and "blob" not in envelope["values"]
    # The page still shows 1 and the client still holds that page's token.
    after = tab.send("increment")
    assert answer_kind(after) == "patches" and _texts(after) == ["2"]
