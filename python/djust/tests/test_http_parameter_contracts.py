"""ADR-036 P2: HTTP delivery of owner-addressed parameter contracts.

The initial page carries its root mount's contracts outside dj-root; HTTP
fallback render responses carry the rendered tree's snapshot, with explicit
clears for a client whose scope is strict.
"""

import json
import re

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust import state
from djust._parameter_metadata import expand_parameter_manifest

BLOCK = re.compile(
    r'<script type="application/json" data-djust-parameter-contracts>(.*?)</script>', re.S
)


class StrictPage(LiveView):
    exposure_policy = "explicit"
    n = state(0)
    template = "<div dj-root><span>{{ n }}</span></div>"

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler(parameter_policy="strict")
    def choose(self, value: int = 5):
        self.n = value

    @event_handler(parameter_policy="strict")
    def quiet(self):
        self._skip_render = True


class BrokenPage(StrictPage):
    @event_handler(parameter_policy="strict")
    def broken(self, value: dict):
        pass


def _get(view_class):
    from djust.tests.test_exposure_runtime import make_request

    request = make_request()
    response = view_class().get(request)
    assert response.status_code == 200
    return request, response.content.decode()


def _post(view_class, request, event, params, header=False):
    from django.test import RequestFactory

    extra = {"HTTP_X_DJUST_PARAMETER_CONTRACTS": "1"} if header else {}
    post = RequestFactory().post(
        request.path,
        data=json.dumps(params),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
        **extra,
    )
    post.user, post.session, post.tenant = request.user, request.session, None
    response = view_class().post(post)
    return response.status_code, json.loads(response.content)


def _path(view_class):
    return f"{view_class.__module__}.{view_class.__name__}"


@pytest.mark.django_db
def test_initial_page_carries_root_contracts_outside_the_live_root():
    _, html = _get(StrictPage)
    blocks = BLOCK.findall(html)
    assert len(blocks) == 1
    payload = json.loads(blocks[0])
    assert payload["view"] == _path(StrictPage)
    root = expand_parameter_manifest(payload["contracts"])[0]
    assert (root["view_id"], root["component_id"]) == (None, None)
    assert root["handlers"]["choose"]["policy"] == "strict"
    # Server defaults never reach the page; the block is not VDOM content.
    assert '"default"' not in blocks[0]
    root_start = html.index("<div dj-root")
    root_end = html.index("</div>", root_start)
    assert html.index("data-djust-parameter-contracts") > root_end


@pytest.mark.django_db
def test_discovery_failure_marks_the_page_scope_invalid_without_breaking_it(monkeypatch):
    # An invalid declaration already fails the page render itself (handler
    # metadata compiles the contract); this covers a failing discovery step.
    from djust import _parameter_metadata

    def fail(view):
        raise _parameter_metadata.ContractError("owner limit")

    monkeypatch.setattr(_parameter_metadata, "parameter_contract_manifest", fail)
    _, html = _get(StrictPage)
    assert json.loads(BLOCK.findall(html)[0]) == {"view": _path(StrictPage), "contracts": False}
    assert "owner limit" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("event,params", [("choose", {"value": "7"}), ("quiet", {})])
def test_http_render_responses_carry_the_rendered_snapshot(event, params):
    request, _ = _get(StrictPage)
    status, body = _post(StrictPage, request, event, params)
    assert status == 200, body
    assert body["parameter_contract_view"] == _path(StrictPage)
    assert (
        expand_parameter_manifest(body["parameter_contracts"])[0]["handlers"]["choose"]["policy"]
        == "strict"
    )


@pytest.mark.django_db
def test_discovery_failure_withholds_the_http_dom_update():
    request, _ = _get(StrictPage)
    status, body = _post(BrokenPage, request, "choose", {"value": "7"})
    assert status == 500
    assert body == {"type": "error", "error": "Render parameter contracts unavailable."}
