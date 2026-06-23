"""Regression tests for multi-tenant isolation on the live (WS/SSE) path.

Finding #6 (CWE-862 / CWE-636): the current tenant was stored in
``threading.local()`` and only ever set by ``TenantMiddleware`` on the HTTP
path. On the WebSocket/SSE path ``get_current_tenant()`` was therefore ``None``
during every event handler, and the two tenant-aware managers failed OPEN
(``TenantQuerySet._filter_by_tenant`` returned ``self`` unconditionally and
never honored STRICT_MODE), disclosing all tenants' rows. ``TenantQuerySet``
also infinite-recursed whenever a tenant *was* set (``_chain`` → ``filter`` →
``_chain`` → ...).

The fix:
  A. tenant storage is a ``contextvars.ContextVar`` (per async-task), not
     ``threading.local()`` (shared across connections on the sync_to_async
     executor thread);
  B. the live path binds the tenant via ``tenant_context()`` around mount and
     every event dispatch;
  C. both managers scope the BASE queryset once (so ``.all()`` is scoped too),
     never recurse, and fail CLOSED (``.none()``) under STRICT_MODE (default).

These tests build real DB tables for a tenant FK model under each manager
variant and assert the no-tenant / tenant-bound / recursion / context-isolation
behaviors directly.
"""

import asyncio

import pytest
from django.db import connection, models
from django.test import override_settings
from django.test.utils import isolate_apps

from djust.tenants.managers import TenantManager, TenantQuerySet
from djust.tenants.middleware import (
    _current_tenant,
    get_current_tenant,
    set_current_tenant,
    tenant_context,
)
from djust.tenants.resolvers import TenantInfo

pytestmark = pytest.mark.tenants

# DB-touching classes use ``transaction=True`` because the fixture builds real
# tables via the SQLite schema editor, which cannot run inside the atomic block
# that the default ``django_db`` marker wraps each test in.
_db = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Fixtures: build real tables for a Tenant model + two tenant-scoped models
# (one per manager variant) and seed two tenants' rows.
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_models():
    """Create real DB tables for a Tenant + two scoped models, seeded.

    Yields a dict with the model classes and the two seeded tenants.
    Tables are torn down on exit.
    """
    # The tenant FK is modeled as a plain integer ``org_id`` (the tenant's pk)
    # rather than a Django ForeignKey so the SQLite schema editor can create
    # the tables inside the test transaction (SQLite refuses schema edits while
    # FK constraint checks are on). The managers only need a field they can
    # filter on (``tenant_field``) — they never traverse the relation — so this
    # exercises the exact scoping path. ``tenant.raw`` carries the int pk.
    ORG_A_ID = 101
    ORG_B_ID = 202

    with isolate_apps("djust_tests"):

        class WidgetMgr(models.Model):
            """Tenant-scoped via TenantManager."""

            org_id = models.IntegerField()
            label = models.CharField(max_length=50)

            objects = TenantManager(tenant_field="org_id")

            class Meta:
                app_label = "djust_tests"

        class WidgetQs(models.Model):
            """Tenant-scoped via TenantQuerySet.as_manager()."""

            org_id = models.IntegerField()
            label = models.CharField(max_length=50)

            objects = TenantQuerySet.as_manager(tenant_field="org_id")

            class Meta:
                app_label = "djust_tests"

        with connection.schema_editor() as se:
            se.create_model(WidgetMgr)
            se.create_model(WidgetQs)

        try:
            for Model in (WidgetMgr, WidgetQs):
                # bypass the tenant filter for seeding via the base manager
                Model._base_manager.create(org_id=ORG_A_ID, label="a1")
                Model._base_manager.create(org_id=ORG_A_ID, label="a2")
                Model._base_manager.create(org_id=ORG_B_ID, label="b1")

            tenant_a = TenantInfo(tenant_id=str(ORG_A_ID), raw=ORG_A_ID)
            tenant_b = TenantInfo(tenant_id=str(ORG_B_ID), raw=ORG_B_ID)

            yield {
                "WidgetMgr": WidgetMgr,
                "WidgetQs": WidgetQs,
                "tenant_a": tenant_a,
                "tenant_b": tenant_b,
            }
        finally:
            with connection.schema_editor() as se:
                se.delete_model(WidgetQs)
                se.delete_model(WidgetMgr)


@pytest.fixture(autouse=True)
def _clear_tenant():
    """Ensure no tenant leaks in/out of a test."""
    set_current_tenant(None)
    yield
    set_current_tenant(None)


def _both_managers(models_):
    return [("TenantManager", models_["WidgetMgr"]), ("TenantQuerySet", models_["WidgetQs"])]


# ---------------------------------------------------------------------------
# C. Fail-closed when no tenant is bound (the live-path disclosure bug)
# ---------------------------------------------------------------------------


@_db
class TestFailClosedNoTenant:
    def test_no_tenant_strict_mode_returns_none_for_all_variants(self, tenant_models):
        """With NO tenant bound, every manager variant returns ZERO rows under
        STRICT_MODE (the default) — never all tenants' rows."""
        assert get_current_tenant() is None
        for name, Model in _both_managers(tenant_models):
            assert Model.objects.all().count() == 0, f"{name} disclosed rows with no tenant"
            assert list(Model.objects.all()) == [], name

    @override_settings(DJUST_TENANTS={"STRICT_MODE": False})
    def test_no_tenant_lax_mode_returns_all_rows(self, tenant_models):
        """STRICT_MODE=False is the explicit, dangerous opt-out: unfiltered."""
        assert get_current_tenant() is None
        for name, Model in _both_managers(tenant_models):
            # 3 seeded rows total (2 for org_a + 1 for org_b)
            assert Model.objects.all().count() == 3, name

    @override_settings(DJUST_TENANTS={})
    def test_strict_mode_defaults_to_true(self, tenant_models):
        """STRICT_MODE absent → fail-closed (the safe default)."""
        assert get_current_tenant() is None
        for name, Model in _both_managers(tenant_models):
            assert Model.objects.all().count() == 0, name


# ---------------------------------------------------------------------------
# C. Tenant-bound scoping (.all() and .filter() both scoped)
# ---------------------------------------------------------------------------


@_db
class TestTenantScoping:
    def test_all_is_scoped_to_bound_tenant(self, tenant_models):
        """``Model.objects.all()`` returns only the bound tenant's rows — for
        BOTH variants. (TenantQuerySet.all() was unfiltered before the fix.)"""
        with tenant_context(tenant_models["tenant_a"]):
            for name, Model in _both_managers(tenant_models):
                labels = sorted(Model.objects.all().values_list("label", flat=True))
                assert labels == ["a1", "a2"], f"{name} all() not scoped: {labels}"

        with tenant_context(tenant_models["tenant_b"]):
            for name, Model in _both_managers(tenant_models):
                labels = sorted(Model.objects.all().values_list("label", flat=True))
                assert labels == ["b1"], f"{name} all() not scoped: {labels}"

    def test_filter_narrows_within_tenant_scope(self, tenant_models):
        """A chained ``.filter()`` narrows within the tenant scope and never
        crosses tenants (and never recurses)."""
        with tenant_context(tenant_models["tenant_a"]):
            for name, Model in _both_managers(tenant_models):
                # label a1 belongs to tenant_a → visible
                assert Model.objects.filter(label="a1").count() == 1, name
                # label b1 belongs to tenant_b → invisible even though it exists
                assert Model.objects.filter(label="b1").count() == 0, name

    def test_chaining_does_not_recurse_when_tenant_set(self, tenant_models):
        """The pre-fix TenantQuerySet RecursionError'd on any chain with a tenant
        bound. Exercise a deep chain to lock the regression."""
        with tenant_context(tenant_models["tenant_a"]):
            qs = (
                tenant_models["WidgetQs"]
                .objects.all()
                .filter(label__startswith="a")
                .order_by("label")
                .exclude(label="nope")
            )
            # Force evaluation — would raise RecursionError pre-fix.
            assert [w.label for w in qs] == ["a1", "a2"]

    def test_unscoped_bypasses_filter_for_both_variants(self, tenant_models):
        """`unscoped(reason=...)` is the documented cross-tenant escape hatch
        (migration note + S006 hint) — it must exist and work on BOTH the
        TenantManager and the TenantQuerySet.as_manager() variants (#157 review
        🟡: the queryset variant previously had no `unscoped`)."""
        # No tenant bound + strict mode would normally fail closed (0 rows).
        assert get_current_tenant() is None
        for name, Model in _both_managers(tenant_models):
            assert Model.objects.unscoped(reason="admin report").count() == 3, (
                f"{name}.unscoped() did not bypass the tenant filter (no tenant bound)"
            )
        # Also bypasses while a tenant IS bound.
        with tenant_context(tenant_models["tenant_a"]):
            for name, Model in _both_managers(tenant_models):
                assert Model.objects.unscoped(reason="x").count() == 3, (
                    f"{name}.unscoped() did not bypass while a tenant was bound"
                )


# ---------------------------------------------------------------------------
# A. ContextVar isolation across interleaved async contexts
# ---------------------------------------------------------------------------


class TestContextVarIsolation:
    def test_two_interleaved_contexts_do_not_clobber(self):
        """Two concurrent async tasks each bind their own tenant; neither sees
        the other's. With threading.local on a shared executor thread this
        clobbers; with ContextVar each task is isolated."""

        async def worker(tenant_id, hold):
            with tenant_context(TenantInfo(tenant_id=tenant_id)):
                # Yield control so the other worker runs while we "hold" the
                # context — proving the value survives interleaving.
                await asyncio.sleep(hold)
                cur = get_current_tenant()
                return cur.id if cur else None

        async def run():
            # task A binds "A" and sleeps longer; task B binds "B" and finishes
            # first. If contexts clobbered, A would observe "B" on resume.
            return await asyncio.gather(worker("A", 0.02), worker("B", 0.005))

        results = asyncio.run(run())
        assert results == ["A", "B"]

    def test_set_outside_does_not_leak_into_context_block_after_reset(self):
        """tenant_context restores the prior value on exit (reset semantics)."""
        set_current_tenant(TenantInfo(tenant_id="outer"))
        with tenant_context(TenantInfo(tenant_id="inner")):
            assert get_current_tenant().id == "inner"
        assert get_current_tenant().id == "outer"

    def test_uses_contextvar_not_threading_local(self):
        """Pin the storage mechanism: the module-level store is a ContextVar."""
        from contextvars import ContextVar

        assert isinstance(_current_tenant, ContextVar)


# ---------------------------------------------------------------------------
# B. Live-path context set/clear (the WS/SSE wiring contract)
# ---------------------------------------------------------------------------


class TestTenantContextHelper:
    def test_context_visible_during_block_and_cleared_after(self):
        """The exact contract the WS/SSE handlers rely on: a tenant bound for
        the dispatch is visible to get_current_tenant() and cleared after."""
        tenant = TenantInfo(tenant_id="t-live")
        assert get_current_tenant() is None
        with tenant_context(tenant):
            assert get_current_tenant() is tenant
        assert get_current_tenant() is None

    def test_context_cleared_even_on_exception(self):
        """A handler raising mid-dispatch must still clear the tenant (try/finally)."""
        with pytest.raises(ValueError):
            with tenant_context(TenantInfo(tenant_id="boom")):
                raise ValueError("handler blew up")
        assert get_current_tenant() is None

    def test_none_tenant_context_is_a_noop_binding(self):
        """Binding None (no tenant resolved) is valid and stays fail-closed."""
        with tenant_context(None):
            assert get_current_tenant() is None


# ---------------------------------------------------------------------------
# D. System check S006 — STRICT_MODE explicitly False
# ---------------------------------------------------------------------------


class TestS006StrictModeCheck:
    """S006 warns when DJUST_TENANTS['STRICT_MODE'] is explicitly False."""

    @staticmethod
    def _run():
        from djust.checks.configuration import _check_tenant_strict_mode_disabled

        errors = []
        _check_tenant_strict_mode_disabled(errors)
        return [e for e in errors if e.id == "djust.S006"]

    @override_settings(DJUST_TENANTS={"STRICT_MODE": False})
    def test_fires_when_strict_mode_false(self):
        warnings = self._run()
        assert len(warnings) == 1
        assert "fail-OPEN" in warnings[0].msg

    @override_settings(DJUST_TENANTS={"STRICT_MODE": True})
    def test_silent_when_strict_mode_true(self):
        assert self._run() == []

    @override_settings(DJUST_TENANTS={})
    def test_silent_when_strict_mode_absent_default_safe(self):
        """Absence → fail-closed default in effect; no warning."""
        assert self._run() == []

    @override_settings(DJUST_TENANTS={"STRICT_MODE": False, "suppress_checks": []})
    def test_suppressible(self):
        from django.test import override_settings as _os

        with _os(DJUST_CONFIG={"suppress_checks": ["S006"]}):
            assert self._run() == []
