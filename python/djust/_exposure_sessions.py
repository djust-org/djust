"""ADR-038 server persistence, connected to the staged explicit HTTP path.

Only audited, concrete Django server session implementations are supported.
Their opaque session identifier is the browser's handle; state stays in storage.
Custom backends need a reviewed adapter, not a trusted-looking engine name or
inherited capability flag. See Django's session confidentiality contract:
https://docs.djangoproject.com/en/5.2/topics/http/sessions/

Callers must establish current request authorization BEFORE loading and resolve
managed objects through current authorized querysets AFTER validating identities.
This adapter verifies storage and binding, not application permissions. Never
construct a binding from client event parameters. No object hydration occurs here.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.contrib.sessions.backends.base import SessionBase
from django.contrib.sessions.backends.cache import SessionStore as CacheSession
from django.contrib.sessions.backends.cached_db import SessionStore as CachedDBSession
from django.contrib.sessions.backends.db import SessionStore as DBSession
from django.contrib.sessions.backends.file import SessionStore as FileSession

from ._exposure import ExposureContract, ExposureError, clone_json_state

_SERVER_SESSION_TYPES = (DBSession, CachedDBSession, CacheSession, FileSession)
_VERSION = 1


def _identity(value: Any) -> str:
    """Encode an approved identifier without invoking a model/service's str()."""
    if type(value) is str and value and len(value) <= 1024:
        return "str:" + value
    if type(value) is int and -(2**63) <= value < 2**63:
        return "int:" + str(value)
    if type(value) is UUID:
        return "uuid:" + str(value)
    raise ExposureError("Unsupported request identity type")


def request_binding(request: Any) -> "StateBinding":
    """Derive identity from middleware/routing state, never event/restore data.

    AuthenticationMiddleware is required even for anonymous views. TenantInfo
    is djust's resolver result; model-based tenant middleware is also supported
    via its primary key. Configured tenant resolution may not silently disappear.
    A missing session handle must be established by the transport before saving.
    """
    from django.conf import settings
    from django.db.models import Model

    from .tenants.resolvers import TenantInfo

    user = getattr(request, "user", None)
    authenticated = getattr(user, "is_authenticated", None)
    if user is None or type(authenticated) is not bool:
        raise ExposureError("Explicit persistence requires request authentication middleware")
    user_id = "anonymous" if not authenticated else "user:" + _identity(user.pk)
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        configured = bool(getattr(settings, "DJUST_TENANTS", None)) or (
            "TENANT_RESOLVER" in (getattr(settings, "DJUST_CONFIG", None) or {})
        )
        if configured and not hasattr(request, "tenant"):
            raise ExposureError("Configured tenant resolution is missing from the request")
        tenant_id = "none"
    elif isinstance(tenant, TenantInfo):
        tenant_id = "tenant:" + _identity(tenant.id)
    elif isinstance(tenant, Model):
        tenant_id = "model:" + tenant._meta.label_lower + ":" + _identity(tenant.pk)
    else:
        raise ExposureError("Unsupported request tenant identity")
    return StateBinding(request.session.session_key, user_id, tenant_id, request.path)


def server_state_adapter(
    view: Any, request: Any, *, create: bool = False
) -> "ServerStateSession | None":
    """Build the explicit adapter; no server grants means no server persistence."""
    policy = getattr(view, "exposure_policy", None)
    if type(policy) is not str or policy != "explicit":
        raise ExposureError("Explicit server persistence requires the explicit policy")
    contract = ExposureContract.from_view_class(type(view))
    if not any(field.persist == "server" for field in contract.fields.values()):
        return None
    session = getattr(request, "session", None)
    if session is None or type(session) not in _SERVER_SESSION_TYPES:
        raise ExposureError("Explicit persistence requires a supported server-side session")
    if not session.session_key:
        if not create:
            return None
        session.create()
    return ServerStateSession(session, contract, request_binding(request))


def save_server_state(view: Any, request: Any) -> None:
    """Persist only declared server fields after the transport's authorization."""
    adapter = server_state_adapter(view, request, create=True)
    if adapter is not None:
        adapter.save(adapter.contract.project_view(view, "server"))


def load_server_state(view: Any, request: Any) -> dict[str, Any] | None:
    """Validate current server state or request a fresh mount for rejected data.

    Invalid configuration and storage failures still propagate. Only a rejected
    envelope (schema/identity/expiry) becomes a cache miss. Never hydrate a legacy
    session dictionary or log rejected values. Caller performs fresh object auth
    before dispatch/render after applying these declared values.
    """
    adapter = server_state_adapter(view, request)
    if adapter is None:
        return None
    try:
        return adapter.load()
    except ExposureError:
        return None


@dataclass(frozen=True)
class StateBinding:
    """Explicit trusted identifiers; anonymous/no-tenant need explicit sentinels.

    ``view`` includes the concrete route/object identity, not just a class name.
    The contract separately binds the class and schema. Identifiers must already
    be canonical strings; arbitrary model/string conversion is forbidden here.
    """

    session: str
    user: str
    tenant: str
    view: str

    def __post_init__(self) -> None:
        for value in (self.session, self.user, self.tenant, self.view):
            if type(value) is not str or not value or len(value) > 2048:
                raise ExposureError("State binding requires bounded nonempty identifiers")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ExposureError("State binding contains an invalid encoding") from None

    @property
    def digest(self) -> str:
        """Identity comparison token, NOT a signature or authorization proof."""
        encoded = json.dumps(
            [self.session, self.user, self.tenant, self.view], separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


class ServerStateSession:
    """Persist a declaration-selected envelope in a supported server session.

    Storage exceptions propagate. There is no cookie/snapshot fallback. Session
    cleanup and backend expiry remain Django's responsibility; envelope TTL is an
    additional restore limit, not a change to the login session's lifetime.
    Cache/file cross-worker availability depends on deployment; confidentiality
    support does not imply shared storage or cache durability.
    """

    def __init__(
        self,
        session: SessionBase,
        contract: ExposureContract,
        binding: StateBinding,
        *,
        max_age: int = 3600,
    ) -> None:
        # Exact type checks deliberately reject cookie subclasses and custom
        # implementations masquerading as a known server backend.
        if type(session) not in _SERVER_SESSION_TYPES:
            raise ExposureError("Explicit persistence requires a supported server-side session")
        if type(binding) is not StateBinding or type(contract) is not ExposureContract:
            raise ExposureError("Invalid server state contract or binding")
        if type(max_age) is not int or not 0 < max_age <= 86400:
            raise ExposureError("Invalid server state lifetime")
        self.session = session
        self.contract = contract
        self.binding = binding
        self.max_age = max_age
        # Use owner rather than schema here: a schema change must encounter and
        # reject old data rather than leaving one unreachable entry per deploy.
        identity = json.dumps([contract.owner, binding.view], separators=(",", ":")).encode()
        self.key = "_djust_explicit_" + hashlib.sha256(identity).hexdigest()
        self._check_session()

    def _check_session(self) -> None:
        if self.session.session_key != self.binding.session:
            raise ExposureError("State binding does not match the current session")

    def _capture(self, values: dict[str, Any]) -> dict[str, Any]:
        self._check_session()
        created = int(time.time())
        envelope = {
            "version": _VERSION,
            "binding": self.binding.digest,
            "created": created,
            "expires": created + self.max_age,
            "state": self.contract.capture(values, "server"),
        }
        # Include envelope overhead in the same wire/storage budget. No encoder
        # fallback is allowed, even if a session has a permissive serializer.
        return dict(clone_json_state(envelope, limits=self.contract.limits))

    def _restore(self, raw: Any) -> dict[str, Any] | None:
        # Lazy session loading can invalidate a deleted/expired session key.
        self._check_session()
        if raw is None:
            return None
        envelope = clone_json_state(raw, limits=self.contract.limits)
        expected = {"version", "binding", "created", "expires", "state"}
        if type(envelope) is not dict or set(envelope) != expected:
            raise ExposureError("Invalid server state envelope fields")
        if type(envelope["version"]) is not int or envelope["version"] != _VERSION:
            raise ExposureError("Unsupported server state envelope version")
        if type(envelope["binding"]) is not str or envelope["binding"] != self.binding.digest:
            raise ExposureError("Server state binding mismatch")
        created, expires = envelope["created"], envelope["expires"]
        now = int(time.time())
        if (
            type(created) is not int
            or type(expires) is not int
            or created < 0
            or created > now
            or not 0 < expires - created <= 86400
        ):
            raise ExposureError("Invalid server state timestamp")
        if now >= min(expires, created + self.max_age):
            raise ExposureError("Server state has expired")
        return self.contract.prepare_restore(envelope["state"], "server")

    def save(self, values: dict[str, Any]) -> None:
        """Write and flush to Django's server storage; never silently downgrade."""
        envelope = self._capture(values)
        # Force loading before assignment, then recheck in case load reset a
        # stale session key. Do not bind data to a newly generated replacement.
        self.session.get(self.key)
        self._check_session()
        self.session[self.key] = envelope
        self.session.save()

    async def asave(self, values: dict[str, Any]) -> None:
        """Async equivalent for WebSocket/actor dispatch adapters."""
        envelope = self._capture(values)
        await self.session.aget(self.key)
        self._check_session()
        await self.session.aset(self.key, envelope)
        await self.session.asave()

    def load(self) -> dict[str, Any] | None:
        """Return detached validated values; caller applies them after authorization."""
        self._check_session()
        return self._restore(self.session.get(self.key))

    async def aload(self) -> dict[str, Any] | None:
        """Async equivalent; identical schema, identity and expiry validation."""
        self._check_session()
        return self._restore(await self.session.aget(self.key))
