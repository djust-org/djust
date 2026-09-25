"""#3079: ``tenant_scoped_presence_key`` must not read a half-imported module.

The function used to peek at ``sys.modules["djust.tenants.mixin"]`` and read
``.TenantMixin`` straight off it. While another thread is still executing that
module for the first time, ``sys.modules`` already holds the partially
initialised module, so the attribute read raised::

    AttributeError: partially initialized module 'djust.tenants.mixin' has no
    attribute 'TenantMixin'

It was seen under load: the HTTP worker threads and the channels sync thread
touching presence for the first time together.

The race is made deterministic with a slow-import shim: a meta-path finder
whose loader parks inside ``exec_module`` until the test releases it, so
thread A holds a partial ``djust.tenants.mixin`` in ``sys.modules`` for as long
as the test needs while thread B asks for a presence key.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import threading

import pytest

import djust.tenants
import djust.tenants.mixin as _real_mixin_module
from djust.presence import PresenceMixin, tenant_scoped_presence_key
from djust.tenants.resolvers import TenantInfo

_NAME = "djust.tenants.mixin"


class _ParkingLoader(importlib.abc.Loader):
    """Wraps the real loader; parks before running the module body."""

    def __init__(self, real, started: threading.Event, release: threading.Event):
        self._real = real
        self._started = started
        self._release = release

    def create_module(self, spec):
        return self._real.create_module(spec)

    def exec_module(self, module):
        # The module is already in sys.modules here, with no attributes yet:
        # exactly the state another thread sees during a slow first import.
        self._started.set()
        assert self._release.wait(10), "test never released the parked import"
        self._real.exec_module(module)


class _SlowImportFinder(importlib.abc.MetaPathFinder):
    def __init__(self, started: threading.Event, release: threading.Event):
        self._started = started
        self._release = release

    def find_spec(self, fullname, path, target=None):
        if fullname != _NAME:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None:
            return None
        spec.loader = _ParkingLoader(spec.loader, self._started, self._release)
        return spec


@pytest.fixture
def parked_tenant_import():
    """Re-import ``djust.tenants.mixin`` on a thread that parks mid-import.

    Yields ``release`` (an Event); the import finishes once it is set. The
    original module object is put back afterwards so classes other tests
    imported (``TenantMixin``) stay the ones ``isinstance`` checks see.
    """
    started, release = threading.Event(), threading.Event()
    finder = _SlowImportFinder(started, release)
    errors: list[BaseException] = []

    def importer():
        try:
            importlib.import_module(_NAME)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    sys.modules.pop(_NAME, None)
    sys.meta_path.insert(0, finder)
    thread_a = threading.Thread(target=importer, name="slow-importer")
    try:
        thread_a.start()
        assert started.wait(10), "the slow import never started"
        assert _NAME in sys.modules
        assert not hasattr(sys.modules[_NAME], "TenantMixin")  # really partial
        yield release
    finally:
        release.set()
        thread_a.join(10)
        sys.meta_path.remove(finder)
        sys.modules[_NAME] = _real_mixin_module
        djust.tenants.mixin = _real_mixin_module
    assert not errors, errors


def test_presence_key_waits_for_a_half_imported_tenant_module(parked_tenant_import):
    release = parked_tenant_import
    result: dict = {}

    def presence_lookup():
        try:
            result["key"] = tenant_scoped_presence_key(object(), "room:1")
        except BaseException as exc:  # noqa: BLE001 - asserted below
            result["error"] = exc

    thread_b = threading.Thread(target=presence_lookup, name="presence-lookup")
    thread_b.start()
    # With the fix, thread B is blocked on the module's import lock; without
    # it, B has already failed with AttributeError by now.
    thread_b.join(0.3)
    release.set()
    thread_b.join(10)
    assert not thread_b.is_alive()
    assert "error" not in result, f"presence lookup raised: {result.get('error')!r}"
    assert result["key"] == "room:1"


def test_tenant_view_is_still_scoped():
    class _Base:
        def __init__(self, **kwargs):
            pass

    class View(_real_mixin_module.TenantMixin, PresenceMixin, _Base):
        presence_key = "room:{room}"

    view = View()
    view._tenant = TenantInfo(tenant_id="acme")
    assert tenant_scoped_presence_key(view, "room:1") == "tenant:acme:room:1"
    # Idempotent: an already-scoped key is not prefixed twice.
    assert tenant_scoped_presence_key(view, "tenant:acme:room:1") == "tenant:acme:room:1"


def test_non_tenant_view_passes_through():
    assert tenant_scoped_presence_key(object(), "room:1") == "room:1"
