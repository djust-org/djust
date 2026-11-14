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
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeGuard
from urllib.parse import quote
from uuid import UUID

from django.contrib.sessions.backends.base import SessionBase
from django.contrib.sessions.backends.cache import SessionStore as CacheSession
from django.contrib.sessions.backends.cached_db import SessionStore as CachedDBSession
from django.contrib.sessions.backends.db import SessionStore as DBSession
from django.contrib.sessions.backends.file import SessionStore as FileSession

from ._exposure import (
    ExposureConfigurationError,
    ExposureContract,
    ExposureError,
    StateLimitError,
    StateLimits,
    clone_json_state,
)

_SERVER_SESSION_TYPES = (DBSession, CachedDBSession, CacheSession, FileSession)

#: The ``SESSION_ENGINE`` values whose store can hold ``persist="server"``
#: state: exactly the store classes in ``_SERVER_SESSION_TYPES`` (#4092).
SUPPORTED_SESSION_ENGINES = tuple(cls.__module__ for cls in _SERVER_SESSION_TYPES)


class UnsupportedSessionEngine(ExposureConfigurationError, ExposureError):
    """Declared server state with a session store that cannot hold it (#4092).

    A configuration error, not a payload error: the message is
    framework-authored and names no view value, so the protected HTTP entry
    lets it reach the developer (``ExposureConfigurationError``) instead of a
    bare 500. Still an :class:`ExposureError` for the callers that refuse on
    one. System check ``djust.C030`` reports the same combination at startup.
    """


def session_store_supported(store_class: Any) -> bool:
    """Whether a session store class can hold server state: exactly one of the
    audited stores. A subclass or a custom store needs a reviewed adapter.

    The one predicate for the runtime (the request's session) and system check
    ``djust.C030`` (the configured engine), so the two cannot disagree.
    """
    return any(store_class is supported for supported in _SERVER_SESSION_TYPES)


def session_engine_store(engine: Any) -> Any:
    """The ``SessionStore`` class ``SESSION_ENGINE`` resolves to, as Django
    resolves it; ``None`` when it does not import."""
    from importlib import import_module

    if not isinstance(engine, str) or not engine:
        return None
    try:
        return getattr(import_module(engine), "SessionStore", None)
    except Exception:  # noqa: BLE001 — Django reports a broken engine itself
        logger.debug("djust: SESSION_ENGINE does not import; not checked")
        return None


def unsupported_session_message(session: Any) -> str:
    """The configuration error for a session store that cannot hold server state."""
    if session is None:
        found = "no session (is SessionMiddleware installed?)"
    else:
        found = "%s.%s" % (
            getattr(type(session), "__module__", "?"),
            getattr(type(session), "__qualname__", "?"),
        )
    return (
        'State declared with persist="server" is kept in a server-side session, '
        "and this request's session cannot hold it: %s. Set SESSION_ENGINE to one "
        'of %s, or declare the state without persist="server".'
        % (found, ", ".join(SUPPORTED_SESSION_ENGINES))
    )


# Envelope format 2 records the application contract version (E2-9). Format-1
# envelopes are unindexed and are rejected, so the view remounts (D-j).
_VERSION = 2
_DEFAULT_MAX_AGE = 3600

logger = logging.getLogger(__name__)

StateMigration = Callable[[int, dict[str, Any]], Any]


def server_state_max_age() -> int:
    """Return ``DJUST_SERVER_STATE_MAX_AGE``; an invalid value fails closed.

    System check ``djust.C020`` reports the same misconfiguration at startup.
    """
    from django.conf import settings

    value = getattr(settings, "DJUST_SERVER_STATE_MAX_AGE", _DEFAULT_MAX_AGE)
    if type(value) is not int or not 0 < value <= 86400:
        raise ExposureError("DJUST_SERVER_STATE_MAX_AGE must be an integer from 1 to 86400")
    return value


#: Upper bound for ``DJUST_EXPLICIT_STATE_SAVE_TIMEOUT``. An explicit turn waits
#: this long for its save before answering, so a larger value is a stall.
MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S = 10.0


def valid_explicit_state_save_timeout(value: Any) -> "TypeGuard[int | float]":
    """True for a finite number of seconds in ``(0, 10]`` (bools rejected)."""
    import math

    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 < value <= MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S
    )


def _migration_hook(view: Any) -> StateMigration | None:
    """Bind an opt-in ``migrate_state`` defined on the view class, if any."""
    if not callable(getattr(type(view), "migrate_state", None)):
        return None
    hook: StateMigration = view.migrate_state
    return hook


def _identity(value: Any) -> str:
    """Encode an approved identifier without invoking a model/service's str()."""
    if type(value) is str and value and len(value) <= 1024:
        return "str:" + value
    if type(value) is int and -(2**63) <= value < 2**63:
        return "int:" + str(value)
    if type(value) is UUID:
        return "uuid:" + str(value)
    raise ExposureError("Unsupported request identity type")


def _request_principal(request: Any) -> tuple[str, str]:
    """Derive principal from middleware state, never event/restore data.

    AuthenticationMiddleware is required even for anonymous views. TenantInfo
    is djust's resolver result; model-based tenant middleware is also supported
    via its primary key. Configured tenant resolution may not silently disappear.
    Session identity is checked separately by the consuming boundary.
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
            # Tenancy configured without TenantMiddleware (views resolving
            # their own tenant through TenantMixin) is supported: resolve it
            # here with the configured resolver, as the middleware would. A
            # resolver failure stays a refusal, never a silent "no tenant".
            try:
                from .tenants.resolvers import get_tenant_resolver

                resolved = get_tenant_resolver().resolve(request)
            except Exception:
                raise ExposureError("Configured tenant resolution failed") from None
            # Required tenancy that resolves to nothing is still missing, as
            # TenantMiddleware treats it (its 404); never "no tenant".
            tenants = getattr(settings, "DJUST_TENANTS", None) or {}
            config = getattr(settings, "DJUST_CONFIG", None) or {}
            if resolved is None and (
                tenants.get("REQUIRED", False) or config.get("TENANT_REQUIRED", False)
            ):
                raise ExposureError("Configured tenant resolution is missing from the request")
            request.tenant = resolved
            return _request_principal(request)
        tenant_id = "none"
    elif isinstance(tenant, TenantInfo):
        tenant_id = "tenant:" + _identity(tenant.id)
    elif isinstance(tenant, Model):
        tenant_id = "model:" + tenant._meta.label_lower + ":" + _identity(tenant.pk)
    else:
        raise ExposureError("Unsupported request tenant identity")
    return user_id, tenant_id


def request_binding(request: Any) -> "StateBinding":
    """Bind an established server session to the current principal and route.

    A view mounted beside the page view (#3252) shares the page's route and
    session, so its request carries the view's ``_djust_slot_target`` and the
    binding names it: the stored state, the client snapshot and the event
    authorization of one view never match a sibling's, even of the same class.
    The page view's binding is unchanged.
    """
    user_id, tenant_id = _request_principal(request)
    route = request.path
    slot = getattr(request, "_djust_slot_target", None)
    if type(slot) is str and slot:
        # A page route always starts with "/", so the slot form (which starts
        # with "slot:") cannot equal any page's, whatever characters its path holds.
        route = f"slot:{quote(slot, safe='')}:{route}"
    return StateBinding(request.session.session_key, user_id, tenant_id, route)


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
    if session is None or not session_store_supported(type(session)):
        raise UnsupportedSessionEngine(unsupported_session_message(session))
    if not session.session_key:
        if not create:
            return None
        session.create()
    return ServerStateSession(
        session,
        contract,
        request_binding(request),
        max_age=server_state_max_age(),
        migrate=_migration_hook(view),
    )


class StateLimitExceeded(ExposureError):
    """A save refused because the declared server state is over its limits (#4095).

    Raised before anything is written: the stored state is the last one that
    fit. ``field`` is the declared field that does not fit (alone, or the
    largest one when only the fields together are over); ``limit`` is
    ``"bytes"`` or ``"structure"``. The message names the field and the limit,
    never a value, so a transport can show it to the user.
    """

    def __init__(self, field: str, limit: str, limits: StateLimits) -> None:
        self.field = field
        self.limit = limit
        if limit == "bytes":
            bound = "the %d-byte limit for persisted state" % limits.max_bytes
        else:
            bound = "the persisted-state limit of %d values and nesting depth %d" % (
                limits.max_nodes,
                limits.max_depth,
            )
        # Built from the field name and the limit only, never from a value, so a
        # transport can show it to the user. Transports send ``user_message``,
        # not ``str(exception)``.
        self.user_message = "The change was not saved: state field '%s' would exceed %s." % (
            field,
            bound,
        )
        super().__init__(self.user_message)


def _limit_culprit(contract: ExposureContract, view: Any) -> str:
    """Which declared server field makes the projection go over its limits.

    The first field that is over the limits on its own; else (only the
    fields together are over) the one with the largest encoded size.
    """
    largest, largest_size = "", -1
    for name, policy in contract.fields.items():
        if not policy.permits("server"):
            continue
        value = getattr(view, name, None)
        try:
            clone_json_state({name: value}, limits=contract.limits)
        except StateLimitError:
            return name
        except ExposureError:
            continue
        try:
            size = len(json.dumps(value, ensure_ascii=False))
        except (TypeError, ValueError):
            continue
        if size > largest_size:
            largest, largest_size = name, size
    return largest


def _project_for_save(adapter: "ServerStateSession", view: Any) -> dict[str, Any]:
    try:
        return adapter.contract.project_view(view, "server")
    except StateLimitError as error:
        field = _limit_culprit(adapter.contract, view)
        raise StateLimitExceeded(field, error.limit, adapter.contract.limits) from None


def _declared_adapter(
    view: Any, request: Any, *, create: bool = False
) -> "ServerStateSession | None":
    """The strict adapter for declared ``persist="server"`` fields.

    A legacy view has no declared server state (the constructor refuses the
    grant), so it has no adapter; its state is the automatic kind only (#4107).
    """
    from ._exposure import uses_legacy_exposure

    if uses_legacy_exposure(view):
        return None
    return server_state_adapter(view, request, create=create)


def _flush(session: Any, expected_key: Any) -> None:
    from ._session_merge import save_merged

    save_merged(session, expected_key)


def save_server_state(view: Any, request: Any) -> None:
    """Save the view's server-side state after the transport's authorization.

    Two parts, one session write (#4107):

    * declared ``persist="server"`` fields, strictly: a value the declared
      codec refuses is an error;
    * the rest of the view's state (``_automatic_state``): its public
      attributes and the private ones ``mount()`` created, as 1.3 saved them.
      Not written when it equals what is stored.

    Either part over its size limit raises :class:`StateLimitExceeded` before
    anything is written: state is never saved in part. Called after the page
    GET's ``mount()`` too, where it overwrites what an earlier page load saved.
    """
    from . import _automatic_state

    adapter = _declared_adapter(view, request, create=True)
    # Projected first: a refusal must leave the session dict untouched.
    values = _project_for_save(adapter, view) if adapter is not None else None
    modified_before = getattr(getattr(request, "session", None), "modified", None)
    undo = _automatic_state.stage(view, request)
    if adapter is not None and values is not None:
        try:
            adapter.save(values)  # flushes the automatic envelope with it
        except StateLimitError as error:
            # The envelope's own overhead tipped it over: the fields are the cause.
            if undo is not None:
                undo()
            field = _limit_culprit(adapter.contract, view)
            raise StateLimitExceeded(field, error.limit, adapter.contract.limits) from None
    elif undo is not None:
        session = request.session
        try:
            _flush(session, session.session_key)
        except BaseException:
            # Never leave a staged envelope behind a write that failed: the
            # session middleware would try the same write again at the response.
            undo()
            if modified_before is not None:
                session.modified = modified_before
            raise


def reapply_saved_state(view: Any) -> None:
    """Put the view's restored state back over what ``handle_params()`` just
    set, on a socket mount (#4107), when the URL is the one it was saved under:
    declared ``persist="server"`` fields and the rest alike. At another URL
    ``handle_params()`` has the last word for what it assigns (#3624).
    """
    from . import _automatic_state

    _automatic_state.finish_mount(view)


class SavedView:
    """What the store holds for a view, read BEFORE ``mount()`` (the HTTP
    fallback): the values to put back, and whether ``mount()`` must run first.

    ``declared`` holds the strict ``persist="server"`` fields, ``public`` /
    ``private`` / ``components`` the rest of the view's state. ``must_mount``
    is 1.3's rule: nothing saved, or something of the view could not be saved
    (an unsaveable attribute, a component, work ``mount()`` queued that has not
    run), so only ``mount()`` can supply it.
    """

    __slots__ = ("declared", "public", "private", "components", "must_mount", "found")

    def __init__(
        self,
        declared: dict[str, Any],
        public: dict[str, Any],
        private: dict[str, Any],
        must_mount: bool,
        components: dict[str, Any] | None = None,
    ) -> None:
        self.declared = declared
        self.public = public
        self.private = private
        self.components = components or {}
        self.must_mount = must_mount
        self.found = bool(declared or public or private or self.components) or not must_mount


def load_saved_view(view: Any, request: Any) -> SavedView:
    """Read a view's saved state before deciding whether to run ``mount()``."""
    from . import _automatic_state

    stored = _automatic_state.saved(view, request)
    declared: dict[str, Any] | None = None
    adapter = _declared_adapter(view, request)
    if adapter is not None:
        try:
            declared = adapter.load()
        except ExposureError:
            declared = None
    if stored is None:
        # Nothing of the view's own state is stored: mount() builds it. Declared
        # fields, if any, still go back over it.
        return SavedView(declared or {}, {}, {}, True)
    return SavedView(
        declared or {}, stored.public, stored.private, stored.remount, stored.components
    )


def load_server_state(view: Any, request: Any) -> dict[str, Any] | None:
    """Validated stored state to put back after ``mount()``, or None.

    Declared ``persist="server"`` fields and the automatic state (#4107), in
    one dictionary: every restore site applies it the same way. The two name
    sets are disjoint (a field that states ``persist`` is never automatic).

    Invalid configuration and storage failures still propagate. Only a rejected
    envelope (schema/identity/expiry) becomes a cache miss. Never hydrate a legacy
    session dictionary or log rejected values. Caller performs fresh object auth
    before dispatch/render after applying these values.
    """
    from . import _automatic_state

    restored: dict[str, Any] | None = None
    adapter = _declared_adapter(view, request)
    if adapter is not None:
        try:
            restored = adapter.load()
        except ExposureError:
            restored = None
    declared_restored = restored is not None
    # Assigned again after handle_params() when the URL is the one they were
    # saved under (``finish_mount``): one rule for declared and undeclared state.
    view.__dict__["_djust_auto_reapply_declared"] = dict(restored) if restored else None
    # Also puts the saved private attributes back on the view.
    automatic = _automatic_state.load(view, request)
    if automatic is not None:
        restored = {**automatic, **(restored or {})}
    # For the socket mount (#1977): did the restore change what mount() built?
    view.__dict__["_djust_restore_changed_view"] = declared_restored or not view.__dict__.get(
        "_djust_auto_restore_same", False
    )
    return restored


async def asave_server_state(view: Any, request: Any) -> None:
    """Async transport save; the work runs in the Django thread."""
    from asgiref.sync import sync_to_async

    await sync_to_async(save_server_state)(view, request)


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
        max_age: int = _DEFAULT_MAX_AGE,
        migrate: StateMigration | None = None,
    ) -> None:
        # Exact type checks deliberately reject cookie subclasses and custom
        # implementations masquerading as a known server backend.
        if type(session) not in _SERVER_SESSION_TYPES:
            raise ExposureError("Explicit persistence requires a supported server-side session")
        if type(binding) is not StateBinding or type(contract) is not ExposureContract:
            raise ExposureError("Invalid server state contract or binding")
        if type(max_age) is not int or not 0 < max_age <= 86400:
            raise ExposureError("Invalid server state lifetime")
        if migrate is not None and not callable(migrate):
            raise ExposureError("Invalid server state migration hook")
        self.session = session
        self.contract = contract
        self.binding = binding
        self.max_age = max_age
        self.migrate = migrate
        self.key = self._storage_key()
        self._check_session()

    def _storage_key(self) -> str:
        """The session key this envelope is stored under."""
        # Use owner rather than schema here: a schema change must encounter and
        # reject old data rather than leaving one unreachable entry per deploy.
        identity = json.dumps(
            [self.contract.owner, self.binding.view], separators=(",", ":")
        ).encode()
        return "_djust_explicit_" + hashlib.sha256(identity).hexdigest()

    def _check_session(self) -> None:
        if self.session.session_key != self.binding.session:
            raise ExposureError("State binding does not match the current session")

    def _capture(self, values: dict[str, Any]) -> dict[str, Any]:
        self._check_session()
        created = int(time.time())
        envelope = {
            "version": _VERSION,
            "schema_version": self.contract.version,
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
        expected = {"version", "schema_version", "binding", "created", "expires", "state"}
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
        stored_version = envelope["schema_version"]
        if type(stored_version) is not int or not 0 < stored_version <= 2**31 - 1:
            raise ExposureError("Invalid server state contract version")
        if stored_version != self.contract.version:
            return self._migrate(stored_version, envelope["state"])
        return self.contract.prepare_restore(envelope["state"], "server")

    def _migrate(self, stored_version: int, state: Any) -> dict[str, Any]:
        """Translate an older contract's values through the view's opt-in hook.

        Only an upgrade with a hook is attempted; anything else is rejected so
        the view remounts (D-j). The hook's result is validated exactly like a
        fresh envelope under the current schema, so undeclared or missing keys
        and non-primitive values are still rejected. Neither the hook's
        exception nor any value reaches the log.
        """
        if self.migrate is None or stored_version > self.contract.version:
            raise ExposureError("Server state contract version is not current")
        if (
            type(state) is not dict
            or set(state) != {"schema", "destination", "values"}
            or state["destination"] != "server"
            or type(state["values"]) is not dict
        ):
            raise ExposureError("Invalid server state envelope for migration")
        values = clone_json_state(state["values"], limits=self.contract.limits)
        try:
            migrated = self.migrate(stored_version, values)
        except Exception:  # noqa: BLE001 — hook exceptions may contain stored values
            logger.warning(
                "djust: migrate_state failed for %s (contract version %d -> %d); remounting",
                self.contract.owner,
                stored_version,
                self.contract.version,
            )
            raise ExposureError("Server state migration failed") from None
        try:
            return self.contract.prepare_restore(
                {"schema": self.contract.schema, "destination": "server", "values": migrated},
                "server",
            )
        except ExposureError:
            logger.warning(
                "djust: migrate_state for %s returned state that does not match contract "
                "version %d; remounting",
                self.contract.owner,
                self.contract.version,
            )
            raise

    def save(self, values: dict[str, Any]) -> None:
        """Write and flush to Django's server storage; never silently downgrade."""
        envelope = self._capture(values)
        # Force loading before assignment, then recheck in case load reset a
        # stale session key. Do not bind data to a newly generated replacement.
        self.session.get(self.key)
        self._check_session()
        self.session[self.key] = envelope
        # Merges this envelope (and any other write made through the session)
        # into the stored session (#3347). A pool save that outlived its
        # request first checks the session is still there and still this
        # user's (#3247; a no-op off the save pool).
        from ._session_merge import save_merged

        save_merged(self.session, self.binding.session)

    async def asave(self, values: dict[str, Any]) -> None:
        """Async equivalent for WebSocket dispatch adapters."""
        envelope = self._capture(values)
        await self.session.aget(self.key)
        self._check_session()
        await self.session.aset(self.key, envelope)
        from ._session_merge import asave_merged

        await asave_merged(self.session)

    def load(self) -> dict[str, Any] | None:
        """Return detached validated values; caller applies them after authorization."""
        self._check_session()
        return self._restore(self.session.get(self.key))

    async def aload(self) -> dict[str, Any] | None:
        """Async equivalent; identical schema, identity and expiry validation."""
        self._check_session()
        return self._restore(await self.session.aget(self.key))
