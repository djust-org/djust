"""Keep a test's current tenant from leaking into the next test.

``djust.tenants.middleware._current_tenant`` is a ``ContextVar``. A sync test
runs in the worker's main context, so a test that binds a tenant without
resetting it (for example by calling ``run_pre_mount_auth`` directly, which
binds the view's tenant unscoped) leaves it bound for every later test in the
same process. ``push_to_view`` then adds that tenant's ``tenant_scope`` to its
group message, and ``tests/unit/test_server_push.py`` failed whenever such a
test ran first on the same xdist worker::

    pytest "python/djust/tests/test_mount_security_sequence_1853.py::TestRunPreMountAuthHelper::test_resolves_then_binds_tenant_in_order_on_auth_success" \\
           "tests/unit/test_server_push.py::TestPushToView::test_push_state" -p no:randomly

The root ``conftest.py`` wraps every test in :func:`isolated_current_tenant`.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator

_MODULE = "djust.tenants.middleware"


def _clear_current_tenant() -> None:
    """Unbind the current tenant, but only if the tenants module is loaded.

    Never import it here: ``djust.push`` reads "not in ``sys.modules``" as a
    project without tenants, so importing it would change what the test sees.
    """
    module = sys.modules.get(_MODULE)
    if module is not None:
        module._current_tenant.set(None)


@contextmanager
def isolated_current_tenant() -> Iterator[None]:
    """Start the block with no current tenant and leave none bound after it.

    The tenants module may be imported for the first time inside the block, so
    the exit step looks it up again instead of reusing the entry lookup.
    """
    _clear_current_tenant()
    try:
        yield
    finally:
        _clear_current_tenant()
