"""#2973 part 1: tenant-scoped presence.

- The global presence registry understands the ``tenant_redis`` /
  ``tenant_memory`` values ``djust.tenants`` documents (``tenant_redis`` used
  to become the per-process memory backend, silently).
- The tenant prefix on the presence key no longer depends on base order:
  ``PresenceMixin`` listed before ``TenantMixin`` used to drop it.
- ``djust.C019`` warns on an unknown ``PRESENCE_BACKEND`` value.

The state-key prefix (part 2) is 1.3 work and is not covered here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from djust.backends.memory import InMemoryPresenceBackend
from djust.backends.registry import _create_presence_backend
from djust.presence import PresenceMixin, tenant_scoped_presence_key
from djust.tenants.mixin import TenantMixin
from djust.tenants.resolvers import TenantInfo


# --- registry -----------------------------------------------------------------


@pytest.mark.parametrize("value", ["redis", "tenant_redis"])
def test_redis_values_select_the_redis_backend(value):
    with patch("djust.backends.redis.RedisPresenceBackend") as redis_cls:
        backend = _create_presence_backend(value, {"PRESENCE_REDIS_URL": "redis://x:6379/1"})
    assert backend is redis_cls.return_value
    redis_cls.assert_called_once_with(redis_url="redis://x:6379/1", key_prefix="djust:presence")


@pytest.mark.parametrize("value", ["memory", "tenant_memory"])
def test_memory_values_select_the_memory_backend_quietly(value, caplog):
    backend = _create_presence_backend(value, {})
    assert isinstance(backend, InMemoryPresenceBackend)
    assert "Unknown PRESENCE_BACKEND" not in caplog.text


def test_unknown_value_falls_back_to_memory_and_says_so(caplog):
    with caplog.at_level("WARNING", logger="djust.backends.registry"):
        backend = _create_presence_backend("tenant_redsi", {})
    assert isinstance(backend, InMemoryPresenceBackend)
    assert "Unknown PRESENCE_BACKEND 'tenant_redsi'" in caplog.text


# --- presence key, both base orders ---------------------------------------------


class _Base:
    def __init__(self, **kwargs):
        pass


class TenantFirst(TenantMixin, PresenceMixin, _Base):
    presence_key = "doc:{doc_id}"


class PresenceFirst(PresenceMixin, TenantMixin, _Base):
    presence_key = "doc:{doc_id}"


class PresenceOnly(PresenceMixin, _Base):
    presence_key = "doc:{doc_id}"


@pytest.mark.parametrize("cls", [TenantFirst, PresenceFirst])
def test_tenant_prefix_applies_in_either_base_order(cls):
    view = cls()
    view.doc_id = 7
    view._tenant = TenantInfo(tenant_id="acme")
    assert view.get_presence_key() == "tenant:acme:doc:7"


@pytest.mark.parametrize("cls", [TenantFirst, PresenceFirst])
def test_default_presence_key_is_scoped_too(cls):
    class Default(cls):
        presence_key = None

    view = Default()
    view._tenant = TenantInfo(tenant_id="acme")
    assert view.get_presence_key() == "tenant:acme:%s.Default" % Default.__module__


@pytest.mark.parametrize("cls", [TenantFirst, PresenceFirst])
def test_no_tenant_means_no_prefix(cls):
    view = cls()
    view.doc_id = 7
    view._tenant = None
    assert view.get_presence_key() == "doc:7"


def test_two_tenants_get_distinct_groups_with_presence_first():
    a, b = PresenceFirst(), PresenceFirst()
    a.doc_id = b.doc_id = 1
    a._tenant = TenantInfo(tenant_id="acme")
    b._tenant = TenantInfo(tenant_id="globex")
    assert a.get_presence_key() != b.get_presence_key()


def test_presence_without_tenant_mixin_is_unchanged():
    view = PresenceOnly()
    view.doc_id = 3
    view._tenant = TenantInfo(tenant_id="acme")  # not a TenantMixin: ignored
    assert view.get_presence_key() == "doc:3"


def test_helper_is_idempotent():
    view = TenantFirst()
    view._tenant = TenantInfo(tenant_id="acme")
    once = tenant_scoped_presence_key(view, "room")
    assert tenant_scoped_presence_key(view, once) == once == "tenant:acme:room"


# --- C019 -----------------------------------------------------------------------


def _c019(settings_dict):
    from djust.checks.configuration import _check_presence_backend

    errors: list = []
    with patch("djust.config.get_djust_config", return_value=settings_dict):
        _check_presence_backend(errors)
    return [e for e in errors if e.id == "djust.C019"]


@pytest.mark.parametrize("value", [None, "memory", "redis", "tenant_memory", "tenant_redis"])
def test_c019_silent_for_known_values(value):
    cfg = {} if value is None else {"PRESENCE_BACKEND": value}
    assert _c019(cfg) == []


def test_c019_warns_for_unknown_value():
    found = _c019({"PRESENCE_BACKEND": "rediss"})
    assert len(found) == 1
    assert "'rediss'" in found[0].msg
    assert "tenant_redis" in found[0].hint


def test_c019_can_be_suppressed():
    from django.test import override_settings

    with override_settings(DJUST_CONFIG={"suppress_checks": ["C019"]}):
        assert _c019({"PRESENCE_BACKEND": "rediss"}) == []
