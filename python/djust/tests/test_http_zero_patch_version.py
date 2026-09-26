"""The HTTP fallback answers a render with no DOM change without resetting.

A handler whose render changes nothing produced zero patches. The fallback
then reset the diff baseline and sent full HTML, so the server's next version
restarted at 1 while the client expected the next number: the client's
version check failed and it reloaded the page, losing its state. An ADR-034
client-owned dropdown hits this on every selection (the choice changes no
markup). The answer is now an empty patch list carrying the new version, as
the socket runtime's no-op is.
"""

import json

import pytest
from django.test import RequestFactory

from djust import LiveView
from djust.components.interactive import DropdownMenu
from djust.decorators import event_handler


class QuietPage(LiveView):
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
def test_versions_stay_consecutive_across_renders_without_changes():
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    html = QuietPage().get(initial).content.decode()
    component_id = html.split('data-component-id="', 1)[1].split('"', 1)[0]

    frames = [
        _post(initial, "bump", {}),
        _post(initial, "select", {"component_id": component_id, "value": "one"}),
        _post(initial, "touch_private", {}),
        _post(initial, "bump", {}),
    ]
    versions = [frame["version"] for frame in frames]
    assert versions == list(range(versions[0], versions[0] + 4)), frames
    # The unchanged renders answer with no patches and no full HTML.
    assert frames[1]["patches"] == [] and "html" not in frames[1]
    assert frames[2]["patches"] == [] and "html" not in frames[2]
    assert frames[3]["patches"], frames[3]
