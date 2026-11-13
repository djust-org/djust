"""The HTTP fallback answers a render with no DOM change without a reload.

Until 2.0 a legacy view's fallback POST diffed against the renderer the
previous request left in the state backend, so a handler whose render changed
nothing had to answer an empty patch list carrying the next version, or the
client's version check failed and it reloaded the page. 2.0 removed that
cache: every fallback request renders on a fresh renderer and answers with the
whole page as v1, which the client re-bases on instead of checking
(``tests/js/http-fallback-whole-page-version.test.js``). An explicit view
always answered this way. Pinned here: every answer, changing or not, is that
whole page.
"""

import json

import pytest
from django.test import RequestFactory

from djust import LiveView
from djust.components.interactive import DropdownMenu
from djust.decorators import event_handler


class QuietPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ menu }}<p>{{ count }}</p></div>"
    menu = DropdownMenu(label="Menu", items=[{"label": "One", "value": "one"}], visibility="client")

    def mount(self, request, **kwargs):
        self.count = 0
        self._hidden = 0

    @event_handler()
    def bump(self):
        self.count += 1

    @event_handler()
    def touch_private(self):
        self._hidden += 1


def _post(initial, event, params):
    request = RequestFactory().post(
        initial.path,
        data=json.dumps(params),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    response = QuietPage().post(request)
    assert response.status_code == 200, response.content
    return json.loads(response.content)


@pytest.mark.django_db
def test_every_answer_is_a_whole_page_the_client_rebases_on():
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    QuietPage().get(initial)

    frames = [
        _post(initial, "bump", {}),
        _post(initial, "touch_private", {}),
        _post(initial, "bump", {}),
    ]
    for frame in frames:
        assert "patches" not in frame and frame["version"] == 1, frame
    # Nothing is kept between requests: each POST mounts again (count 0 + 1).
    assert ">1</p>" in frames[0]["html"]
    assert ">0</p>" in frames[1]["html"]
    assert ">1</p>" in frames[2]["html"]
