"""A tenant bound in one test must not reach the next one (`tests/tenant_isolation.py`).

The failure this pins: a sync test called ``run_pre_mount_auth`` for a view
whose tenant has id 42 and never reset the binding. The ContextVar stayed set
in the worker's main context, and a later
``tests/unit/test_server_push.py::TestPushToView::test_push_state`` on the same
xdist worker saw ``'tenant_scope': 'tenant:42'`` in its group message.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import RequestFactory

from djust import LiveView
from djust.auth.core import run_pre_mount_auth
from djust.push import push_to_view
from djust.tenants.middleware import get_current_tenant, set_current_tenant
from tests.tenant_isolation import isolated_current_tenant

_MODULE = "djust.tenants.middleware"


class _FakeTenant:
    id = 42


class _TenantView(LiveView):
    login_required = False
    template = "<div dj-id='0'>x</div>"

    def _ensure_tenant(self, request):
        self._tenant = _FakeTenant()


class _AuthedUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    pk = 1


def _bind_tenant_42_the_leaking_way():
    """The original leaker's shape: the bare helper, no scope around it."""
    request = RequestFactory().get("/")
    request.user = _AuthedUser()
    assert run_pre_mount_auth(_TenantView(), request) is None
    assert get_current_tenant() is not None, "precondition: the helper bound a tenant"


def _push_message():
    with patch("djust.push.get_channel_layer") as get_layer:
        layer = MagicMock()
        layer.group_send = AsyncMock()
        get_layer.return_value = layer
        push_to_view("myapp.views.DashboardView", state={"count": 42})
    return layer.group_send.call_args[0][1]


def test_a_tenant_bound_inside_the_block_is_unbound_on_exit():
    with isolated_current_tenant():
        _bind_tenant_42_the_leaking_way()
        assert _push_message()["tenant_scope"] == "tenant:42"
    assert get_current_tenant() is None
    assert "tenant_scope" not in _push_message()


def test_a_tenant_bound_before_the_block_is_unbound_on_entry():
    set_current_tenant(_FakeTenant())
    with isolated_current_tenant():
        assert get_current_tenant() is None


def test_the_block_does_not_import_the_tenants_module(monkeypatch):
    """``djust.push`` reads "not imported" as a project without tenants."""
    monkeypatch.delitem(sys.modules, _MODULE)
    with isolated_current_tenant():
        assert _MODULE not in sys.modules
    assert _MODULE not in sys.modules


def test_the_root_conftest_wraps_every_test(request):
    assert "_isolate_current_tenant" in request.fixturenames


# The two tests below are the PR #3596 CI failure end to end: they run in this
# order when they share a worker. Without the root conftest's fixture the
# second one fails with an extra 'tenant_scope': 'tenant:42'.
def test_leaves_tenant_42_bound_like_the_original_leaker():
    _bind_tenant_42_the_leaking_way()


def test_the_next_test_pushes_without_a_tenant_scope():
    assert get_current_tenant() is None
    assert _push_message() == {
        "type": "server_push",
        "state": {"count": 42},
        "handler": None,
        "payload": None,
        "group": "djust_view_myapp_views_DashboardView",
        "sender_channel": None,
    }
