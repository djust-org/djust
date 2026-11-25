"""Direct state API boundaries while ADR-038 construction remains gated."""

import json
from types import SimpleNamespace

import pytest

from djust import LiveView
from djust.decorators import state


class ExportView(LiveView):
    exposure_policy = "explicit"
    transient_value = state("TRANSIENT_SENTINEL")
    server_value = state("SERVER_SENTINEL", persist="server")
    client_value = state(default_factory=lambda: ["visible"], client=True)
    snapshot_value = state(default_factory=lambda: {"page": 1}, persist="client", client=True)

    @property
    def unrelated_property(self):
        raise AssertionError("Do not evaluate undeclared properties")


@pytest.fixture
def view():
    # Exercise direct sinks without making the staged policy constructible.
    result = object.__new__(ExportView)
    result.public_note = "PUBLIC_SENTINEL"
    result._private_note = "PRIVATE_SENTINEL"
    result._user_private_keys = {"_private_note"}
    result._components = {"menu": SimpleNamespace(open=True, secret="COMPONENT_SENTINEL")}
    return result


@pytest.mark.parametrize("debug", [False, True])
def test_get_state_exports_only_client_grants_and_detaches_values(view, settings, debug):
    settings.DEBUG = debug
    exported = view.get_state()
    assert exported == {"client_value": ["visible"], "snapshot_value": {"page": 1}}
    assert "SENTINEL" not in json.dumps(exported)
    exported["client_value"].append("changed")
    assert view.client_value == ["visible"]
    assert "_state_server_value" not in view.__dict__
    assert "_state_transient_value" not in view.__dict__


@pytest.mark.parametrize("error_type", [RuntimeError, AttributeError])
def test_policy_descriptor_is_refused_without_evaluating_it(error_type):
    """A descriptor stated as ``exposure_policy`` is refused when its class is
    created; it is never evaluated, so its exception cannot be disclosed."""
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured) as error:

        class BrokenPolicy(LiveView):
            @property
            def exposure_policy(self):
                raise error_type("POLICY_SECRET_SENTINEL")

    assert "a value of type property" in str(error.value)
    assert "SENTINEL" not in str(error.value)


def test_broken_policy_descriptor_cannot_enable_legacy_debug_fallback():
    from djust._exposure import explicit_debug_projection, uses_legacy_exposure

    class BrokenPolicy:
        @property
        def exposure_policy(self):
            raise AttributeError("POLICY_SECRET_SENTINEL")

    view = BrokenPolicy()
    view.public_note = "UNDECLARED_SECRET_SENTINEL"
    assert not uses_legacy_exposure(view)
    assert explicit_debug_projection(view) == {"_djust_projection_error": "State unavailable"}
    # Genuinely undeclared: reads as the default policy, which is explicit, so
    # an object that never asked for the legacy export is not given it.
    assert not uses_legacy_exposure(object())
    assert not uses_legacy_exposure(None)
