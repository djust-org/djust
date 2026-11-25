"""A failed contract discovery fails closed (#3122, #4205).

The HTTP page and render paths emit ``contracts: false`` (or withhold the
update) on any discovery exception: strict is the only policy, so there is no
legacy frame shape to fall back to.
"""

from unittest import mock

import pytest
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler
from djust.mixins import request as request_mixin


class Page(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><button dj-click='go'>Go</button></div>"

    @event_handler()
    def go(self, **kwargs):
        pass


@pytest.fixture
def discovery_fails():
    with mock.patch(
        "djust._parameter_metadata.parameter_contract_manifest",
        side_effect=RuntimeError("declaration error"),
    ):
        yield


def test_page_fails_closed_when_discovery_fails(discovery_fails):
    body = request_mixin._initial_parameter_contracts(Page(), "app.Page")
    assert body is not None and '"contracts": false' in body


def test_render_withholds_the_update_when_discovery_fails(discovery_fails):
    request = RequestFactory().post("/")
    assert request_mixin._http_parameter_contract_fields(Page(), request) is None


def test_a_client_that_advertised_contracts_still_fails_closed(discovery_fails):
    request = RequestFactory().post("/", HTTP_X_DJUST_PARAMETER_CONTRACTS="1")
    assert request_mixin._http_parameter_contract_fields(Page(), request) is None
