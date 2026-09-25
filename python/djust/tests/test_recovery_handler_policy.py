"""ADR-036 owner decision R1: dj-auto-recover handlers run under legacy policy.

A handler that a literal ``dj-auto-recover`` in the view's own template targets
receives the ``_form_values`` / ``_data_attrs`` envelope, which no strict
signature can declare. Dispatch therefore resolves it to the legacy policy even
in a strict project or when it is declared strict, and the startup check warns
about an explicit strict declaration (V019). The template is server-owned, so
a client cannot claim this downgrade for any other handler.
"""

import pytest

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler
from djust.validation import get_handler_parameter_policy, validate_handler_params

CALLS: list = []
ENVELOPE = {"_form_values": {"title": "T"}, "_data_attrs": {"canvas-id": "main"}}


class RecoveryView(LiveView):
    template = (
        '<div dj-root><div dj-auto-recover="restore_state" data-canvas-id="main">'
        '<input name="title"></div><button dj-click="pick">x</button></div>'
    )

    def mount(self, request, **kwargs):
        self.restored = None

    @event_handler
    def restore_state(self, **kwargs):
        CALLS.append(("restore_state", kwargs))

    @event_handler
    def pick(self, item_id: int):
        CALLS.append(("pick", item_id))


class DeclaredStrictRecoveryView(RecoveryView):
    @event_handler(parameter_policy="strict")
    def restore_state(self, **kwargs):
        CALLS.append(("restore_state", kwargs))


@pytest.fixture(autouse=True)
def _reset():
    old = config.get("event_parameter_policy", "legacy")
    CALLS.clear()
    yield
    config.set("event_parameter_policy", old)
    CALLS.clear()


def _dispatch(view_class, event, params):
    from asgiref.sync import async_to_sync
    from djust.tests.test_runtime_child_routing_1892 import _make_runtime_with_view

    view = view_class()
    view.mount(None)
    runtime, transport = _make_runtime_with_view(view)
    async_to_sync(runtime.dispatch_event)({"type": "event", "event": event, "params": params})
    return transport


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [RecoveryView, DeclaredStrictRecoveryView])
def test_recovery_still_works_in_a_strict_project(view_class):
    config.set("event_parameter_policy", "strict")
    transport = _dispatch(view_class, "restore_state", dict(ENVELOPE))
    assert CALLS == [("restore_state", ENVELOPE)], transport.sent
    view = view_class()
    assert get_handler_parameter_policy(view.restore_state) == "legacy"


@pytest.mark.django_db
def test_other_handlers_of_the_view_stay_strict():
    config.set("event_parameter_policy", "strict")
    view = RecoveryView()
    assert get_handler_parameter_policy(view.pick) == "strict"
    assert not validate_handler_params(view.pick, {"item_id": "7x"}, "pick")["valid"]
    _dispatch(RecoveryView, "pick", {"item_id": "7"})
    assert CALLS == [("pick", 7)]


@pytest.mark.django_db
def test_the_public_manifest_advertises_recovery_handlers_as_legacy():
    from djust._parameter_metadata import parameter_contract_manifest

    config.set("event_parameter_policy", "strict")
    view = RecoveryView()
    handlers = parameter_contract_manifest(view)["owners"][0]["handlers"]
    assert handlers["restore_state"] == {"policy": "legacy"}
    assert handlers["pick"]["policy"] == "strict"


def _messages():
    from djust.checks.parameters import check_event_parameter_contracts

    return [
        (m.id, m.level)
        for m in check_event_parameter_contracts(None)
        if __name__ in m.msg and "restore_state" in m.msg
    ]


def test_explicit_strict_recovery_handler_is_a_startup_warning():
    assert _messages() == [("djust.V019", 30)]


def test_strict_project_reports_nothing_else_for_recovery_handlers():
    config.set("event_parameter_policy", "strict")
    # The unannotated **kwargs recovery handler would be a V016 error if it
    # were strict; it is not, and only the explicit declaration warns.
    assert _messages() == [("djust.V019", 30)]


@pytest.mark.django_db
def test_legacy_project_is_unchanged():
    transport = _dispatch(RecoveryView, "restore_state", dict(ENVELOPE))
    assert CALLS == [("restore_state", ENVELOPE)], transport.sent
    assert get_handler_parameter_policy(RecoveryView().pick) == "legacy"
