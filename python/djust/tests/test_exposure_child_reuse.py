"""Real live_render paths must authorize existing children on each request."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.template import Context, Template, TemplateSyntaxError

from djust import LiveView


class ReusableChild(LiveView):
    sticky = True
    sticky_id = "guarded"
    template = "<div>PERMITTED_CHILD_CONTENT</div>"

    def mount(self, request, **kwargs):
        self._render_calls = 0
        self._auth_calls = 0

    def check_permissions(self, request):
        if request.fail_view_check:
            raise RuntimeError("broken predicate")
        return request.view_allowed

    def get_object(self):
        if self.request.fail_object_check:
            raise RuntimeError("broken object lookup")
        self._auth_calls += 1
        return SimpleNamespace(allowed=self.request.object_allowed)

    def has_object_permission(self, request, obj):
        return obj.allowed

    def get_context_data(self, **kwargs):
        self._render_calls += 1
        return super().get_context_data(**kwargs)


def make_request(rf, **changes):
    request = rf.get("/page/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore("reuse-session-key-not-loaded")
    request.view_allowed = True
    request.object_allowed = True
    request.fail_view_check = False
    request.fail_object_check = False
    for name, value in changes.items():
        setattr(request, name, value)
    return request


def render(parent, request):
    parent.request = request
    return Template(
        "{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_reuse.ReusableChild" sticky=True %}'
    ).render(Context({"view": parent, "request": request}))


@pytest.fixture(autouse=True, params=["legacy", "explicit"])
def allow_fixture_module(settings, monkeypatch, request):
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = ["djust.tests.test_exposure_child_reuse"]
    if request.param == "explicit":
        # Only construction is bypassed: actual template, registry and auth run.
        # Use an explicit root and stable middleware identity for preservation.
        # No fields persist, so no session data is read or written.
        monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
        monkeypatch.setattr(LiveView, "exposure_policy", "explicit")


@pytest.mark.parametrize("reuse", ["registered", "preserved"])
@pytest.mark.parametrize(
    "revoked",
    [
        {"view_allowed": False},
        {"object_allowed": False},
        {"fail_view_check": True},
        {"fail_object_check": True},
    ],
)
def test_revoked_child_is_not_rendered_or_reattached(rf, reuse, revoked):
    parent = LiveView()
    assert "PERMITTED_CHILD_CONTENT" in render(parent, make_request(rf))
    child = parent._get_child_view("guarded")
    assert child._render_calls == 1
    if reuse == "preserved":
        parent = LiveView()
        parent._ws_consumer = SimpleNamespace(_sticky_preserved={"guarded": child})
    with pytest.raises((PermissionDenied, TemplateSyntaxError)) as denied:
        render(parent, make_request(rf, **revoked))
    assert "broken predicate" not in str(denied.value)
    assert "broken object lookup" not in str(denied.value)
    assert child._render_calls == 1
    if reuse == "preserved":
        assert parent._get_all_child_views() == {}
        assert not getattr(parent._ws_consumer, "_sticky_auto_reattached", set())


@pytest.mark.parametrize("reuse", ["registered", "preserved"])
def test_allowed_reuse_keeps_instance_but_rechecks_current_object(rf, reuse):
    parent = LiveView()
    render(parent, make_request(rf))
    child = parent._get_child_view("guarded")
    if reuse == "preserved":
        parent = LiveView()
        parent._ws_consumer = SimpleNamespace(_sticky_preserved={"guarded": child})
    current = make_request(rf)
    html = render(parent, current)
    assert parent._get_child_view("guarded") is child
    assert child.request is current
    assert child._auth_calls == 2
    if reuse == "preserved":
        assert 'dj-sticky-slot="guarded"' in html
        assert child._render_calls == 1
    else:
        assert "PERMITTED_CHILD_CONTENT" in html
        assert child._render_calls == 2


@pytest.mark.parametrize("reuse", ["registered", "preserved"])
def test_logout_denies_existing_authenticated_child(rf, monkeypatch, reuse):
    monkeypatch.setattr(ReusableChild, "login_required", True)
    parent = LiveView()
    logged_in = make_request(rf)
    logged_in.user = SimpleNamespace(is_authenticated=True, pk=1)
    render(parent, logged_in)
    child = parent._get_child_view("guarded")
    if reuse == "preserved":
        parent = LiveView()
        parent._ws_consumer = SimpleNamespace(_sticky_preserved={"guarded": child})
    with pytest.raises((PermissionDenied, TemplateSyntaxError)):
        render(parent, make_request(rf))
    assert child._render_calls == 1
