"""A failed contract discovery only fails closed where strict contracts can exist.

PR #3122 review: the HTTP page and render paths emitted ``contracts: false`` on
any discovery exception, so an all-legacy page lost every binding client-side
and POST returned 500. The socket path (``direct_render_contract_fields``)
keeps a legacy session's legacy frame shape; HTTP now does the same.
"""

from unittest import mock

import pytest
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler
from djust.mixins import request as request_mixin


class LegacyPage(LiveView):
    template = "<div dj-root><button dj-click='go'>Go</button></div>"

    @event_handler()
    def go(self, **kwargs):
        pass


@pytest.fixture
def project_policy():
    from djust.config import config

    previous = config.get("event_parameter_policy", "legacy")

    def set_policy(value):
        config.set("event_parameter_policy", value)

    yield set_policy
    config.set("event_parameter_policy", previous)


@pytest.fixture
def discovery_fails():
    with mock.patch(
        "djust._parameter_metadata.parameter_contract_manifest",
        side_effect=RuntimeError("declaration error"),
    ):
        yield


def test_legacy_page_stays_legacy_when_discovery_fails(project_policy, discovery_fails):
    project_policy("legacy")
    assert request_mixin._initial_parameter_contracts(LegacyPage(), "app.LegacyPage") is None


def test_strict_project_page_fails_closed_when_discovery_fails(project_policy, discovery_fails):
    project_policy("strict")
    body = request_mixin._initial_parameter_contracts(LegacyPage(), "app.LegacyPage")
    assert body is not None and '"contracts": false' in body


def test_legacy_render_keeps_its_shape_when_discovery_fails(project_policy, discovery_fails):
    project_policy("legacy")
    request = RequestFactory().post("/")
    assert request_mixin._http_parameter_contract_fields(LegacyPage(), request) == {}


def test_strict_render_withholds_the_update_when_discovery_fails(project_policy, discovery_fails):
    project_policy("strict")
    request = RequestFactory().post("/")
    assert request_mixin._http_parameter_contract_fields(LegacyPage(), request) is None


def test_a_client_that_advertised_contracts_still_fails_closed(project_policy, discovery_fails):
    project_policy("legacy")
    request = RequestFactory().post("/", HTTP_X_DJUST_PARAMETER_CONTRACTS="1")
    assert request_mixin._http_parameter_contract_fields(LegacyPage(), request) is None
