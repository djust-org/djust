"""``LiveViewTestClient.send_event`` runs the consumer's authorization gates (#3094).

Before the fix, ``send_event`` called the handler directly, so a test that sent
an event to a ``@permission_required`` handler as an unprivileged user passed
whether the decorator was there or not. Each denial test here has a twin
without the gate, which must run the handler: that pair is what shows the test
can fail.
"""

from djust import LiveView
from djust.decorators import event_handler
from djust.decorators import permission_required as require_permission
from djust.testing import LiveViewTestClient


class _User:
    is_authenticated = True
    is_active = True

    def __init__(self, *perms):
        self.perms = set(perms)

    def has_perms(self, perms):
        return set(perms) <= self.perms

    def has_perm(self, perm, obj=None):
        return perm in self.perms


class GatedView(LiveView):
    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @require_permission("app.add_decision")
    @event_handler()
    def decide(self, **kwargs):
        self.count += 1

    @event_handler()
    def ungated(self, **kwargs):
        self.count += 1


class ObjectGatedView(LiveView):
    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self.allowed = True

    def get_object(self):
        return {"id": 1}

    def has_object_permission(self, request, obj):
        return self.allowed

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


def test_handler_permission_denies_unprivileged_user():
    client = LiveViewTestClient(GatedView, user=_User())
    client.mount()

    result = client.send_event("decide")

    assert result["success"] is False
    assert result["code"] == "permission_denied"
    assert result["error"] == "Permission denied"
    assert client.view_instance.count == 0
    assert client.get_event_history()[-1]["code"] == "permission_denied"


def test_handler_permission_allows_privileged_user():
    client = LiveViewTestClient(GatedView, user=_User("app.add_decision"))
    client.mount()

    assert client.send_event("decide")["success"] is True
    assert client.view_instance.count == 1


def test_ungated_handler_runs_for_unprivileged_user():
    client = LiveViewTestClient(GatedView, user=_User())
    client.mount()

    assert client.send_event("ungated")["success"] is True
    assert client.view_instance.count == 1


def test_gated_handler_without_request_is_denied():
    client = LiveViewTestClient(GatedView, user=_User("app.add_decision"))
    client.mount()
    client.view_instance.request = None

    assert client.send_event("decide")["error"] == "Permission denied"
    assert client.view_instance.count == 0


def test_object_permission_is_rechecked_per_event():
    client = LiveViewTestClient(ObjectGatedView, user=_User())
    client.mount()
    assert client.send_event("bump")["success"] is True

    client.view_instance.allowed = False
    result = client.send_event("bump")

    assert result["success"] is False
    assert result["error"] == "Access denied for this object."
    assert client.view_instance.count == 1


def test_object_permission_check_that_raises_fails_closed():
    class Broken(ObjectGatedView):
        def has_object_permission(self, request, obj):
            raise AttributeError("bug in the app's predicate")

    client = LiveViewTestClient(Broken, user=_User())
    client.mount()

    assert client.send_event("bump")["code"] == "permission_denied"
    assert client.view_instance.count == 0
