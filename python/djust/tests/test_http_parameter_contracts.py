"""ADR-036 P2: HTTP delivery of owner-addressed parameter contracts.

The initial page carries its root mount's contracts outside dj-root; HTTP
fallback render responses carry the rendered tree's snapshot, with explicit
clears for a client whose scope is strict. All-legacy pages are unchanged.
"""

import json
import re

import pytest

from djust import LiveView
from djust.decorators import event_handler

BLOCK = re.compile(
    r'<script type="application/json" data-djust-parameter-contracts>(.*?)</script>', re.S
)


class StrictPage(LiveView):
    template = "<div dj-root><span>{{ n }}</span></div>"

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler(parameter_policy="strict")
    def choose(self, value: int = 5):
        self.n = value

    @event_handler(parameter_policy="strict")
    def quiet(self):
        self._skip_render = True


class LegacyPage(StrictPage):
    template = "<div dj-root><span>{{ n }}</span></div>"

    @event_handler(parameter_policy="legacy")
    def choose(self, value=5, **kwargs):
        self.n = int(value)

    @event_handler(parameter_policy="legacy")
    def quiet(self, **kwargs):
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
    root = payload["contracts"]["owners"][0]
    assert (root["view_id"], root["component_id"]) == (None, None)
    assert root["handlers"]["choose"]["policy"] == "strict"
    # Server defaults never reach the page; the block is not VDOM content.
    assert '"default"' not in blocks[0]
    root_start = html.index("<div dj-root")
    root_end = html.index("</div>", root_start)
    assert html.index("data-djust-parameter-contracts") > root_end


@pytest.mark.django_db
def test_all_legacy_page_is_unchanged():
    _, html = _get(LegacyPage)
    assert "djust-parameter-contracts" not in html


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
    assert body["parameter_contracts"]["owners"][0]["handlers"]["choose"]["policy"] == "strict"


@pytest.mark.django_db
@pytest.mark.parametrize("event,params", [("choose", {"value": "7"}), ("quiet", {})])
@pytest.mark.parametrize("header", [False, True])
def test_legacy_responses_omit_contracts_unless_the_client_scope_is_strict(event, params, header):
    request, _ = _get(LegacyPage)
    status, body = _post(LegacyPage, request, event, params, header=header)
    assert status == 200, body
    if header:
        assert body["parameter_contracts"] is None
        assert body["parameter_contract_view"] == _path(LegacyPage)
    else:
        assert "parameter_contracts" not in body and "parameter_contract_view" not in body


@pytest.mark.django_db
def test_discovery_failure_withholds_the_http_dom_update():
    request, _ = _get(StrictPage)
    status, body = _post(BrokenPage, request, "choose", {"value": "7"})
    assert status == 500
    assert body == {"type": "error", "error": "Render parameter contracts unavailable."}
