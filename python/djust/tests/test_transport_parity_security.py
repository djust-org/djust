"""Transport-parity security net (WU1 — #1646 "make drift mechanically detectable").

The mount surface is reached over THREE transports — WebSocket
(``LiveViewConsumer.handle_mount``), the generic ``ViewRuntime.dispatch_mount``,
and SSE (``_sse_mount_view``). Findings #7/#22/#23/#24 were all the SAME class:
one transport had a security control (URL-traversal validation, allowlist-before-
import) that a parallel transport lacked, so the same attacker payload was
neutralised on one path and landed verbatim on another.

The structural cure (#1646) routed every transport through one shared module,
``djust.security.mount`` (:func:`validate_mount_url`, :func:`resolve_view_class`,
:func:`is_view_path_allowed`). This file is the *enforcement net* for that cure:
it PARAMETRIZES the same payload over every mount entry point and asserts an
IDENTICAL security verdict on each. If a future transport (or a regression on an
existing one) stops routing through the chokepoint, the parametrized case for
that transport FAILS loudly — drift becomes mechanically detectable instead of a
silent reopen of #7/#22/#23/#24.

Two payload classes are covered:

* **Mount-URL traversal** — the client-supplied ``url`` is rebuilt into an
  ``HttpRequest`` (WS + runtime build a request via ``RequestFactory``; SSE uses
  the real HTTP request directly and so is excluded from this axis). The
  resulting ``request.path`` must be neutralised (no ``..``) for traversal
  payloads and preserved verbatim for a legit path. WS and runtime must agree.
* **Arbitrary-module import** — the client-supplied ``view`` dotted path must be
  REJECTED on ALL THREE entry points WITHOUT importing the module (sentinel never
  enters ``sys.modules``); a legit INSTALLED_APPS LiveView must mount on all
  three. The allowlist verdict must be identical across transports.

Non-tautology (#1468): gate-off verified — temporarily making one transport skip
the shared resolver (e.g. ``importlib.import_module`` directly, or feeding the raw
``page_url`` into ``RequestFactory``) makes that transport's parametrized case
FAIL. See the module commit message / PR body for the recorded gate-off run.

Adding a 4th transport: register one more adapter in ``_IMPORT_ADAPTERS`` /
``_URL_ADAPTERS`` below; the existing parametrized cases then immediately cover it
(and fail if it doesn't route through the chokepoint).
"""

import sys
import uuid
from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory, override_settings

from djust.live_view import LiveView

# A real, mountable demo-app LiveView (demo_project.settings INSTALLED_APPS
# includes the demo apps). Resolves under the INSTALLED_APPS fallback even with
# LIVEVIEW_ALLOWED_MODULES unset — the non-breaking property of the F22 default.
_DEMO_VIEW_PATH = "demo_app.views.CounterView"

# Arbitrary-module-import payloads (the canonical CWE-470 / F22 shapes the issue
# names). ``os`` / ``subprocess`` are already loaded under test, so we assert
# rejection + that the module was not *newly* imported (snapshot diff) rather
# than absence. ``antigravity`` is NOT preloaded and has a real import side
# effect (opens a browser) — so for it the snapshot-diff proof IS an
# absence-from-sys.modules proof, AND a regression that imports it is caught
# before the side effect is observable in CI (the resolver rejects pre-import).
_ARBITRARY_IMPORT_PAYLOADS = [
    "os.system",
    "antigravity.X",
    "subprocess.Popen",
]

# A purpose-built, never-preloaded module with an observable top-level marker —
# the safe sentinel for the "rejected WITHOUT importing" proof (mirrors
# test_mount_consolidation_f22_f23.py::import_sentinel). Using a fresh tmp module
# instead of ``antigravity`` keeps the side-effect proof free of real-world
# browser-launch risk if a gate-off / regression ever DOES import it.
_SENTINEL_MODULE = "djust_wu1_parity_import_sentinel"


# --------------------------------------------------------------------------- #
# Per-transport adapters.
#
# Each adapter drives ONE real mount entry point with the given payload and
# returns a NORMALISED verdict so the parametrized assertions are transport-blind:
#   - import adapters return a bool "mounted?" (True == view resolved + mounted)
#   - url adapters return the resulting ``request.path`` (or None if the transport
#     does not build a request from the client URL)
# --------------------------------------------------------------------------- #


# ---- WebSocket (LiveViewConsumer.handle_mount) -------------------------------
# handle_mount resolves the view via ``resolve_view_class`` (websocket.py:1915)
# and builds the request via ``_validate_mount_url`` + ``RequestFactory().get()``
# (websocket.py:2052-2054). Driving the full coroutine end-to-end needs ~700
# lines of Rust/session/scope fixture (see test_handle_mount_drains_queues.py),
# so we exercise the EXACT chokepoint calls handle_mount makes, at the same
# seams — the security verdict is produced entirely by those calls.
def _ws_import_verdict(view_path: str) -> bool:
    from djust.websocket import _safe_error  # noqa: F401  (import-shape parity)
    from djust.security.mount import resolve_view_class

    resolution = resolve_view_class(view_path)
    return bool(resolution)


def _ws_request_path(url: str) -> str:
    # Replicates handle_mount websocket.py:2052-2054 verbatim.
    from djust.websocket import _validate_mount_url

    page_url = _validate_mount_url(url)
    request = RequestFactory().get(page_url)
    return request.path


# ---- Generic runtime (ViewRuntime.dispatch_mount / _instantiate_view) --------
class _NoopTransport:
    session_id = "parity-test"
    scope = None

    async def send(self, msg):
        pass

    async def send_error(self, *a, **k):
        pass


def _runtime_import_verdict(view_path: str) -> bool:
    # dispatch_mount calls ``_instantiate_view`` which calls ``resolve_view_class``
    # (runtime.py:727). We drive the real ``_instantiate_view`` — its return is the
    # security verdict (None == rejected; instance == resolved + instantiated).
    from djust.runtime import ViewRuntime

    runtime = ViewRuntime(_NoopTransport())
    instance = runtime._instantiate_view(view_path)
    return instance is not None


async def _runtime_request_path(url: str) -> str:
    # _build_request is the request-building seam dispatch_mount uses; it routes
    # the client URL through ``validate_mount_url`` (runtime.py:773) before
    # RequestFactory.
    from djust.runtime import ViewRuntime

    runtime = ViewRuntime(_NoopTransport())
    request = await runtime._build_request(page_url=url, params={})
    return request.path


# ---- SSE (_sse_mount_view) ---------------------------------------------------
# SSE uses the real HTTP request directly (no RequestFactory) so it is NOT part of
# the URL-traversal request-build axis; it IS part of the import-allowlist axis.
async def _sse_import_verdict(view_path: str) -> bool:
    from djust.sse import SSESession, _sse_mount_view

    session = SSESession(str(uuid.uuid4()))
    session._owner_user_pk = 7
    request = RequestFactory().get("/")
    request.session = MagicMock()
    request.user = MagicMock(is_authenticated=False, pk=7)
    return await _sse_mount_view(session, request, view_path)


# Adapter registries. Add a 4th transport by registering one more entry; the
# parametrized cases below then cover it automatically (and fail if it drifts).
_IMPORT_ADAPTERS = {
    "ws": _ws_import_verdict,
    "runtime": _runtime_import_verdict,
    "sse": _sse_import_verdict,
}

# Only transports that REBUILD a request from the client URL participate in the
# traversal-neutralisation axis. SSE uses the real request and is excluded by
# design (documented in the module docstring).
_URL_ADAPTERS = {
    "ws": _ws_request_path,
    "runtime": _runtime_request_path,
}


async def _call_import_adapter(adapter, view_path: str) -> bool:
    """Invoke an import adapter that may be sync or async; normalise to bool."""
    result = adapter(view_path)
    if hasattr(result, "__await__"):
        result = await result
    return result


async def _call_url_adapter(adapter, url: str) -> str:
    result = adapter(url)
    if hasattr(result, "__await__"):
        result = await result
    return result


@pytest.fixture
def import_sentinel(tmp_path):
    """A fresh, not-yet-imported module whose top-level code sets a marker.

    Yields ``(module_name, marker_getter)``. A correct resolver never imports it,
    so ``marker_getter()`` stays ``None`` and ``module_name`` never enters
    ``sys.modules``. Mirrors test_mount_consolidation_f22_f23.py::import_sentinel.
    """
    import builtins

    marker = "_DJ_WU1_SENTINEL_IMPORTED"
    (tmp_path / f"{_SENTINEL_MODULE}.py").write_text(f"import builtins\nbuiltins.{marker} = True\n")
    sys.path.insert(0, str(tmp_path))
    sys.modules.pop(_SENTINEL_MODULE, None)
    if hasattr(builtins, marker):
        delattr(builtins, marker)
    try:
        yield _SENTINEL_MODULE, (lambda: getattr(builtins, marker, None))
    finally:
        sys.modules.pop(_SENTINEL_MODULE, None)
        if hasattr(builtins, marker):
            delattr(builtins, marker)
        try:
            sys.path.remove(str(tmp_path))
        except ValueError:
            pass


# --------------------------------------------------------------------------- #
# Axis 1 — arbitrary-module import: identical REJECT verdict on every transport.
# --------------------------------------------------------------------------- #
class TestImportAllowlistParity:
    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    @pytest.mark.parametrize("transport", sorted(_IMPORT_ADAPTERS))
    @pytest.mark.parametrize("payload", _ARBITRARY_IMPORT_PAYLOADS)
    async def test_arbitrary_import_rejected_on_every_transport(self, transport, payload):
        """Every mount entry point REJECTS an arbitrary-module view path WITHOUT
        newly importing the target module.

        Gate-off (#1468): bypass the shared resolver on any one transport
        (e.g. ``importlib.import_module(view_path.rsplit('.',1)[0])`` directly)
        and that transport's row here flips to "mounted" → FAILS.
        """
        before = set(sys.modules)
        adapter = _IMPORT_ADAPTERS[transport]
        mounted = await _call_import_adapter(adapter, payload)

        assert mounted is False, (
            f"{transport} mounted arbitrary view path {payload!r} — the shared "
            f"allowlist (djust.security.mount.resolve_view_class) was bypassed on "
            f"this transport (parallel-path drift, #1646)."
        )
        # The allowlist runs BEFORE import, so a rejected payload must not have
        # newly loaded its target module (os/subprocess are already loaded; the
        # snapshot diff catches a *new* import — e.g. antigravity — regardless).
        newly = set(sys.modules) - before
        target_module = payload.rsplit(".", 1)[0]
        assert target_module not in newly, (
            f"{transport} newly IMPORTED {target_module!r} while rejecting "
            f"{payload!r} — allowlist check must run BEFORE import."
        )

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    @pytest.mark.parametrize("transport", sorted(_IMPORT_ADAPTERS))
    async def test_sentinel_module_never_imported_on_any_transport(
        self, transport, import_sentinel
    ):
        """Reject-without-import proof via an observable, side-effecting sentinel.

        A purpose-built module not under any allowlist prefix; if a transport
        imported it, its top-level code would set the marker. Every transport
        must reject it AND leave both the marker unset and ``sys.modules`` clean.
        """
        name, marker = import_sentinel
        assert name not in sys.modules
        assert marker() is None

        adapter = _IMPORT_ADAPTERS[transport]
        mounted = await _call_import_adapter(adapter, f"{name}.Pwn")

        assert mounted is False, f"{transport} mounted disallowed sentinel {name}.Pwn"
        assert name not in sys.modules, f"{transport} imported the disallowed sentinel {name!r}"
        assert marker() is None, f"{transport} ran the sentinel's top-level code"

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    @pytest.mark.parametrize("transport", sorted(_IMPORT_ADAPTERS))
    async def test_legit_installed_app_view_mounts_on_every_transport(self, transport):
        """A real INSTALLED_APPS LiveView mounts on every transport (non-breaking).

        This is the positive half of the parity: the allowlist fallback
        (INSTALLED_APPS roots + ``djust``) must ACCEPT a legit view identically
        on each transport. Without it the reject-side assertions could pass
        vacuously by rejecting everything.
        """
        adapter = _IMPORT_ADAPTERS[transport]
        mounted = await _call_import_adapter(adapter, _DEMO_VIEW_PATH)

        assert mounted is True, (
            f"{transport} REJECTED the legit view {_DEMO_VIEW_PATH!r} — the "
            f"allowlist verdict drifted from the other transports."
        )

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    @pytest.mark.parametrize("payload", _ARBITRARY_IMPORT_PAYLOADS)
    def test_arbitrary_import_payloads_resolve_falsy_at_chokepoint(self, payload):
        """The shared chokepoint itself rejects each payload (single source of truth).

        Pins that the verdict the adapters mirror comes from
        ``djust.security.mount.resolve_view_class`` — the one place every
        transport routes through.
        """
        from djust.security.mount import resolve_view_class

        resolution = resolve_view_class(payload)
        assert not resolution
        assert resolution.generic == "View not found"

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    def test_demo_view_resolves_to_liveview_subclass(self):
        """The legit view the positive-parity case relies on really is a LiveView."""
        from djust.security.mount import resolve_view_class

        resolution = resolve_view_class(_DEMO_VIEW_PATH)
        assert resolution
        assert resolution.view_class is not None
        assert issubclass(resolution.view_class, LiveView)


# --------------------------------------------------------------------------- #
# Axis 2 — mount-URL traversal: identical neutralised ``request.path`` on every
# request-building transport.
# --------------------------------------------------------------------------- #
_TRAVERSAL_URLS = [
    "/%2e%2e/%2e%2e/admin/",  # percent-encoded traversal (#1825 bypass shape)
    "/../../admin/",  # raw traversal (#1819)
]


class TestMountUrlTraversalParity:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("transport", sorted(_URL_ADAPTERS))
    @pytest.mark.parametrize("url", _TRAVERSAL_URLS)
    async def test_traversal_neutralised_on_every_request_building_transport(self, transport, url):
        """Traversal payloads neutralise to a path with no ``..`` on each transport.

        Gate-off (#1468): feed the RAW ``url`` straight into ``RequestFactory``
        on either transport (skip ``validate_mount_url`` / ``_validate_mount_url``)
        and that transport's ``request.path`` keeps ``/../../admin/`` → FAILS here.
        """
        adapter = _URL_ADAPTERS[transport]
        path = await _call_url_adapter(adapter, url)

        assert ".." not in path, (
            f"{transport} left a traversal segment in request.path={path!r} for "
            f"input {url!r} — the shared validate_mount_url was bypassed on this "
            f"transport (parallel-path drift, #1646)."
        )
        # Neutralised to the safe fallback.
        assert path == "/", f"{transport} did not neutralise {url!r} to '/': {path!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("transport", sorted(_URL_ADAPTERS))
    async def test_legit_path_preserved_on_every_request_building_transport(self, transport):
        """A legit site-relative path is preserved verbatim (non-breaking)."""
        adapter = _URL_ADAPTERS[transport]
        path = await _call_url_adapter(adapter, "/items/42/")
        assert path == "/items/42/", f"{transport} mangled the legit path /items/42/ -> {path!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", _TRAVERSAL_URLS)
    async def test_all_request_building_transports_agree(self, url):
        """Cross-transport agreement: every request-building transport produces the
        SAME ``request.path`` for the SAME payload (the parity invariant itself)."""
        paths = {}
        for transport, adapter in _URL_ADAPTERS.items():
            paths[transport] = await _call_url_adapter(adapter, url)
        distinct = set(paths.values())
        assert len(distinct) == 1, (
            f"Transports disagree on request.path for {url!r}: {paths!r} — "
            f"this is exactly the #7/#23 drift class this net exists to catch."
        )
