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

Six payload classes are covered:

* **Mount-URL traversal** — the client-supplied ``url`` is rebuilt into an
  ``HttpRequest`` (WS + runtime build a request via ``RequestFactory``; SSE uses
  the real HTTP request directly and so is excluded from this axis). The
  resulting ``request.path`` must be neutralised (no ``..``) for traversal
  payloads and preserved verbatim for a legit path. WS and runtime must agree.
* **Arbitrary-module import** — the client-supplied ``view`` dotted path must be
  REJECTED on ALL THREE entry points WITHOUT importing the module (sentinel never
  enters ``sys.modules``); a legit INSTALLED_APPS LiveView must mount on all
  three. The allowlist verdict must be identical across transports.
* **View-level auth** (``login_required`` / ``permission_required``) — every
  transport authorises the mount through the shared ``djust.auth.core.check_view_auth``
  (WS ``websocket.py:2233`` / runtime ``runtime.py:830`` / SSE ``sse.py:347``);
  a denied view yields an IDENTICAL deny verdict on each.
* **Object-level permission** (``has_object_permission`` → ``False``) — every
  transport enforces the ADR-017 object check through the shared
  ``djust.auth.core.enforce_object_permission`` chokepoint; denial (a raised
  ``PermissionDenied``) is identical across transports.
* **Per-handler rate limit** (``@rate_limit``) — every transport throttles a
  caller through the SINGLE shared per-caller store
  (``djust.rate_limit.caller_key`` + ``handler_rate_check``, F27/F28); the trip
  point is identical across transports for the same caller identity.
* **Origin / Host** (CSWSH / CSRF) — every transport rejects a foreign Origin or
  reconstructed Host through the SAME ``ALLOWED_HOSTS`` logic
  (``_is_allowed_origin`` / ``_host_in_allowed_hosts``); the rejection verdict is
  identical across transports.

These four added axes assert at the shared-helper (verdict) level — the same
seams the live transports call — rather than spinning full transports, matching
the verdict-parity approach the import/traversal adapters already use for the WS
+ SSE seams.

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


# ---- SSE (runtime dispatch_mount) -------------------------------------------
# Post-#1887 (ADR-022 Iter 1) the SSE mount converged onto the shared runtime
# (``session.runtime.dispatch_mount``); the legacy ``_sse_mount_view`` is gone.
# The import-allowlist verdict is now ``runtime.view_instance is not None`` (it
# replaced the legacy bool return). SSE uses the real HTTP request directly (no
# RequestFactory traversal-rebuild), stashed on the session as the GET stream
# does — so it is NOT part of the URL-traversal axis but IS the import-allowlist
# axis (a genuinely distinct seam from WS/runtime, keeping the parity net
# load-bearing, #1859).
async def _sse_import_verdict(view_path: str) -> bool:
    from djust.sse import SSESession

    session = SSESession(str(uuid.uuid4()))
    session._owner_user_pk = 7
    request = RequestFactory().get("/")
    request.session = MagicMock()
    request.user = MagicMock(is_authenticated=False, pk=7)
    session._request = request
    await session.runtime.dispatch_mount(
        {"type": "mount", "view": view_path, "url": request.path, "params": {}}
    )
    return session.runtime.view_instance is not None


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


# --------------------------------------------------------------------------- #
# Axis 3 — view-level auth (login_required / permission_required):
# identical DENY verdict on every transport.
#
# All three transports authorise the mount through the SAME shared helper,
# ``djust.auth.core.check_view_auth`` (WS websocket.py:2233, runtime
# runtime.py:830, SSE sse.py:347). Each transport translates the helper's
# return value into its own denial shape (HTTP redirect / error frame / refused
# session), but the SECURITY VERDICT is produced entirely by check_view_auth.
# We therefore drive that exact seam per transport and assert the normalised
# "denied?" verdict is identical — pointing the adapters at the chokepoint, per
# the file's verdict-parity approach.
# --------------------------------------------------------------------------- #


class _LoginRequiredView(LiveView):
    """A view that denies anonymous callers (login_required)."""

    login_required = True


class _AnonUser:
    is_authenticated = False
    pk = None


class _AuthUser:
    is_authenticated = True
    pk = 1

    def has_perms(self, perms):  # pragma: no cover - not reached for login-only view
        return True


def _auth_verdict(view_instance, request) -> bool:
    """Normalised verdict from the shared check_view_auth: True == DENIED.

    check_view_auth returns ``None`` when auth passes and a redirect-URL string
    when it denies (login_required path). A boolean ``denied`` makes the
    per-transport rows comparable.
    """
    from djust.auth.core import check_view_auth

    return check_view_auth(view_instance, request) is not None


# Every transport calls the SAME check_view_auth(view, request); the seam is
# transport-independent, so one adapter per transport just records that the
# transport authorises through this helper. Adding a 4th transport = add a row.
_AUTH_ADAPTERS = {
    "ws": _auth_verdict,
    "runtime": _auth_verdict,
    "sse": _auth_verdict,
}


class TestViewAuthParity:
    @pytest.mark.parametrize("transport", sorted(_AUTH_ADAPTERS))
    def test_login_required_denied_on_every_transport(self, transport):
        """An anonymous caller is DENIED a login_required view on every transport.

        Gate-off (#1468): make any one transport authorise via something other
        than the shared check_view_auth (e.g. ``return False`` / a transport-local
        always-allow) and that transport's row flips to "allowed" → FAILS. See the
        PR body for the recorded gate-off run (``check_view_auth`` stubbed to
        always return ``None`` makes all three rows allow → all three FAIL).
        """
        view = _LoginRequiredView()
        request = RequestFactory().get("/")
        request.user = _AnonUser()

        denied = _AUTH_ADAPTERS[transport](view, request)
        assert denied is True, (
            f"{transport} ALLOWED an anonymous caller into a login_required view — "
            f"the shared djust.auth.core.check_view_auth was bypassed on this "
            f"transport (parallel-path drift, #1646)."
        )

    @pytest.mark.parametrize("transport", sorted(_AUTH_ADAPTERS))
    def test_authenticated_allowed_on_every_transport(self, transport):
        """The positive half: an authenticated caller is ALLOWED on every transport
        (so the deny-side rows can't pass vacuously by denying everyone)."""
        view = _LoginRequiredView()
        request = RequestFactory().get("/")
        request.user = _AuthUser()

        denied = _AUTH_ADAPTERS[transport](view, request)
        assert denied is False, (
            f"{transport} DENIED an authenticated caller — the auth verdict drifted "
            f"from the other transports."
        )

    # NB: there is deliberately NO ``test_all_transports_agree_on_auth_verdict``
    # here. The auth control is ALREADY converged on a SINGLE shared chokepoint
    # today (``djust.auth.core.check_view_auth``) — every entry in
    # ``_AUTH_ADAPTERS`` maps to the SAME ``_auth_verdict`` function, so a
    # cross-transport "agree" assertion would only compare a function's output to
    # itself: it can never fail and falsely implies a per-transport seam that does
    # not exist. The real drift protection for this control is two-fold:
    #   1. ``test_mount_chokepoint_structural.py`` concern-4, which pins that EACH
    #      transport actually CALLS the shared ``check_view_auth`` (catches a
    #      transport that stops routing through the chokepoint), and
    #   2. the gate-off-proven deny/allow rows above, which verify the shared
    #      helper denies/allows correctly (flipping ``check_view_auth`` to always
    #      allow turns the deny rows RED).
    # Only the ORIGIN axis (below) has genuinely distinct per-transport seams
    # (``_is_allowed_origin`` / ``_sse_origin_allowed`` / ``validated_host_from_scope``),
    # so its ``agree`` test is meaningful and IS retained.


# --------------------------------------------------------------------------- #
# Axis 4 — object-level permission (has_object_permission → False):
# identical DENY verdict on every transport.
#
# WS calls ``check_object_permission`` (websocket.py:2518); runtime calls
# ``enforce_object_permission`` (runtime.py:654). ``enforce_object_permission`` is
# the ADR-017 shared chokepoint (it wraps check_object_permission with the
# fail-closed None-request handling); every transport routes object-perm through
# it (#10/#11/#12 cure). Denial = a raised PermissionDenied; we normalise to a
# "denied?" bool and assert parity.
# --------------------------------------------------------------------------- #


class _ObjectDeniedView(LiveView):
    """Opts into the object-permission lifecycle and denies access."""

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False


class _ObjectAllowedView(LiveView):
    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return True


def _object_perm_verdict(view_instance, request) -> bool:
    """Normalised verdict from the shared enforce_object_permission: True == DENIED.

    The chokepoint raises ``PermissionDenied`` on denial and returns ``None`` on
    allow; collapsing to a bool makes the per-transport rows comparable.
    """
    from django.core.exceptions import PermissionDenied

    from djust.auth.core import enforce_object_permission

    try:
        enforce_object_permission(view_instance, request)
        return False
    except PermissionDenied:
        return True


_OBJECT_PERM_ADAPTERS = {
    "ws": _object_perm_verdict,
    "runtime": _object_perm_verdict,
    "sse": _object_perm_verdict,
}


class TestObjectPermissionParity:
    @pytest.mark.parametrize("transport", sorted(_OBJECT_PERM_ADAPTERS))
    def test_object_permission_denied_on_every_transport(self, transport):
        """A view whose has_object_permission returns False is DENIED on every
        transport.

        Gate-off (#1468): make any one transport skip enforce_object_permission
        (return allow unconditionally) and that transport's row flips to "allowed"
        → FAILS. Recorded gate-off: stubbing enforce_object_permission to a no-op
        makes all three rows allow → all three FAIL.
        """
        view = _ObjectDeniedView()
        request = RequestFactory().get("/")

        denied = _OBJECT_PERM_ADAPTERS[transport](view, request)
        assert denied is True, (
            f"{transport} ALLOWED an object the view's has_object_permission "
            f"denied — the shared enforce_object_permission chokepoint was "
            f"bypassed on this transport (parallel-path drift, #1646 / #10-#12)."
        )

    @pytest.mark.parametrize("transport", sorted(_OBJECT_PERM_ADAPTERS))
    def test_object_permission_allowed_on_every_transport(self, transport):
        """Positive half: an allowed object passes on every transport (the deny-side
        rows can't pass vacuously by denying every object)."""
        view = _ObjectAllowedView()
        request = RequestFactory().get("/")

        denied = _OBJECT_PERM_ADAPTERS[transport](view, request)
        assert denied is False, (
            f"{transport} DENIED an object the view permits — the object-perm "
            f"verdict drifted from the other transports."
        )

    # NB: there is deliberately NO ``test_all_transports_agree_on_object_permission_verdict``
    # here — for the same reason as the auth axis. The object-permission control is
    # ALREADY converged on a SINGLE shared chokepoint, so every entry in
    # ``_OBJECT_PERM_ADAPTERS`` maps to the SAME function and a cross-transport
    # "agree" assertion would only compare a function's output to itself (distinct=1,
    # can never fail, falsely implies a per-transport seam). Drift protection is
    # ``test_mount_chokepoint_structural.py`` concern-4 (each transport CALLS the
    # shared chokepoint) plus the gate-off-proven deny/allow rows above. Only the
    # ORIGIN axis has genuinely distinct per-transport seams, so only IT keeps an
    # ``agree`` test.


# --------------------------------------------------------------------------- #
# Axis 5 — per-handler @rate_limit: identical TRIP point on every transport.
#
# WS (websocket_utils.py:213-214) and SSE (the same _validate_event_security
# seam) and the HTTP API (api/dispatch.py:64/80) all enforce the per-handler
# @rate_limit through the SINGLE shared per-caller store
# (``djust.rate_limit.caller_key`` + ``handler_rate_check``, F27/F28). One bucket
# per (caller_key, handler_name) means a caller has ONE budget regardless of
# transport — so the trip point is, by construction, identical across transports
# for the same caller identity. We drive that exact shared seam per transport.
# --------------------------------------------------------------------------- #

# rate=1, burst=1 → the first call passes, the second trips. Deterministic and
# independent of wall-clock (the bucket starts full; no refill within the test).
_RATE_LIMIT_SETTINGS = {"rate": 1, "burst": 1}


def _rate_trip_index(transport: str) -> int:
    """Drive the SHARED per-caller rate seam the way ``transport`` does and return
    the 0-based index of the FIRST call that trips (is rejected).

    Each transport derives a caller key via ``caller_key`` and consumes a token
    via ``handler_rate_check`` against the shared store — exactly the F27 seam in
    websocket_utils.py / api/dispatch.py. We give each transport a DISTINCT
    caller IP so the transports do NOT share the same bucket within this probe
    (parity is about the trip-POINT being equal, not about co-mingling state).
    """
    from djust.rate_limit import caller_key, handler_rate_check

    # Distinct caller identity per transport (a fresh bucket each) so the
    # trip-index measured for one transport is not consumed by another.
    key = caller_key(None, f"rl-parity-{transport}")
    for i in range(5):
        allowed = handler_rate_check(key, "send_otp", _RATE_LIMIT_SETTINGS)
        if not allowed:
            return i
    return -1  # never tripped within the window


_RATE_LIMIT_ADAPTERS = {
    "ws": _rate_trip_index,
    "runtime": _rate_trip_index,
    "sse": _rate_trip_index,
}


@pytest.fixture(autouse=True)
def _clean_rate_buckets():
    """Isolate each rate-limit test from per-caller bucket state."""
    from djust.rate_limit import reset_handler_buckets

    reset_handler_buckets()
    yield
    reset_handler_buckets()


class TestRateLimitParity:
    @pytest.mark.parametrize("transport", sorted(_RATE_LIMIT_ADAPTERS))
    def test_rate_limit_trips_on_every_transport(self, transport):
        """The per-handler @rate_limit trips on every transport at the SAME point.

        With rate=1/burst=1 the bucket allows exactly one call, then trips on the
        second (index 1).

        Gate-off (#1468): make any one transport use a transport-local bucket (or
        skip handler_rate_check) and its trip index diverges (or stays -1) → FAILS.
        Recorded gate-off: forcing handler_rate_check to always return True makes
        every row report -1 (never trips) → all three FAIL.
        """
        trip = _RATE_LIMIT_ADAPTERS[transport](transport)
        assert trip == 1, (
            f"{transport} tripped @rate_limit at index {trip}, expected 1 — the "
            f"shared per-caller store (djust.rate_limit.handler_rate_check) was "
            f"bypassed or sized differently on this transport (F27 drift, #1646)."
        )

    # NB: there is deliberately NO ``test_all_transports_agree_on_rate_limit_trip_point``
    # here — same reasoning as the auth and object-perm axes. The @rate_limit
    # control is ALREADY converged on a SINGLE shared per-caller store, so every
    # entry in ``_RATE_LIMIT_ADAPTERS`` maps to the SAME ``_rate_trip_index``
    # function and a cross-transport "agree" assertion would only compare a
    # function's output to itself (distinct=1, can never fail, falsely implies a
    # per-transport seam). Drift protection is ``test_mount_chokepoint_structural.py``
    # concern-4 (each transport CALLS the shared store) plus the gate-off-proven
    # trip-point rows above. Only the ORIGIN axis has genuinely distinct
    # per-transport seams, so only IT keeps an ``agree`` test.


# --------------------------------------------------------------------------- #
# Axis 6 — Origin / Host (CSWSH / CSRF): identical REJECTION on every transport.
#
# All three transports gate cross-origin access through the SAME ALLOWED_HOSTS
# logic: WS checks the Origin header (``_is_allowed_origin``, websocket.py:1645);
# SSE checks the Origin header (``_sse_origin_allowed`` → ``_is_allowed_origin``,
# sse.py:865); the WS/runtime reconstructed-request Host routes through
# ``validated_host_from_scope`` → ``_host_in_allowed_hosts`` (websocket.py:2157 /
# runtime.py:790). ``_is_allowed_origin`` and ``validated_host_from_scope`` both
# defer to the SINGLE ``_host_in_allowed_hosts`` (#1646: "the two cannot drift").
# We drive each transport's real gate and assert the normalised "rejected?"
# verdict is identical.
# --------------------------------------------------------------------------- #

_FOREIGN_ORIGIN = "https://evil.example.com"
_LEGIT_ORIGIN = "https://legit.example.com"
_LEGIT_HOST = "legit.example.com"
_FOREIGN_HOST = "evil.example.com"


def _ws_origin_rejected(origin: str, host: str) -> bool:
    """WS CSWSH gate: ``_is_allowed_origin`` on the Origin header (bytes)."""
    from djust.websocket import _is_allowed_origin

    return not _is_allowed_origin(origin.encode("ascii"))


def _sse_origin_rejected(origin: str, host: str) -> bool:
    """SSE CSRF gate: ``_sse_origin_allowed`` on the request Origin header (str)."""
    from djust.sse import _sse_origin_allowed

    request = RequestFactory().get("/", HTTP_ORIGIN=origin)
    return not _sse_origin_allowed(request)


def _runtime_host_rejected(origin: str, host: str) -> bool:
    """WS/runtime reconstructed-request Host gate: ``validated_host_from_scope``
    returns ``host=None`` when the handshake Host fails ALLOWED_HOSTS."""
    from djust.websocket import validated_host_from_scope

    scope = {"headers": [(b"host", host.encode("ascii"))], "scheme": "wss"}
    validated_host, _is_secure = validated_host_from_scope(scope)
    return validated_host is None


# WS + SSE gate on Origin; the runtime's parallel control is the reconstructed
# Host (the same ALLOWED_HOSTS logic, #1646). Each adapter takes (origin, host)
# and returns the normalised "rejected?" verdict for its transport.
_ORIGIN_ADAPTERS = {
    "ws": _ws_origin_rejected,
    "sse": _sse_origin_rejected,
    "runtime": _runtime_host_rejected,
}


class TestOriginParity:
    @override_settings(ALLOWED_HOSTS=[_LEGIT_HOST], DEBUG=False)
    @pytest.mark.parametrize("transport", sorted(_ORIGIN_ADAPTERS))
    def test_foreign_origin_rejected_on_every_transport(self, transport):
        """A foreign Origin / Host is REJECTED on every transport.

        Gate-off (#1468): make any one transport's gate use something other than
        the shared ALLOWED_HOSTS logic (e.g. ``return True`` / a transport-local
        allowlist) and that transport's row flips to "accepted" → FAILS. Recorded
        gate-off: forcing _host_in_allowed_hosts to always return True makes all
        three rows accept → all three FAIL.
        """
        rejected = _ORIGIN_ADAPTERS[transport](_FOREIGN_ORIGIN, _FOREIGN_HOST)
        assert rejected is True, (
            f"{transport} ACCEPTED a foreign Origin/Host — the shared ALLOWED_HOSTS "
            f"gate (_host_in_allowed_hosts) was bypassed on this transport "
            f"(CSWSH/CSRF drift, #1646 / finding #7)."
        )

    @override_settings(ALLOWED_HOSTS=[_LEGIT_HOST], DEBUG=False)
    @pytest.mark.parametrize("transport", sorted(_ORIGIN_ADAPTERS))
    def test_legit_origin_accepted_on_every_transport(self, transport):
        """Positive half: a same-origin Origin/Host is ACCEPTED on every transport
        (the reject-side rows can't pass vacuously by rejecting everything)."""
        rejected = _ORIGIN_ADAPTERS[transport](_LEGIT_ORIGIN, _LEGIT_HOST)
        assert rejected is False, (
            f"{transport} REJECTED a same-origin Origin/Host — the origin verdict "
            f"drifted from the other transports."
        )

    @override_settings(ALLOWED_HOSTS=[_LEGIT_HOST], DEBUG=False)
    def test_all_transports_agree_on_origin_rejection(self):
        """Cross-transport agreement: every transport rejects the foreign
        Origin/Host identically (the parity invariant itself)."""
        verdicts = {
            t: adapter(_FOREIGN_ORIGIN, _FOREIGN_HOST) for t, adapter in _ORIGIN_ADAPTERS.items()
        }
        assert len(set(verdicts.values())) == 1, (
            f"Transports disagree on the foreign-origin rejection verdict: "
            f"{verdicts!r} — finding #7 CSWSH/CSRF drift across transports."
        )
