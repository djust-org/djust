"""#3328 cases that outlive the legacy signed snapshot.

``test_snapshot_token_tenant_3328`` went with the legacy snapshot, but one of
its classes pins code that stays: ``snapshot_tenant_scope`` (still used by
``_lazy_containers.py``): empty for a view without ``TenantMixin``, ``None``
(fail closed) for an unresolved tenant, and the tenant id quoted. The
``legacy_render_only_keys`` cases went with that function (#4202).

Moved unchanged, except that the views no longer set ``enable_state_snapshot``,
which the scope helper never read.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust.tenants.mixin import TenantMixin

pytestmark = [pytest.mark.tenants]


class _TenantView(TenantMixin, LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root dj-id="0">[{{ secret }}]</div>'


class _PlainView(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root dj-id="0">[{{ secret }}]</div>'


class TestScopeHelper:
    def test_scope_of_a_view(self):
        from djust._tenant_state import snapshot_tenant_scope
        from djust.tenants.resolvers import TenantInfo

        assert snapshot_tenant_scope(_PlainView()) == ""
        view = _TenantView()
        assert snapshot_tenant_scope(view) is None  # unresolved: fail closed
        view.tenant = TenantInfo(tenant_id="acme")
        assert snapshot_tenant_scope(view) == "tenant:acme"
        view.tenant = TenantInfo(tenant_id="a:b")
        assert snapshot_tenant_scope(view) == "tenant:a%3Ab"
