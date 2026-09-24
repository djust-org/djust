"""Direct state API boundaries while ADR-038 construction remains gated."""

import json
import traceback
from types import SimpleNamespace

import pytest

from djust import LiveView
from djust._exposure import ExposureError
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


@pytest.mark.parametrize("strict", [False, True])
def test_snapshot_api_uses_only_snapshot_grants(view, strict):
    exported = view._capture_snapshot_state(strict=strict)
    assert exported == {"snapshot_value": {"page": 1}}
    exported["snapshot_value"]["page"] = 2
    assert view.snapshot_value == {"page": 1}
    assert "_state_client_value" not in view.__dict__
    assert "_state_server_value" not in view.__dict__


@pytest.mark.parametrize("method", ["get_state", "_capture_snapshot_state"])
@pytest.mark.parametrize("policy", [None, "typo", 0])
def test_unknown_policy_never_selects_legacy_export(view, method, policy):
    view.exposure_policy = policy
    with pytest.raises(ExposureError):
        getattr(view, method)()


@pytest.mark.parametrize("method", ["get_state", "_capture_snapshot_state"])
def test_export_failure_is_redacted_without_legacy_fallback(view, method):
    def secret_factory():
        raise RuntimeError("FACTORY_SECRET_SENTINEL")

    class BrokenView(LiveView):
        exposure_policy = "explicit"
        value = state(default_factory=secret_factory, persist="client", client=True)

    broken = object.__new__(BrokenView)
    broken.public_note = "FALLBACK_SENTINEL"
    with pytest.raises(ExposureError) as error:
        getattr(broken, method)()
    assert "SENTINEL" not in str(error.value)
    assert "SENTINEL" not in "".join(traceback.format_exception(error.value))
    assert error.value.__suppress_context__


@pytest.mark.parametrize("method", ["get_state", "_capture_snapshot_state"])
@pytest.mark.parametrize("bad", [object(), float("nan"), "x" * 65536])
def test_declared_values_still_require_bounded_exact_json(view, method, bad):
    view.snapshot_value = {"nested": bad}
    with pytest.raises(ExposureError):
        getattr(view, method)()


@pytest.mark.parametrize("method", ["_get_private_state", "_capture_components_snapshot"])
@pytest.mark.parametrize("policy", ["explicit", "invalid", None])
def test_legacy_private_and_component_exporters_reject_nonlegacy(view, method, policy):
    view.exposure_policy = policy
    with pytest.raises(ExposureError):
        getattr(view, method)()


@pytest.mark.parametrize("method", ["_restore_private_state", "_restore_snapshot"])
@pytest.mark.parametrize("policy", ["explicit", "invalid", None])
def test_raw_restore_is_rejected_before_reading_payload_or_assigning(view, method, policy):
    class HostilePayload(dict):
        def items(self):
            raise AssertionError("Raw restore must not inspect unverified input")

    view.exposure_policy = policy
    before = dict(view.__dict__)
    with pytest.raises(ExposureError):
        getattr(view, method)(HostilePayload(snapshot_value={"page": 9}, _private_note="changed"))
    assert view.__dict__ == before


def test_legacy_direct_api_behavior_remains_available():
    legacy = object.__new__(LiveView)
    legacy.public_value = [1]
    legacy._private_value = "private"
    legacy._user_private_keys = {"_private_value"}
    assert legacy.get_state() == {"public_value": [1]}
    assert legacy._get_private_state() == {"_private_value": "private"}
    assert legacy._capture_snapshot_state() == {"public_value": [1]}
    legacy._restore_snapshot({"public_value": [2]})
    legacy._restore_private_state({"_private_value": "restored"})
    assert legacy.public_value == [2]
    assert legacy._private_value == "restored"


def test_raw_snapshot_restore_cannot_accept_even_declared_values(view):
    with pytest.raises(ExposureError):
        view._restore_snapshot({"snapshot_value": {"page": 42}})
    assert "_state_snapshot_value" not in view.__dict__


def test_audio_private_restore_rejects_before_filtering_payload():
    from djust.audio import AudioMixin

    class AudioView(AudioMixin, LiveView):
        exposure_policy = "explicit"

    class HostilePayload(dict):
        def items(self):
            raise AssertionError("Audio restore must not inspect unverified input")

    view = object.__new__(AudioView)
    with pytest.raises(ExposureError):
        view._restore_private_state(HostilePayload(_audio_cursor=5))
    assert view.__dict__ == {}


@pytest.mark.parametrize("method", ["get_state", "_capture_snapshot_state"])
def test_inherited_grants_cannot_widen_exports(method):
    class Inherited(ExportView):
        pass

    view = object.__new__(Inherited)
    with pytest.raises(ExposureError):
        getattr(view, method)()
    assert view.__dict__ == {}


@pytest.mark.parametrize("method", ["get_state", "_capture_snapshot_state"])
@pytest.mark.parametrize("error_type", [RuntimeError, AttributeError])
def test_policy_lookup_failure_does_not_disclose_exception(method, error_type):
    class BrokenPolicy(LiveView):
        @property
        def exposure_policy(self):
            raise error_type("POLICY_SECRET_SENTINEL")

    with pytest.raises(ExposureError) as error:
        getattr(object.__new__(BrokenPolicy), method)()
    assert "SENTINEL" not in str(error.value)


def test_broken_policy_descriptor_cannot_enable_legacy_debug_fallback():
    from djust._exposure import explicit_debug_projection, uses_legacy_exposure
    from djust.observability.views import _lenient_assigns

    class BrokenPolicy:
        @property
        def exposure_policy(self):
            raise AttributeError("POLICY_SECRET_SENTINEL")

    view = BrokenPolicy()
    view.public_note = "UNDECLARED_SECRET_SENTINEL"
    assert not uses_legacy_exposure(view)
    assert explicit_debug_projection(view) == {"_djust_projection_error": "State unavailable"}
    assert "SENTINEL" not in json.dumps(_lenient_assigns(view))
    assert uses_legacy_exposure(object())  # genuinely undeclared, legacy-compatible


@pytest.mark.parametrize(
    "parent_policy,child_policy", [("legacy", "explicit"), ("explicit", "legacy")]
)
def test_sticky_restore_rejects_explicit_boundary_before_any_assignment(
    parent_policy, child_policy
):
    from djust.mixins.sticky import restore_sticky_child_state, sticky_child_session_key

    child = object.__new__(LiveView)
    child.exposure_policy = child_policy
    child.sticky_id = "menu"
    child.enable_state_snapshot = True
    parent = SimpleNamespace(exposure_policy=parent_policy, enable_state_snapshot=True)
    session = {sticky_child_session_key("/page/", "menu"): {"public_secret": "STALE_SENTINEL"}}
    before = dict(child.__dict__)
    with pytest.raises(ExposureError):
        restore_sticky_child_state(child, parent, session, "/page/")
    assert child.__dict__ == before


@pytest.mark.parametrize("async_save", [False, True])
async def test_sticky_save_does_not_reach_context_when_private_helper_is_overridden(async_save):
    from djust.mixins.sticky import save_sticky_child_state, save_sticky_child_state_sync

    class Child:
        exposure_policy = "explicit"
        sticky_id = "menu"

        def _get_private_state(self):
            return {}

        def get_context_data(self):
            raise AssertionError("Sticky exporter must not infer persistence from context")

    with pytest.raises(ExposureError):
        if async_save:
            await save_sticky_child_state(Child(), {}, "/page/")
        else:
            save_sticky_child_state_sync(Child(), {}, "/page/")
