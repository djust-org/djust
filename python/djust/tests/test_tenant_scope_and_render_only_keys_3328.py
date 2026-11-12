"""#3328 cases that outlive the legacy signed snapshot.

``test_snapshot_token_tenant_3328`` went with the legacy snapshot, but two of
its classes pin code that stays:

* ``snapshot_tenant_scope`` (still used by ``_lazy_containers.py``): empty for
  a view without ``TenantMixin``, ``None`` (fail closed) for an unresolved
  tenant, and the tenant id quoted;
* ``legacy_render_only_keys`` keeps ``tenant`` render-only whichever order
  ``ModelFormMixin`` and ``TenantMixin`` come in, because every
  ``_djust_render_only_context_keys`` hook chains through ``super()``.

Moved unchanged, except that the views no longer set ``enable_state_snapshot``,
which the scope helper never read.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust.tenants.mixin import TenantMixin

pytestmark = [pytest.mark.tenants]


class _TenantView(TenantMixin, LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-id="0">[{{ secret }}]</div>'


class _PlainView(LiveView):
    exposure_policy = "legacy"
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


class TestRenderOnlyKeysChainThroughTheMro:
    def test_model_form_mixin_before_tenant_mixin_keeps_the_tenant_key_render_only(self):
        from djust.forms import ModelFormMixin
        from djust.mixins.context import legacy_render_only_keys

        class _Edit(ModelFormMixin, TenantMixin, LiveView):
            exposure_policy = "legacy"
            context_object_name = "project"

        view = _Edit()
        view.object = None
        keys = legacy_render_only_keys(view)
        assert "tenant" in keys, keys
        assert {"object", "project"} <= keys, keys

    def test_tenant_mixin_before_model_form_mixin_still_works(self):
        from djust.forms import ModelFormMixin
        from djust.mixins.context import legacy_render_only_keys

        class _Edit(TenantMixin, ModelFormMixin, LiveView):
            exposure_policy = "legacy"
            context_object_name = "project"

        view = _Edit()
        view.object = None
        keys = legacy_render_only_keys(view)
        assert {"tenant", "object", "project"} <= keys, keys

    def test_every_render_only_hook_chains(self):
        """A hook that does not call ``super()`` hides the ones after it."""
        import ast
        import pathlib

        import djust

        pkg = pathlib.Path(djust.__file__).resolve().parent
        offenders = []
        for path in pkg.rglob("*.py"):
            rel = path.relative_to(pkg).as_posix()
            if "/tests/" in rel or rel.startswith("tests/"):
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == (
                    "_djust_render_only_context_keys"
                ):
                    if "super" not in ast.dump(node):
                        offenders.append(f"{rel}:{node.lineno}")
        assert not offenders, offenders
